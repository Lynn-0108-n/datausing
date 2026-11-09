"""图片 OCR 解析：png/jpg → 文字 → Document（content_type='text'）。

设计要点：
- **依赖可选**：``rapidocr-onnxruntime`` 不在默认依赖里，未安装时解析器
  会抛 ``ParseUnsupported``，让 ``/detect/file`` 返回 415 而不是 500；
- **懒加载**：模块级 ``_engine = None``，首次调用时才初始化（避免一启动
  就拉模型权重拖慢冷启动）；
- **OCR 失败抛 ``ParseError``**：让上层按 422 处理，不让一个坏图片拖垮接口；
- **settings.ocr_enabled 开关**：关闭时直接 ``ParseUnsupported("OCR 未启用")``，
  评委默认环境不需要装 ONNX runtime 也能跑；
- **OCR 结果是文本**：多行用 ``\\n`` 拼接后走 ``parse_text`` 复用纯文本路径，
  保持与粘贴文本一致的识别/脱敏/导出链路。
"""

from __future__ import annotations

from typing import Any

from .base import Document, ParseError, ParseUnsupported
from .text import parse_text

# 模块级懒加载引擎；多次调用只初始化一次
_engine: Any = None
_engine_loaded = False


def _get_engine():
    """懒加载 rapidocr-onnxruntime；失败返回 None。"""
    global _engine, _engine_loaded
    if _engine_loaded:
        return _engine
    _engine_loaded = True
    try:
        from rapidocr_onnxruntime import RapidOCR  # type: ignore[import-not-found]
    except Exception:  # noqa: BLE001
        # 依赖未安装：返回 None，调用方据此抛 ParseUnsupported
        _engine = None
        return None
    try:
        _engine = RapidOCR()
    except Exception as exc:  # noqa: BLE001
        # 依赖装了但初始化失败（缺 ONNX runtime、模型权重等）
        _engine = None
        _engine_init_error = str(exc)  # noqa: F841
    return _engine


def parse_image(data: bytes, source: str = "image") -> Document:
    """png/jpg → OCR 提取文字 → Document（content_type='text'）。

    - OCR 关闭（``ocr_enabled=False``）→ ``ParseUnsupported("OCR 未启用")``；
    - rapidocr 未安装或初始化失败 → ``ParseUnsupported``（前端 415）；
    - OCR 调用失败 → ``ParseError("OCR 失败：...")``（前端 422）。
    """
    # 开关检查：默认关，避免评委环境必须装 ONNX runtime
    from ..core.config import get_settings

    settings = get_settings()
    if not settings.ocr_enabled:
        raise ParseUnsupported("OCR 未启用（设置 FLOWMASK_OCR_ENABLED=1 开启）")

    engine = _get_engine()
    if engine is None:
        raise ParseUnsupported(
            "OCR 引擎未就绪：需要安装 rapidocr-onnxruntime（pip install rapidocr-onnxruntime）"
        )

    try:
        result, _elapsed = engine(data)
    except Exception as exc:  # noqa: BLE001
        raise ParseError(f"OCR 失败：{exc}") from exc

    if not result:
        raise ParseError("OCR 未识别到任何文字（图片可能为空或无文字）")

    # rapidocr 返回 [[box, text, score], ...]；只取 text，按行拼接
    lines: list[str] = []
    for item in result:
        if not item or len(item) < 2:
            continue
        text = str(item[1] or "").strip()
        if text:
            lines.append(text)
    if not lines:
        raise ParseError("OCR 未识别到任何文字")

    text = "\n".join(lines)
    document = parse_text(text, source=source)
    document.meta["ocr"] = True
    document.meta["ocr_engine"] = settings.ocr_engine
    return document
