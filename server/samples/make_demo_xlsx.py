"""生成演示用的 Excel 工作簿：`python -m samples.make_demo_xlsx`（也可直接 `python samples/make_demo_xlsx.py`）。

单张工作表 `客户表`：首行是表头（姓名 / 手机号 / 邮箱 / 备注），下面是两条客户数据，
其中手机号以数值形式存（真实 Excel 里很常见，用来演示「数值单元格也会被检测」）。

产物 `samples/demo-sheet.xlsx` 已随仓库提交，demo.ps1 / demo.sh 直接用它做
「文件检测 + 脱敏导出」演示；改了数据重新跑一次本脚本即可。

只用标准库 zipfile 手写最小 OOXML，不需要 openpyxl。
"""

from __future__ import annotations

import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

_BOOK_NAME = "客户表"

_CONTENT_TYPES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
</Types>"""

_ROOT_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>"""

_WORKBOOK = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
<sheets><sheet name="{name}" sheetId="1" r:id="rId1"/></sheets>
</workbook>"""

_WORKBOOK_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
</Relationships>"""

_HEADERS = ("姓名", "手机号", "邮箱", "备注")
_ROWS = (
    ("张三", 13812345678, "zhangsan@example.com", "普通客户"),
    ("李四", 13900001111, "lisi@example.org", "重点客户"),
)


def _cell(column: str, row: int, value: object) -> str:
    ref = f"{column}{row}"
    if isinstance(value, (int, float)):
        return f'<c r="{ref}"><v>{value}</v></c>'
    return f'<c r="{ref}" t="inlineStr"><is><t xml:space="preserve">{escape(str(value))}</t></is></c>'


def _sheet_xml() -> str:
    columns = "ABCD"
    lines = [
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>',
    ]
    lines.append(
        '<row r="1">' + "".join(_cell(col, 1, text) for col, text in zip(columns, _HEADERS)) + "</row>"
    )
    for offset, row in enumerate(_ROWS, start=2):
        lines.append(
            f'<row r="{offset}">'
            + "".join(_cell(col, offset, value) for col, value in zip(columns, row))
            + "</row>"
        )
    lines.append("</sheetData></worksheet>")
    return "".join(lines)


def build_demo_xlsx(path: Path) -> Path:
    """把演示工作簿写到 `path`，返回该路径。"""
    parts = {
        "[Content_Types].xml": _CONTENT_TYPES,
        "_rels/.rels": _ROOT_RELS,
        "xl/workbook.xml": _WORKBOOK.format(name=escape(_BOOK_NAME, {'"': "&quot;"})),
        "xl/_rels/workbook.xml.rels": _WORKBOOK_RELS,
        "xl/worksheets/sheet1.xml": _sheet_xml(),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in parts.items():
            archive.writestr(name, content.encode("utf-8"))
    return path


def main() -> None:
    target = build_demo_xlsx(Path(__file__).with_name("demo-sheet.xlsx"))
    print(f"已生成 {target}（{target.stat().st_size} 字节）")


if __name__ == "__main__":
    main()
