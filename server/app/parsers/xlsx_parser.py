"""Excel（.xlsx）接入：用标准库解析 OOXML，不引入 openpyxl。

.xlsx 本质是一个 zip：工作簿结构在 ``xl/workbook.xml``（工作表名 → 关系 id）、
``xl/_rels/workbook.xml.rels``（关系 id → 工作表文件）、``xl/sharedStrings.xml``
（共享字符串表），每张表的单元格在 ``xl/worksheets/sheetN.xml``。

与 CSV 保持一致：**每张表的第一行内容行视为表头**——表头单元格不单独检测
（避免"身份证号"这类列名误报），只作为其余单元格的 ``hint``（列名）进入识别
上下文。文本、共享字符串、数值、布尔、日期、错误值都生成检测段——Excel 里
手机号、身份证号经常就存成数字。

Segment 路径 ``{工作表}!{单元格坐标}``（如 ``客户表!B2``）；导出时按
``meta["xlsx"]`` 记录的行列结构逐格写回，表头原文不动、数据格换成脱敏值。
"""

from __future__ import annotations

import io
import re
import xml.etree.ElementTree as ET
import zipfile

from .base import Document, ParseError, Segment

_XLSX = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_PKG_REL = "http://schemas.openxmlformats.org/package/2006/relationships"
_SHARED_STRINGS_REL = (
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships/sharedStrings"
)

# 单个 XML 部件与整个包解压后的上限，避免压缩炸弹（与 docx 解析器口径一致）
_MAX_XML_BYTES = 64 * 1024 * 1024
_MAX_TOTAL_XML_BYTES = 256 * 1024 * 1024

_REF_PATTERN = re.compile(r"^([A-Za-z]+)(\d+)$")


def _q(tag: str) -> str:
    """把 ``row`` / ``c`` 这类本地名加上 SpreadsheetML 命名空间。"""
    return f"{{{_XLSX}}}{tag}"


def _read_part(archive: zipfile.ZipFile, name: str, total: list[int]) -> bytes:
    """读一个 zip 成员并累计解压体积，超过上限直接拒绝。"""
    info = archive.getinfo(name)
    total[0] += info.file_size
    if info.file_size > _MAX_XML_BYTES or total[0] > _MAX_TOTAL_XML_BYTES:
        raise ParseError("Excel 文件解压后过大（超过 64MB 单部件 / 256MB 总量上限）")
    return archive.read(info)


def _parse_xml(data: bytes, part: str) -> ET.Element:
    try:
        return ET.fromstring(data)
    except ET.ParseError as exc:
        raise ParseError(f"Excel 的 {part} 解析失败：{exc}") from exc


def _column_index(column: str) -> int:
    """列字母 → 列号（``A`` → 1、``AA`` → 27）。"""
    index = 0
    for char in column.upper():
        index = index * 26 + (ord(char) - ord("A") + 1)
    return index


def _column_letters(index: int) -> str:
    """列号 → 列字母（1 → A、27 → AA）。"""
    letters = ""
    while index > 0:
        index, remainder = divmod(index - 1, 26)
        letters = chr(ord("A") + remainder) + letters
    return letters


def _rich_text(node: ET.Element) -> str:
    """共享字符串/inlineStr 可能是多段富文本，把所有 ``t`` 按顺序拼起来。"""
    return "".join(child.text or "" for child in node.iter(_q("t")))


def _cell_text(cell: ET.Element, shared: list[str]) -> tuple[str, str] | None:
    """返回 ``(显示文本, 类型)``；空单元格返回 None。

    类型：``s`` 文本（共享字符串/内联/formula 字符串结果）、``n`` 数值、
    ``b`` 布尔、``d`` 日期、``e`` 错误值，导出时用来决定写回数字还是文本。
    """
    kind = cell.get("t") or "n"
    value_node = cell.find(_q("v"))
    value = (value_node.text or "") if value_node is not None else ""
    if kind == "inlineStr":
        inline = cell.find(_q("is"))
        text = _rich_text(inline) if inline is not None else ""
        return (text, "s") if text else None
    if kind == "s":
        if not value:
            return None
        try:
            text = shared[int(value)]
        except (ValueError, IndexError) as exc:
            raise ParseError("Excel 共享字符串索引越界（文件可能已损坏）") from exc
        return (text, "s") if text else None
    if kind == "str":  # formula 的字符串结果
        return (value, "s") if value else None
    if kind == "b":
        return ("TRUE" if value == "1" else "FALSE", "b") if value else None
    if kind in ("d", "e"):
        return (value, kind) if value else None
    # 缺省 / ``n``：数值单元格
    return (value, "n") if value else None


def _normalize_target(target: str) -> str:
    """关系文件里的目标路径 → zip 内路径（相对 ``xl/`` 或包根）。"""
    target = target.replace("\\", "/")
    if target.startswith("/"):
        return target.lstrip("/")
    return f"xl/{target}"


def _workbook_rels(
    archive: zipfile.ZipFile, total: list[int]
) -> tuple[dict[str, str], dict[str, tuple[str, str]]]:
    """解析 ``xl/_rels/workbook.xml.rels``。

    返回 ``(部件路径 → 关系类型, 关系 id → (部件路径, 关系类型))``；关系类型用来
    定位共享字符串表，关系 id 用来按工作簿里的顺序找到每张表的 XML。
    """
    rels_name = "xl/_rels/workbook.xml.rels"
    if rels_name not in archive.namelist():
        raise ParseError("不是有效的 .xlsx：缺少 xl/_rels/workbook.xml.rels")
    root = _parse_xml(_read_part(archive, rels_name, total), rels_name)
    by_part: dict[str, str] = {}
    by_id: dict[str, tuple[str, str]] = {}
    for rel in root.findall(f"{{{_PKG_REL}}}Relationship"):
        rel_id = rel.get("Id") or ""
        target = _normalize_target(rel.get("Target") or "")
        rel_type = rel.get("Type") or ""
        if not target:
            continue
        by_part[target] = rel_type
        if rel_id:
            by_id[rel_id] = (target, rel_type)
    return by_part, by_id


def _shared_strings(archive: zipfile.ZipFile, parts: dict[str, str], total: list[int]) -> list[str]:
    """读共享字符串表；没有这个部件时返回空表（全部是内联字符串的表）。"""
    name = "xl/sharedStrings.xml"
    if name not in archive.namelist():
        target = next((target for target, rel_type in parts.items() if rel_type == _SHARED_STRINGS_REL), None)
        if target is None:
            return []
        name = target
    if name not in archive.namelist():
        raise ParseError("不是有效的 .xlsx：共享字符串表缺失")
    root = _parse_xml(_read_part(archive, name, total), name)
    return [_rich_text(item) for item in root.findall(_q("si"))]


def _parse_sheet(root: ET.Element, shared: list[str]) -> tuple[list[tuple[int, list[tuple[str, str, int, str]]]], dict[int, str]]:
    """解析一张工作表。

    返回 ``(所有有内容的行, 表头)``：行是 ``(行号, [(坐标, 类型, 列号, 文本)])``，
    表头是 ``{列号: 表头文本}``。第一行有内容的行即表头行。
    """
    sheet_data = root.find(_q("sheetData"))
    if sheet_data is None:
        return [], {}
    rows: list[tuple[int, list[tuple[str, str, int, str]]]] = []
    previous_row = 0
    for row in sheet_data.findall(_q("row")):
        try:
            row_number = int(row.get("r") or 0) or previous_row + 1
        except ValueError as exc:
            raise ParseError(f"Excel 行号非法：{row.get('r')!r}") from exc
        previous_row = row_number
        cells: list[tuple[str, str, int, str]] = []
        previous_column = 0
        for cell in row.findall(_q("c")):
            reference = (cell.get("r") or "").strip()
            match = _REF_PATTERN.match(reference)
            if match:
                column = _column_index(match.group(1))
                row_number = int(match.group(2))
                previous_row = row_number
            else:
                column = previous_column + 1
                reference = f"{_column_letters(column)}{row_number}"
            previous_column = column
            parsed = _cell_text(cell, shared)
            if parsed is None:
                continue
            cells.append((reference, parsed[1], column, parsed[0]))
        if cells:
            rows.append((row_number, cells))
    if not rows:
        return [], {}
    headers = {column: text for _reference, _kind, column, text in rows[0][1]}
    return rows, headers


def parse_xlsx(data: bytes, source: str = "data.xlsx") -> Document:
    """解析 .xlsx：每张表按行扫单元格，表头行作为数据格的列名上下文（hint）。"""
    total = [0]
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise ParseError("不是有效的 .xlsx：文件不是 zip 结构（可能已损坏或加密）") from exc

    with archive:
        if "xl/workbook.xml" not in archive.namelist():
            raise ParseError("不是有效的 .xlsx：缺少 xl/workbook.xml（老式 .xls 请另存为 .xlsx）")
        parts, rel_by_id = _workbook_rels(archive, total)
        shared = _shared_strings(archive, parts, total)
        workbook = _parse_xml(_read_part(archive, "xl/workbook.xml", total), "xl/workbook.xml")
        sheets_node = workbook.find(_q("sheets"))
        if sheets_node is None:
            raise ParseError("不是有效的 .xlsx：工作簿里没有工作表清单")

        segments: list[Segment] = []
        sheet_meta: list[dict[str, object]] = []
        for sheet in sheets_node.findall(_q("sheet")):
            name = sheet.get("name") or f"Sheet{len(sheet_meta) + 1}"
            rel_id = sheet.get(f"{{{_REL}}}id") or ""
            target, rel_type = rel_by_id.get(rel_id, ("", ""))
            if not target or not rel_type.endswith("/worksheet"):
                raise ParseError(f"不是有效的 .xlsx：工作表「{name}」缺少文件")
            if target not in archive.namelist():
                raise ParseError(f"不是有效的 .xlsx：缺少工作表文件 {target}")
            root = _parse_xml(_read_part(archive, target, total), target)
            rows, headers = _parse_sheet(root, shared)
            if not rows:
                continue
            header_row = rows[0][0]
            rows_meta: list[tuple[int, list[tuple[str, str, object]]]] = []
            for row_number, cells in rows:
                cells_meta: list[tuple[str, str, object]] = []
                for reference, kind, column, text in cells:
                    if row_number == header_row:
                        # 表头单元格保留原文，不参与检测
                        cells_meta.append((reference, kind, text))
                        continue
                    segments.append(
                        Segment(path=f"{name}!{reference}", text=text, hint=headers.get(column, "").strip())
                    )
                    cells_meta.append((reference, kind, len(segments) - 1))
                rows_meta.append((row_number, cells_meta))
            sheet_meta.append({"name": name, "rows": rows_meta})

    if not segments:
        raise ParseError("Excel 表格里没有可检测的文本（空表格，或只有表头行）")
    return Document(
        source=source,
        content_type="xlsx",
        segments=segments,
        raw="\n".join(segment.text for segment in segments),
        meta={"xlsx": {"sheets": sheet_meta}},
    )
