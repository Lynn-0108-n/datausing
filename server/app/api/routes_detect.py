"""即时检测与即时导出：文本粘贴 / 文件上传 / 脱敏文件下载 / 密文还原。

这些接口**都不落库**，用于"脱敏前/后左右对照"的现场演示；需要留痕与审计时走
``POST /api/v1/tasks``（后台任务，写 findings + 审计链）。脱敏文件导出同理：原文只在
同一个请求的内存里，用脱敏策略重建后直接下载，服务器不保存原文。
"""

from __future__ import annotations

from datetime import datetime
from pathlib import PurePath

from fastapi import APIRouter, Depends, File, Form, HTTPException, Response, UploadFile, status

from ..core.config import Settings
from ..identify import DetectionPipeline
from ..identify.masking import (
    DEFAULT_STRATEGY,
    ENCRYPTED_PATTERN,
    MASK_STRATEGIES,
    decrypt_encrypted_text,
)
from ..identify.pipeline import DocumentReport
from ..parsers.base import Document, ParseError, ParseUnsupported
from ..parsers.registry import (
    IMAGE_EXTENSIONS,
    parse_bytes,
    parse_csv,
    parse_json,
    parse_sql,
    parse_text,
    parse_text_input,
    supported_formats,
)
from ..tasks.document_export import EXPORT_FORMATS, build_masked_bytes
from ..tasks.export import masked_document
from .deps import build_options, pipeline_dep, read_upload_limited, settings_dep
from .schemas import (
    DetectDecryptIn,
    DetectDecryptOut,
    DetectExportRequest,
    DetectOut,
    DetectRequest,
    MaskStrategiesOut,
    MaskStrategyOut,
    SegmentOut,
    SpanOut,
)

router = APIRouter(tags=["detect"])


def _to_out(report: DocumentReport, document: Document) -> DetectOut:
    return DetectOut(
        content_type=document.content_type,
        segments=report.segments,
        findings=[
            SegmentOut(
                path=finding.path,
                level=finding.level.value,
                score=finding.score,
                types=finding.types,
                hits=finding.hits,
                masked=finding.masked,
                model_chain=finding.model_chain,
                reason=finding.reason,
                degraded=finding.degraded,
                direct=finding.direct,
                spans=[SpanOut(**span) for span in finding.spans],
            )
            for finding in report.findings
        ],
        by_level=report.by_level,
        duration_ms=report.duration_ms,
        degraded=report.degraded,
        masked_document=masked_document(document, report),
    )


@router.post("/detect", response_model=DetectOut, summary="即时检测：粘贴文本")
def detect_text(
    payload: DetectRequest,
    settings: Settings = Depends(settings_dep),
    pipeline: DetectionPipeline = Depends(pipeline_dep),
) -> DetectOut:
    document = parse_text_input(payload.text, source="text")
    options = build_options(
        enable_l2=payload.enable_l2 or settings.enable_l2,
        enable_l3=payload.enable_l3 or settings.enable_l3,
        enable_ner=settings.enable_ner if payload.enable_ner is None else payload.enable_ner,
        enable_keywords=payload.enable_keywords,
        mask_level=payload.mask_level,
        min_level=payload.min_level,
    )
    return _to_out(pipeline.analyze_document(document, options), document)


@router.post("/detect/file", response_model=DetectOut, summary="即时检测：上传文件（txt/json/csv/sql/docx/pdf/xlsx）")
async def detect_file(
    file: UploadFile = File(..., description="待检测文件"),
    enable_l2: bool = False,
    enable_l3: bool = False,
    enable_ner: bool | None = None,
    enable_keywords: bool | None = None,
    mask_level: str | None = None,
    min_level: str = "SAFE",
    settings: Settings = Depends(settings_dep),
    pipeline: DetectionPipeline = Depends(pipeline_dep),
) -> DetectOut:
    data = await read_upload_limited(file, settings.max_upload_mb * 1024 * 1024)
    try:
        document = parse_bytes(file.filename or "", data)
    except ParseUnsupported as exc:
        raise HTTPException(status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, detail=str(exc)) from exc
    except ParseError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc

    options = build_options(
        enable_l2=enable_l2 or settings.enable_l2,
        enable_l3=enable_l3 or settings.enable_l3,
        enable_ner=settings.enable_ner if enable_ner is None else enable_ner,
        enable_keywords=enable_keywords,
        mask_level=mask_level,
        min_level=min_level,
    )
    return _to_out(pipeline.analyze_document(document, options), document)


@router.get("/formats", summary="支持的输入格式", response_model=None)
def formats() -> dict[str, object]:
    return supported_formats()


# ---------------------------------------------------------------------------
# 第一次优化：脱敏策略清单 / 脱敏文件导出 / 密文还原
# ---------------------------------------------------------------------------

#: 导出请求里 ``content_type`` → 对应解析器（docx/pdf 只能走上传入口）
_EXPORT_PARSERS = {
    "text": lambda text: parse_text(text, source="export.txt"),
    "json": lambda text: parse_json(text, source="export.json"),
    "csv": lambda text: parse_csv(text, source="export.csv"),
    "sql": lambda text: parse_sql(text, source="export.sql"),
}

_UNPROCESSABLE_EXPORT_DETAIL = "请求内容无法处理（格式与 content_type 不一致或为空）"


@router.get(
    "/mask-strategies",
    response_model=MaskStrategiesOut,
    summary="支持的脱敏策略（前端下拉框文案来源）",
)
def mask_strategies() -> MaskStrategiesOut:
    """六种脱敏策略的元数据；示例值均为虚构数据，可直接展示给用户。"""
    return MaskStrategiesOut(
        strategies=[MaskStrategyOut(**meta) for meta in MASK_STRATEGIES.values()],
        default=DEFAULT_STRATEGY,
    )


def _validate_export_options(strategy: str, fmt: str) -> tuple[str, str]:
    """校验导出的策略 / 格式（返回归一化后的键）。未知值直接 400，不回显请求内容。"""
    strategy_key = (strategy or "").strip().lower()
    if strategy_key not in MASK_STRATEGIES:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="不支持的脱敏策略")
    fmt_key = (fmt or "auto").strip().lower()
    if fmt_key != "auto" and fmt_key not in EXPORT_FORMATS:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="不支持的输出格式")
    return strategy_key, fmt_key


def _export_secret_or_400(settings: Settings, strategy: str) -> str:
    """可逆加密策略必须有导出密钥；没有就 400（这是服务端配置问题，不静默降级）。"""
    if strategy == "encrypt" and not settings.export_secret:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="脱敏导出密钥未配置")
    return settings.export_secret


def _export_download(content: bytes, media_type: str, ext: str) -> Response:
    """包装成浏览器下载响应：附件名带时间戳，避免同名覆盖。"""
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return Response(
        content=content,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="flowmask-export-{stamp}.{ext}"'},
    )


def _analyze_export_document(
    document: Document,
    *,
    settings: Settings,
    pipeline: DetectionPipeline,
    enable_l2: bool,
    enable_l3: bool,
    enable_ner: bool | None,
    enable_keywords: bool | None,
) -> DocumentReport:
    """导出用的在线分析：``min_level="SAFE"`` 保证拿回全量 finding，不受级别筛选影响。"""
    options = build_options(
        enable_l2=enable_l2 or settings.enable_l2,
        enable_l3=enable_l3 or settings.enable_l3,
        enable_ner=settings.enable_ner if enable_ner is None else enable_ner,
        enable_keywords=enable_keywords,
        mask_level=None,
        min_level="SAFE",
    )
    return pipeline.analyze_document(document, options)


@router.post("/detect/export", summary="即时导出脱敏文件：粘贴文本/JSON/CSV/SQL")
def detect_export(
    payload: DetectExportRequest,
    settings: Settings = Depends(settings_dep),
    pipeline: DetectionPipeline = Depends(pipeline_dep),
) -> Response:
    """同一请求内完成"分析 → 按策略重建 → 下载"，原文不落库。"""
    strategy_key, fmt_key = _validate_export_options(payload.strategy, payload.fmt)
    secret = _export_secret_or_400(settings, strategy_key)
    parser = _EXPORT_PARSERS.get((payload.content_type or "text").strip().lower())
    if parser is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=_UNPROCESSABLE_EXPORT_DETAIL)
    try:
        document = parser(payload.text)
    except (ParseError, ValueError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=_UNPROCESSABLE_EXPORT_DETAIL) from exc
    report = _analyze_export_document(
        document,
        settings=settings,
        pipeline=pipeline,
        enable_l2=payload.enable_l2,
        enable_l3=payload.enable_l3,
        enable_ner=payload.enable_ner,
        enable_keywords=payload.enable_keywords,
    )
    try:
        content, media_type, ext = build_masked_bytes(
            document, report, strategy=strategy_key, fmt=fmt_key, secret=secret
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return _export_download(content, media_type, ext)


@router.post("/detect/decrypt", response_model=DetectDecryptOut, summary="还原加密策略导出的密文片段")
def detect_decrypt(
    payload: DetectDecryptIn,
    settings: Settings = Depends(settings_dep),
) -> DetectDecryptOut:
    """只还原本机导出密钥加密的 ``enc:`` 片段；错误信息不回显密文或明文。"""
    if not ENCRYPTED_PATTERN.search(payload.ciphertext):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="没有可还原的密文片段")
    try:
        text, replaced = decrypt_encrypted_text(payload.ciphertext, settings.export_secret)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return DetectDecryptOut(text=text, replaced=replaced)


@router.post("/detect/file/export", summary="即时导出脱敏文件：上传文件")
async def detect_file_export(
    file: UploadFile = File(..., description="待脱敏导出的文件"),
    strategy: str = Form("partial", description="脱敏策略：block / partial / hash / generalize / pseudonym / encrypt"),
    fmt: str = Form("auto", description="输出格式：auto / txt / json / csv / sql / docx / xlsx"),
    enable_l2: bool = Form(False),
    enable_l3: bool = Form(False),
    enable_ner: bool | None = Form(None),
    enable_keywords: bool | None = Form(None),
    settings: Settings = Depends(settings_dep),
    pipeline: DetectionPipeline = Depends(pipeline_dep),
) -> Response:
    """上传 → 解析 → 分析 → 按策略重建 → 下载；图片需要先装 OCR 依赖。"""
    suffix = PurePath(file.filename or "").suffix.lower()
    if suffix in IMAGE_EXTENSIONS or (file.content_type or "").startswith("image/"):
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="图片文件暂不支持导出（需先安装 OCR 可选依赖）",
        )
    strategy_key, fmt_key = _validate_export_options(strategy, fmt)
    secret = _export_secret_or_400(settings, strategy_key)
    data = await read_upload_limited(file, settings.max_upload_mb * 1024 * 1024)
    try:
        document = parse_bytes(file.filename or "", data)
    except ParseUnsupported as exc:
        raise HTTPException(status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, detail="不支持的文件类型") from exc
    except ParseError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    report = _analyze_export_document(
        document,
        settings=settings,
        pipeline=pipeline,
        enable_l2=enable_l2,
        enable_l3=enable_l3,
        enable_ner=enable_ner,
        enable_keywords=enable_keywords,
    )
    try:
        content, media_type, ext = build_masked_bytes(
            document, report, strategy=strategy_key, fmt=fmt_key, secret=secret
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return _export_download(content, media_type, ext)
