"""格式注册表：按扩展名把上传内容分派给对应解析器。

新增格式只需要在这里登记一行——识别层不需要任何改动，对应需求里的"插件化可扩展"。
"""

from __future__ import annotations

import json
from pathlib import PurePath

from .base import Document, ParseError, ParseUnsupported
from .csv_parser import parse_csv
from .docx_parser import parse_docx
from .image_parser import parse_image
from .json_parser import parse_json
from .pdf_parser import parse_pdf, pdf_engine
from .sql_parser import parse_sql
from .text import parse_text

SUPPORTED_TEXT_EXTENSIONS: dict[str, str] = {
    ".txt": "text",
    ".log": "text",
    ".md": "text",
    ".json": "json",
    ".csv": "csv",
    ".tsv": "csv",
    ".sql": "sql",
}
# 二进制文档格式：docx 用标准库解析；pdf 依赖可选引擎（见 pdf_engine）
DOCUMENT_EXTENSIONS: dict[str, str] = {".docx": "docx", ".pdf": "pdf"}
# 图片格式：OCR 解析（依赖 rapidocr-onnxruntime，且需 settings.ocr_enabled）
IMAGE_EXTENSIONS: dict[str, str] = {".png": "image", ".jpg": "image", ".jpeg": "image"}


def decode_bytes(data: bytes) -> str:
    """按 UTF-8（含 BOM）→ GBK 的顺序解码；都失败就报错，避免悄悄产出乱码。"""
    for encoding in ("utf-8-sig", "gbk"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ParseError("文件编码无法识别（支持 UTF-8 / GBK）")


def parse_bytes(filename: str, data: bytes) -> Document:
    """按扩展名解析上传文件。"""
    suffix = PurePath(filename).suffix.lower()
    if suffix in DOCUMENT_EXTENSIONS:
        if DOCUMENT_EXTENSIONS[suffix] == "docx":
            return parse_docx(data, source=filename)
        return parse_pdf(data, source=filename)
    if suffix in IMAGE_EXTENSIONS:
        # 图片 OCR：内部会检查 settings.ocr_enabled 与依赖是否就绪
        return parse_image(data, source=filename)
    if suffix not in SUPPORTED_TEXT_EXTENSIONS:
        supported = ", ".join(
            sorted(set(SUPPORTED_TEXT_EXTENSIONS) | set(DOCUMENT_EXTENSIONS) | set(IMAGE_EXTENSIONS))
        )
        raise ParseUnsupported(f"暂不支持 {suffix or '无扩展名'} 文件；当前支持：{supported}")

    text = decode_bytes(data)
    content_type = SUPPORTED_TEXT_EXTENSIONS[suffix]
    if content_type == "json":
        return parse_json(text, source=filename)
    if content_type == "csv":
        return parse_csv(text, source=filename)
    if content_type == "sql":
        return parse_sql(text, source=filename)
    document = parse_text(text, source=filename)
    document.meta["suffix"] = suffix
    return document


def parse_text_input(text: str, source: str = "text") -> Document:
    """文本粘贴入口：尝试识别 JSON/CSV/SQL 形态，失败则按纯文本处理。"""
    stripped = text.strip()
    if stripped.startswith(("{", "[")):
        try:
            return parse_json(text, source=source)
        except ParseError:
            pass
    document = parse_text(text, source=source)
    document.meta["detected"] = "text"
    return document


def supported_formats() -> dict[str, object]:
    """给 /formats 与前端用：能传什么、PDF 引擎是否就绪、缺依赖怎么装。"""
    from ..core.config import get_settings

    engine = pdf_engine()
    settings = get_settings()
    return {
        "supported": sorted(
            set(SUPPORTED_TEXT_EXTENSIONS) | set(DOCUMENT_EXTENSIONS) | set(IMAGE_EXTENSIONS)
        ),
        "docx": True,
        "pdf": engine is not None,
        "pdf_engine": engine,
        "ocr": settings.ocr_enabled,
        "ocr_engine": settings.ocr_engine,
        "hint": "" if engine is not None else "PDF 需要可选依赖：pip install pdfplumber",
    }
