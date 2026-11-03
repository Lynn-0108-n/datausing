"""测试用最小 .xlsx 构造器：不参与用例收集，供多个测试模块复用。

只写解析器真正需要的部件（workbook / rels / 工作表 / 可选共享字符串），
每个参数都是可见的 XML，方便用例精确断言"读到了哪些格"。
"""

from __future__ import annotations

import io
import zipfile

XLSX_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PKG_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"


def text_cell(reference: str, text: str) -> str:
    """内联字符串单元格。"""
    return f'<c r="{reference}" t="inlineStr"><is><t xml:space="preserve">{text}</t></is></c>'


def shared_cell(reference: str, index: int) -> str:
    """共享字符串单元格（``index`` 指向 shared_strings 列表）。"""
    return f'<c r="{reference}" t="s"><v>{index}</v></c>'


def number_cell(reference: str, value: str) -> str:
    """数值单元格（Excel 里手机号常存成数字）。"""
    return f'<c r="{reference}"><v>{value}</v></c>'


def row(number: int, *cells: str) -> str:
    """一行：``row(2, text_cell("A2", "张三"), number_cell("B2", "13800138000"))``。"""
    return f'<row r="{number}">{"".join(cells)}</row>'


def make_xlsx(sheets: list[tuple[str, str]], shared_strings: list[str] | None = None) -> bytes:
    """构造最小 .xlsx：``sheets`` 是 ``(工作表名, sheetData 内的 row XML)``。"""
    sheet_nodes = "".join(
        f'<sheet name="{name}" sheetId="{index}" r:id="rId{index}"/>'
        for index, (name, _rows) in enumerate(sheets, start=1)
    )
    relationships = [
        f'<Relationship Id="rId{index}" Type="{REL_NS}/worksheet" '
        f'Target="worksheets/sheet{index}.xml"/>'
        for index in range(1, len(sheets) + 1)
    ]
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(
            "xl/workbook.xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            f'<workbook xmlns="{XLSX_NS}" xmlns:r="{REL_NS}"><sheets>{sheet_nodes}</sheets></workbook>',
        )
        for index, (_name, rows) in enumerate(sheets, start=1):
            archive.writestr(
                f"xl/worksheets/sheet{index}.xml",
                '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                f'<worksheet xmlns="{XLSX_NS}"><sheetData>{rows}</sheetData></worksheet>',
            )
        if shared_strings is not None:
            items = "".join(f"<si><t>{text}</t></si>" for text in shared_strings)
            archive.writestr(
                "xl/sharedStrings.xml",
                '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                f'<sst xmlns="{XLSX_NS}">{items}</sst>',
            )
            relationships.append(
                f'<Relationship Id="rId{len(sheets) + 1}" '
                f'Type="{REL_NS}/sharedStrings" Target="sharedStrings.xml"/>'
            )
        archive.writestr(
            "xl/_rels/workbook.xml.rels",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            f'<Relationships xmlns="{PKG_REL_NS}">{"".join(relationships)}</Relationships>',
        )
    return buffer.getvalue()
