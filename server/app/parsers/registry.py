"""格式注册表：按扩展名把上传内容分派给对应解析器。

新增格式只需要登记一行——识别层不需要任何改动，对应需求里的"插件化可扩展"。
内置格式在模块导入时登记；第三方插件可在运行时调用 :func:`register_format`
追加新格式（见 ``app/plugins.py`` 的统一插件注册表）。
"""

from __future__ import annotations

import os
from pathlib import PurePath
from typing import Callable

from .base import Document, ParseError, ParseUnsupported
from .csv_parser import parse_csv
from .docx_parser import parse_docx
from .image_parser import ocr_engine, parse_image
from .json_parser import parse_json
from .pdf_parser import parse_pdf, pdf_engine
from .sql_parser import parse_sql
from .text import parse_text
from .xlsx_parser import parse_xlsx

#: 解析器契约：``handler(filename, data) -> Document``，直接拿原始字节自行解码。
FormatHandler = Callable[[str, bytes], Document]

# 文本类扩展名 → content_type；二进制文档单独维护（可见性/依赖不同，提示文案也不同）。
SUPPORTED_TEXT_EXTENSIONS: dict[str, str] = {}
DOCUMENT_EXTENSIONS: dict[str, str] = {}
# 图片单独登记：OCR 是可选能力，扩展名仍进分派表与 /formats 的 supported 列表，
# 但不进 format_specs() 的解析器清单（插件页展示的仍是原有六类内置解析器）。
IMAGE_EXTENSIONS: dict[str, str] = {}
_FORMAT_HANDLERS: dict[str, FormatHandler] = {}


def decode_bytes(data: bytes) -> str:
    """按 UTF-8（含 BOM）→ GBK 的顺序解码；都失败就报错，避免悄悄产出乱码。"""
    for encoding in ("utf-8-sig", "gbk"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ParseError("文件编码无法识别（支持 UTF-8 / GBK）")


def register_format(
    suffixes: str | tuple[str, ...] | list[str],
    handler: FormatHandler,
    *,
    content_type: str,
    binary: bool = False,
    replace: bool = False,
) -> tuple[str, ...]:
    """登记一个格式：``suffixes`` 全部映射到 ``handler``，返回规范化后的扩展名。

    ``handler(filename, data)`` 直接拿原始字节；文本类格式自行解码（内置实现统一走
    :func:`decode_bytes`）。扩展名已被占用且未 ``replace=True`` 时抛 ``ValueError``——
    插件不能悄悄顶掉内置解析器，避免"装个插件把 PDF 解析换坏了"。
    """
    if isinstance(suffixes, str):
        suffixes = (suffixes,)
    normalized: list[str] = []
    for suffix in suffixes:
        suffix = suffix.strip().lower()
        if not suffix:
            continue
        if not suffix.startswith("."):
            suffix = f".{suffix}"
        existing = (
            SUPPORTED_TEXT_EXTENSIONS.get(suffix)
            or DOCUMENT_EXTENSIONS.get(suffix)
            or IMAGE_EXTENSIONS.get(suffix)
        )
        if existing and not replace:
            raise ValueError(f"格式扩展名已被占用：{suffix}（{existing}）")
        if existing:
            SUPPORTED_TEXT_EXTENSIONS.pop(suffix, None)
            DOCUMENT_EXTENSIONS.pop(suffix, None)
            IMAGE_EXTENSIONS.pop(suffix, None)
        target = DOCUMENT_EXTENSIONS if binary else SUPPORTED_TEXT_EXTENSIONS
        target[suffix] = content_type
        _FORMAT_HANDLERS[suffix] = handler
        normalized.append(suffix)
    return tuple(normalized)


def _text_handler(parser: Callable[[str, str], Document], *, mark_suffix: bool = False) -> FormatHandler:
    """把「先解码、再解析文本」的解析器包成统一的字节契约。"""

    def handler(filename: str, data: bytes) -> Document:
        document = parser(decode_bytes(data), source=filename)
        if mark_suffix:
            document.meta["suffix"] = PurePath(filename).suffix.lower()
        return document

    return handler


def _docx_handler(filename: str, data: bytes) -> Document:
    return parse_docx(data, source=filename)


def _pdf_handler(filename: str, data: bytes) -> Document:
    return parse_pdf(data, source=filename)


def _xlsx_handler(filename: str, data: bytes) -> Document:
    return parse_xlsx(data, source=filename)


def _image_handler(filename: str, data: bytes) -> Document:
    """图片交给 OCR 解析器；未启用/未安装时 parse_image 会抛 ParseError 提示装依赖。"""
    return parse_image(data, name=filename)


def _register_builtin_formats() -> None:
    """内置格式：文本类先解码再按形态解析；docx/pdf/图片走二进制解析器。"""
    register_format((".txt", ".log", ".md"), _text_handler(parse_text, mark_suffix=True), content_type="text")
    register_format((".json",), _text_handler(parse_json), content_type="json")
    register_format((".csv", ".tsv"), _text_handler(parse_csv), content_type="csv")
    register_format((".sql",), _text_handler(parse_sql), content_type="sql")
    register_format((".docx",), _docx_handler, content_type="docx", binary=True)
    register_format((".xlsx",), _xlsx_handler, content_type="xlsx", binary=True)
    register_format((".pdf",), _pdf_handler, content_type="pdf", binary=True)
    # 图片单独登记（见 IMAGE_EXTENSIONS 注释：不参与 format_specs 的插件清单）
    for suffix in (".png", ".jpg", ".jpeg", ".bmp", ".webp"):
        IMAGE_EXTENSIONS[suffix] = "image"
        _FORMAT_HANDLERS[suffix] = _image_handler


_register_builtin_formats()


def format_specs() -> list[dict[str, object]]:
    """按 content_type 汇总当前已登记格式（给插件注册表与前端展示用）。"""
    grouped: dict[str, list[str]] = {}
    for suffix, content_type in {**SUPPORTED_TEXT_EXTENSIONS, **DOCUMENT_EXTENSIONS}.items():
        grouped.setdefault(content_type, []).append(suffix)
    return [
        {
            "name": name,
            "extensions": sorted(extensions),
            "binary": all(suffix in DOCUMENT_EXTENSIONS for suffix in extensions),
        }
        for name, extensions in sorted(grouped.items())
    ]


def supported_extensions() -> list[str]:
    """当前可解析的全部扩展名（含运行时登记的插件格式）。"""
    return sorted(_FORMAT_HANDLERS)


def _ocr_enabled_by_env() -> bool:
    """只读环境变量判断 OCR 开关，不经过 ``get_settings()``（避免创建数据目录）。

    与 ``config.Settings.ocr_enabled`` 的解析规则保持一致；/formats 是只读接口，
    不能因为一次探测就在仓库里落数据目录或生成密钥。
    """
    value = os.environ.get("FLOWMASK_OCR_ENABLED", "").strip().lower()
    return value in ("1", "true", "yes", "on")


def supported_formats() -> dict[str, object]:
    """给 /formats 与前端用：能传什么、PDF/OCR 是否就绪、缺依赖怎么装。

    这里只做依赖探测，不读 ``get_settings()``——避免在只读 /formats 请求里创建数据
    目录或写密钥，也让该函数可以在没有临时环境的测试里安全调用。

    OCR 未启用（依赖缺失或 FLOWMASK_OCR_ENABLED 未开）时 ``supported`` 保持原有
    10 个文本/文档扩展名不变，图片扩展名只在真正可 OCR 后才出现：前端据此决定是否
    展示「上传图片」入口，而不需要理解配置项。
    """
    engine = pdf_engine()
    ocr = ocr_engine()
    ocr_on = ocr is not None and _ocr_enabled_by_env()
    supported = supported_extensions()
    if not ocr_on:
        supported = [suffix for suffix in supported if suffix not in IMAGE_EXTENSIONS]
    if ocr_on:
        ocr_hint = ""
    elif ocr is None:
        ocr_hint = "图片 OCR 需要可选依赖：pip install -r requirements-ocr.txt 并设置 FLOWMASK_OCR_ENABLED=1"
    else:
        ocr_hint = "图片 OCR 依赖已安装；设置 FLOWMASK_OCR_ENABLED=1 后即可上传图片"
    return {
        "supported": supported,
        "docx": True,
        "xlsx": True,
        "pdf": engine is not None,
        "pdf_engine": engine,
        "image": ocr_on,
        "ocr_engine": ocr,
        "ocr_hint": ocr_hint,
        "hint": "" if engine is not None else "PDF 需要可选依赖：pip install pdfplumber",
    }


def parse_bytes(filename: str, data: bytes) -> Document:
    """按扩展名解析上传文件。"""
    suffix = PurePath(filename).suffix.lower()
    handler = _FORMAT_HANDLERS.get(suffix)
    if handler is None:
        supported = ", ".join(supported_extensions())
        raise ParseUnsupported(f"暂不支持 {suffix or '无扩展名'} 文件；当前支持：{supported}")
    return handler(filename, data)


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
