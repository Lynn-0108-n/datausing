"""图片 OCR 解析器（可选能力）。

OCR 走 ``rapidocr-onnxruntime``：纯本机推理，不上传图片，符合「数据不出本机」的定位。
但它的依赖（onnxruntime + 模型文件）比较重，所以设计成可选依赖：

- 未设置 ``FLOWMASK_OCR_ENABLED=1`` 或未安装依赖时，``parse_image`` 抛 ``ParseError``
  并给出固定中文安装提示，由上层转成 4xx 而不是 500；
- OCR 是懒加载的：只有真正解析图片时才导入模型，避免拖慢应用启动和 /formats 探测；
- OCR 无结果（纯色图、文字太小）不算错误，返回空文本 + hint 说明原因。

图片解码与缩放用 Pillow（随 requirements-ocr.txt / requirements-desktop.txt 安装）完成：
``FLOWMASK_OCR_MAX_SIDE`` 控制参与 OCR 的长边上限，避免超大照片把推理时间拉长。
"""

from __future__ import annotations

import io

from ..core.config import get_settings
from .base import Document, ParseError, Segment

#: 未启用/未安装时的固定提示（对外文案，测试与前端提示都依赖它）
OCR_UNAVAILABLE_MESSAGE = "未启用 OCR：安装可选依赖 requirements-ocr.txt 并设置 FLOWMASK_OCR_ENABLED=1"

#: 懒加载缓存：导入 rapidocr 会连带加载 onnxruntime，只做一次
_engine_loaded = False
_engine_cls: object | None = None


def _load_rapidocr() -> object | None:
    """导入 RapidOCR 类；未安装或依赖不完整时返回 None（不抛出）。"""
    global _engine_loaded, _engine_cls
    if _engine_loaded:
        return _engine_cls
    _engine_loaded = True
    try:
        from rapidocr_onnxruntime import RapidOCR
    except Exception:  # 缺 onnxruntime 时可能抛 RuntimeError 而不是 ImportError
        _engine_cls = None
    else:
        _engine_cls = RapidOCR
    return _engine_cls


def ocr_engine() -> str | None:
    """当前可用的 OCR 引擎名；未安装返回 None。

    只做依赖探测，不加载模型、不触发推理，供 ``supported_formats()`` 展示能力用。
    """
    return "rapidocr" if _load_rapidocr() is not None else None


def _prepare_image(data: bytes, max_side: int) -> tuple[bytes, str, tuple[int, int], float]:
    """用 Pillow 解码并按长边等比缩放，返回 (PNG 字节, 原始色彩模式, 原尺寸, 缩放比)。

    OCR 前统一转 PNG 交给 rapidocr：它内部按文件路径/字节解码，PNG 最稳。
    """
    try:
        from PIL import Image
    except ImportError as exc:  # Pillow 为 OCR 模式的可选依赖，缺了给可操作的提示
        raise ParseError("图片解码需要 Pillow：pip install pillow") from exc

    try:
        with Image.open(io.BytesIO(data)) as image:
            image.load()
            colorspace = image.mode
            width, height = image.size
            longest = max(width, height)
            scale = 1.0
            working = image
            if max_side > 0 and longest > max_side:
                scale = max_side / float(longest)
                target = (max(1, round(width * scale)), max(1, round(height * scale)))
                resample = getattr(Image, "Resampling", Image).LANCZOS
                working = image.resize(target, resample)
            if working.mode not in ("RGB", "L"):
                working = working.convert("RGB")
            buffer = io.BytesIO()
            working.save(buffer, format="PNG")
            return buffer.getvalue(), colorspace, (width, height), scale
    except ParseError:
        raise
    except Exception as exc:
        # 不把底层异常原文直接外抛，避免泄露路径等信息
        raise ParseError("图片解码失败（支持 png/jpg/jpeg/bmp/webp）") from exc


def _box_origin(box: object) -> tuple[float, float] | None:
    """取文本框左上角坐标；结构异常返回 None（该块退化为按返回顺序排列）。"""
    try:
        first = box[0]  # type: ignore[index]
        return float(first[0]), float(first[1])  # type: ignore[index]
    except (TypeError, ValueError, IndexError):
        return None


def _extract_texts(result: object) -> list[str]:
    """从 RapidOCR 返回值里提取文本，按「先上后下、再左到右」排序。

    rapidocr 返回形如 ``([[四个角点], "文本", 置信度], ...)``，有些版本包成
    ``(结果, 耗时)`` 二元组；这里对两种形状都做兼容。
    """
    blocks: object = result
    if isinstance(result, tuple) and len(result) == 2:
        blocks = result[0]
    if not isinstance(blocks, (list, tuple)):
        return []

    rows: list[tuple[float, float, int, str]] = []
    for index, item in enumerate(blocks):
        if not isinstance(item, (list, tuple)) or len(item) < 2:
            continue
        text = str(item[1] or "").strip()
        if not text:
            continue
        origin = _box_origin(item[0])
        # 取不到坐标时按识别顺序排在最后，避免打乱正常文本的阅读顺序
        x, y = origin if origin is not None else (0.0, float(10**9 + index))
        rows.append((y, x, index, text))
    rows.sort(key=lambda row: (row[0], row[1]))
    return [row[3] for row in rows]


def parse_image(data: bytes, name: str = "") -> Document:
    """解析图片：OCR 出文字后作为单个 segment 返回。

    - 未启用 OCR / 依赖缺失：``ParseError``（固定中文提示）
    - 可解码但无文字：返回空文本，hint 里记录原因
    - 解码失败：``ParseError("图片解码失败…")``
    """
    settings = get_settings()
    if not settings.ocr_enabled:
        raise ParseError(OCR_UNAVAILABLE_MESSAGE)
    engine_cls = _load_rapidocr()
    if engine_cls is None:
        raise ParseError(OCR_UNAVAILABLE_MESSAGE)

    png, colorspace, (width, height), scale = _prepare_image(data, settings.ocr_max_side)

    result: object = None
    try:
        engine = engine_cls()  # type: ignore[operator]
        result = engine(png)
    except Exception as exc:
        raise ParseError("图片 OCR 识别失败（可换更清晰的图片或调大 FLOWMASK_OCR_MAX_SIDE）") from exc

    texts = _extract_texts(result)
    text = "\n".join(texts)
    hint = f"colorspace={colorspace} size={width}x{height} scale={scale:.2f}"
    if not text:
        hint += "；未识别到文字（可能是纯色图、文字过小或图片模糊）"
    return Document(
        source=name or "image",
        content_type="image",
        segments=[Segment(path="image.ocr", text=text, hint=hint)],
        raw=text,
        meta={
            "engine": "rapidocr",
            "colorspace": colorspace,
            "size": [width, height],
            "scale": round(scale, 3),
            "boxes": len(texts),
        },
    )
