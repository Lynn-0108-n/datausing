"""即时检测：文本粘贴 / 文件上传。

这两个接口**不落库**，用于"脱敏前/后左右对照"的现场演示；
需要留痕与审计时走 ``POST /api/v1/tasks``（后台任务，写 findings + 审计链）。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, HTTPException, Response, UploadFile, status

from ..core.config import Settings
from ..core.db import Database
from ..identify import DetectionPipeline, MaskStrategy
from ..identify.pipeline import DocumentReport
from ..parsers.base import Document, ParseError, ParseUnsupported
from ..parsers.registry import parse_bytes, parse_text_input, supported_formats
from ..scenarios import get_scenario, list_scenarios
from ..tasks.document_export import build_masked_file, build_masked_preview
from ..tasks.export import masked_document
from ..tasks.service import create_task
from .deps import build_options, db_dep, operator_dep, pipeline_dep, settings_dep
from .schemas import DetectExportRequest, DetectOut, DetectRequest, SegmentOut, SpanOut

router = APIRouter(tags=["detect"])


def _to_out(report: DocumentReport, document: Document, task_id: str = "") -> DetectOut:
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
        task_id=task_id,
    )


def _safe_create_task(
    db: Database,
    *,
    source: str,
    content_type: str,
    document: Document,
    options,
    pipeline: DetectionPipeline,
) -> str:
    """自动建 task：失败不阻塞检测结果返回，task_id 返回空字符串。

    与 ``/tasks`` 显式建任务走同一条 ``create_task``，因此检测记录页
    能直接看到本次检测的内容、命中与审计链。
    """
    try:
        return create_task(
            db,
            source=source,
            content_type=content_type,
            document=document,
            options=options,
            pipeline=pipeline,
        )
    except Exception:  # noqa: BLE001
        # 任何异常都不影响检测结果的返回；用户看到 task_id 为空即知道记录未留存
        return ""


@router.post("/detect", response_model=DetectOut, summary="即时检测：粘贴文本")
def detect_text(
    payload: DetectRequest,
    settings: Settings = Depends(settings_dep),
    pipeline: DetectionPipeline = Depends(pipeline_dep),
    db: Database = Depends(db_dep),
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
    report = pipeline.analyze_document(document, options)
    # 检测成功后自动建 task（失败不阻塞）
    task_id = _safe_create_task(
        db, source="text", content_type=document.content_type,
        document=document, options=options, pipeline=pipeline,
    )
    return _to_out(report, document, task_id=task_id)


@router.post("/detect/file", response_model=DetectOut, summary="即时检测：上传文件（txt/json/csv/sql）")
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
    db: Database = Depends(db_dep),
) -> DetectOut:
    data = await file.read()
    limit = settings.max_upload_mb * 1024 * 1024
    if len(data) > limit:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"文件超过上限 {settings.max_upload_mb} MB",
        )
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
    report = pipeline.analyze_document(document, options)
    # 检测成功后自动建 task（失败不阻塞）
    task_id = _safe_create_task(
        db, source=file.filename or "upload", content_type=document.content_type,
        document=document, options=options, pipeline=pipeline,
    )
    return _to_out(report, document, task_id=task_id)


# ---------- 多策略脱敏文件导出（Day 1.3 新增）----------
#
# 与 /detect、/detect/file 的区别：
# - 返回的是文件流（Response），不是 JSON；
# - 内部固定 min_level=SAFE（覆盖所有命中，不受界面分级过滤影响）；
# - 用户可选脱敏策略（block/partial/hash/generalize/pseudonym/encrypt）；
# - 不落库、不产生 task、不写审计链（与 /detect 一致）。


@router.post("/detect/export", summary="即时检测并导出脱敏文件（文本）")
def detect_export_text(
    payload: DetectExportRequest,
    settings: Settings = Depends(settings_dep),
    pipeline: DetectionPipeline = Depends(pipeline_dep),
) -> Response:
    # 解析策略字符串 → 枚举（非法返 400）
    try:
        strategy = MaskStrategy.parse(payload.strategy)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    # 文本入口只允许 text/json/csv/sql 格式；docx/pdf 只能从上传文件来
    _TEXT_ALLOWED_FORMATS = {"text", "json", "csv", "sql"}
    if payload.format is not None and payload.format not in _TEXT_ALLOWED_FORMATS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"文本入口不支持格式 {payload.format}，可选 {sorted(_TEXT_ALLOWED_FORMATS)}",
        )

    document = parse_text_input(payload.text, source="text")
    # 固定 min_level=SAFE：覆盖所有命中，不受界面分级过滤影响
    options = build_options(
        enable_l2=payload.enable_l2 or settings.enable_l2,
        enable_l3=payload.enable_l3 or settings.enable_l3,
        enable_ner=settings.enable_ner if payload.enable_ner is None else payload.enable_ner,
        enable_keywords=payload.enable_keywords,
        mask_level=payload.mask_level,
        min_level="SAFE",
    )
    report = pipeline.analyze_document(document, options)

    # 预览模式：返回 JSON {masked}，不下载文件，用于左栏实时预览导出策略效果
    if payload.preview:
        preview_text = build_masked_preview(document, report, strategy)
        return {"masked": preview_text}

    # 默认文件名：text + 按内容类型选扩展名
    original_name = "text.txt"
    if payload.format is None:
        # 自动探测：JSON 文本用 .json，其余 .txt
        stripped = payload.text.strip()
        if stripped.startswith(("{", "[")):
            original_name = "text.json"
    elif payload.format == "csv":
        original_name = "text.csv"
    elif payload.format == "sql":
        original_name = "text.sql"
    elif payload.format == "json":
        original_name = "text.json"

    try:
        data, media, disposition, key_b64 = build_masked_file(
            document, report, original_name, strategy, fmt=payload.format
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    headers = {"Content-Disposition": disposition, "X-FlowMask-Export": "masked"}
    if key_b64:
        headers["X-FlowMask-Encrypt-Key"] = key_b64
    return Response(content=data, media_type=media, headers=headers)


@router.post("/detect/file/export", summary="即时检测并导出脱敏文件（文件上传）")
async def detect_export_file(
    file: UploadFile = File(..., description="待检测文件"),
    strategy: str = Form("partial"),
    format: str | None = Form(None),
    enable_l2: bool = Form(False),
    enable_l3: bool = Form(False),
    enable_ner: bool | None = Form(None),
    enable_keywords: bool | None = Form(None),
    mask_level: str | None = Form(None),
    settings: Settings = Depends(settings_dep),
    pipeline: DetectionPipeline = Depends(pipeline_dep),
) -> Response:
    # 解析策略字符串 → 枚举
    try:
        strategy_enum = MaskStrategy.parse(strategy)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    data = await file.read()
    limit = settings.max_upload_mb * 1024 * 1024
    if len(data) > limit:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"文件超过上限 {settings.max_upload_mb} MB",
        )
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
        min_level="SAFE",
    )
    report = pipeline.analyze_document(document, options)

    try:
        data_bytes, media, disposition, key_b64 = build_masked_file(
            document, report, file.filename or "file.txt", strategy_enum, fmt=format
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    headers = {"Content-Disposition": disposition, "X-FlowMask-Export": "masked"}
    if key_b64:
        headers["X-FlowMask-Encrypt-Key"] = key_b64
    return Response(content=data_bytes, media_type=media, headers=headers)


@router.get("/formats", summary="支持的输入格式", response_model=None)
def formats() -> dict[str, object]:
    return supported_formats()


# ---------- 场景模式（Day 2.2 新增）----------
#
# 内置 6 个场景（医疗/政务/电商/金融/HR/审计），给内容检测页一键套用参数。
# 自定义场景走前端 localStorage，不经过这里。


@router.get("/scenarios", summary="列出场景模式", response_model=None)
def scenarios() -> dict[str, object]:
    """返回所有内置场景的列表（含推荐参数与样例文本）。"""
    return {"items": list_scenarios()}


@router.get("/scenarios/{scenario_id}", summary="场景详情", response_model=None)
def scenario_detail(scenario_id: str) -> dict[str, object]:
    """按 id 返回单个场景详情；不存在返回 404。"""
    scenario = get_scenario(scenario_id)
    if scenario is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"场景不存在：{scenario_id}",
        )
    from dataclasses import asdict

    return asdict(scenario)
