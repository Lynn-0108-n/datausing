"""脱敏导出（纯函数层）测试：策略层 + ``tasks/document_export.build_masked_bytes``。

不经过 HTTP：直接构造解析后的 ``Document`` 与真实流水线报告，验证五种输出格式的
"重建 + 零残留"，以及六种脱敏策略的确定性 / 可逆性 / 边界规则。
"""

from __future__ import annotations

import csv
import io
import json
import zipfile

import pytest

from app.identify.masking import (
    DEFAULT_STRATEGY,
    MASK_STRATEGIES,
    decrypt_encrypted_text,
    encrypt_text,
    mask_spans_with_strategy,
    mask_text_strategy,
    mask_value_strategy,
    normalize_strategy,
)
from app.identify.pipeline import DetectOptions, DetectionPipeline, DocumentReport
from app.identify.types import EntityType, Hit
from app.parsers.base import Document, Segment
from app.parsers.registry import parse_csv, parse_docx, parse_json, parse_sql, parse_xlsx
from app.parsers.text import parse_text as parse_plain_text
from app.tasks.document_export import EXPORT_FORMATS, build_masked_bytes

from xlsx_fixtures import make_xlsx, number_cell, row, text_cell

SECRET = "flowmask-doc-export-secret"
PHONE = "13812345678"
ID_CARD = "11010519491231002X"
EMAIL = "zhangsan@example.com"
NAME = "张三"

TEXT = f"姓名：{NAME}，手机：{PHONE}，身份证：{ID_CARD}，邮箱：{EMAIL}。"


def _analyze(document: Document, *, enable_ner: bool = False):
    """真实流水线分析；示例数据必须被识别出来，否则"零残留"断言会失去意义。"""
    pipeline = DetectionPipeline()
    report = pipeline.analyze_document(
        document,
        DetectOptions(enable_l2=True, enable_l3=True, enable_ner=enable_ner),
    )
    assert report.all_findings, "示例数据没有被识别，测试数据失效"
    return report


def _assert_no_residual(text: str, *values: str) -> None:
    for value in values:
        assert value and value not in text, f"导出内容仍含原始敏感值：{value}"


# ---- 策略元数据 -------------------------------------------------------------


def test_mask_strategies_metadata() -> None:
    assert list(MASK_STRATEGIES) == ["block", "partial", "hash", "generalize", "pseudonym", "encrypt"]
    for key, meta in MASK_STRATEGIES.items():
        assert meta["key"] == key
        assert set(meta) == {"key", "label", "description", "reversible", "examples"}
        assert meta["label"] and meta["description"]
        assert meta["reversible"] is (key == "encrypt")
        assert {example["category"] for example in meta["examples"]} == {"phone", "id_card", "email"}
        for example in meta["examples"]:
            assert set(example) == {"category", "raw", "masked"}
            assert example["raw"] and example["masked"]
    assert DEFAULT_STRATEGY == "partial"


def test_normalize_strategy() -> None:
    assert normalize_strategy(None) == DEFAULT_STRATEGY
    assert normalize_strategy("") == DEFAULT_STRATEGY
    assert normalize_strategy("unknown") == DEFAULT_STRATEGY
    assert normalize_strategy(" HASH ") == "hash"
    assert normalize_strategy("encrypt") == "encrypt"


# ---- 单值策略 ---------------------------------------------------------------


def test_block_strategy_uses_entity_label() -> None:
    assert mask_value_strategy(PHONE, EntityType.PHONE, "block") == "[已屏蔽:手机号]"
    assert mask_value_strategy(ID_CARD, "id_card", "block") == "[已屏蔽:身份证号]"
    assert mask_value_strategy(EMAIL, "email", "block") == "[已屏蔽:邮箱]"
    # 无法识别的类别按"其他"处理，不把原始值带进占位符
    assert mask_value_strategy("abc", "unknown_type", "block") == "[已屏蔽:其他]"


def test_partial_strategy_rules() -> None:
    assert mask_value_strategy(PHONE, "phone", "partial") == "1******"
    assert mask_value_strategy(EMAIL, "email", "partial") == "z******"
    assert mask_value_strategy("a", "phone", "partial") == "*"
    # 短值与长值都复用旧 _stars 的封顶口径
    assert mask_value_strategy("12", "phone", "partial") == "1*"
    assert mask_value_strategy("1234567890", "phone", "partial") == "1******"
    assert mask_value_strategy("", "phone", "partial") == ""


def test_hash_strategy_is_deterministic_and_secret_sensitive() -> None:
    first = mask_value_strategy(PHONE, "phone", "hash", SECRET)
    second = mask_value_strategy(PHONE, "phone", "hash", SECRET)
    other = mask_value_strategy(PHONE, "phone", "hash", "another-secret")
    assert first == second
    assert first.startswith("h_") and len(first) == 18
    assert first != other
    # 不同值不同指纹
    assert mask_value_strategy(ID_CARD, "id_card", "hash", SECRET) != first
    # 空 secret 用空串盐，仍然是确定性的
    assert mask_value_strategy(PHONE, "phone", "hash", "") == mask_value_strategy(PHONE, "phone", "hash", "")


def test_pseudonym_strategy_rules() -> None:
    first = mask_value_strategy(PHONE, "phone", "pseudonym", SECRET)
    assert first == mask_value_strategy(PHONE, "phone", "pseudonym", SECRET)
    assert first.startswith("PHONE_") and len(first) == len("PHONE_") + 8
    assert mask_value_strategy(EMAIL, "email", "pseudonym", SECRET).startswith("EMAIL_")
    assert first != mask_value_strategy(PHONE, "phone", "pseudonym", "another-secret")


@pytest.mark.parametrize(
    ("value", "entity", "expected"),
    [
        (ID_CARD, "id_card", "110105********"),
        (PHONE, "phone", "138****78"),
        ("123456", "phone", "12****"),
        ("6222020200112233445", "bank_card", "****3445"),
        (EMAIL, "email", "***@example.com"),
        (NAME, "name", "张*"),
        ("北京市朝阳区建国路1号", "address", "北京市***"),
        ("10.0.0.1", "ip", "[IP地址]"),
    ],
)
def test_generalize_strategy_rules(value: str, entity: str, expected: str) -> None:
    assert mask_value_strategy(value, entity, "generalize", SECRET) == expected


def test_encrypt_strategy_roundtrip_and_errors() -> None:
    token = mask_value_strategy(PHONE, "phone", "encrypt", SECRET)
    assert token.startswith("enc:")
    # 每次加密随机 nonce，但都能还原
    assert token != mask_value_strategy(PHONE, "phone", "encrypt", SECRET)
    restored, count = decrypt_encrypted_text(f"号码 {token} 结束", SECRET)
    assert (restored, count) == (f"号码 {PHONE} 结束", 1)
    with pytest.raises(ValueError, match="脱敏导出密钥未配置"):
        encrypt_text(PHONE, "")
    with pytest.raises(ValueError, match="脱敏导出密钥未配置"):
        mask_value_strategy(PHONE, "phone", "encrypt", "")
    with pytest.raises(ValueError, match="脱敏导出密钥未配置"):
        decrypt_encrypted_text(token, "")


def test_decrypt_encrypted_text_edge_cases() -> None:
    # 没有密文片段：原样返回、计数为 0
    assert decrypt_encrypted_text("没有密文的普通文本", SECRET) == ("没有密文的普通文本", 0)
    # 非法 base64 / 太短的片段
    with pytest.raises(ValueError, match="密文片段无效"):
        decrypt_encrypted_text("enc:abc", SECRET)
    # 只有 nonce、没有密文的片段（能解码但长度不足）
    with pytest.raises(ValueError, match="密文片段无效"):
        decrypt_encrypted_text("enc:" + "A" * 22, SECRET)


# ---- spans / hits 级脱敏 ----------------------------------------------------


def test_mask_spans_with_strategy_handles_offsets_and_order() -> None:
    text = f"姓名：{NAME} 手机：{PHONE}"
    spans = [
        {"start": text.index(NAME), "end": text.index(NAME) + len(NAME), "type": "name"},
        {"start": text.index(PHONE), "end": text.index(PHONE) + len(PHONE), "type": "phone"},
        {"start": -1, "end": 5, "type": "phone"},  # 非法区间被忽略
        {"start": 3, "end": 3, "type": "phone"},  # 空区间被忽略
    ]
    masked = mask_spans_with_strategy(text, spans, "hash", SECRET)
    assert NAME not in masked and PHONE not in masked
    assert masked.startswith("姓名：h_") and " 手机：h_" in masked

    # 带属性的对象（如 Hit）同样支持
    hits = [Hit(type=EntityType.PHONE, start=3, end=14, value=PHONE, weight=1.0, confidence=1.0, rule="mobile")]
    assert mask_text_strategy("号码：13812345678", hits, "block") == "号码：[已屏蔽:手机号]"
    # 敏感词表命中（kw- 规则）不参与替换
    keyword_hit = Hit(type=EntityType.PHONE, start=0, end=3, value="手机", weight=1.0, confidence=1.0, rule="kw-手机")
    assert mask_text_strategy("手机号码", [keyword_hit], "block") == "手机号码"


# ---- 各格式导出 -------------------------------------------------------------


def test_txt_export_has_no_residual() -> None:
    document = parse_plain_text(TEXT, source="report.txt")
    report = _analyze(document, enable_ner=True)
    content, media_type, ext = build_masked_bytes(document, report, strategy="block", fmt="txt", secret=SECRET)
    text = content.decode("utf-8")
    assert (media_type, ext) == ("text/plain;charset=utf-8", "txt")
    _assert_no_residual(text, NAME, PHONE, ID_CARD, EMAIL)
    assert "[已屏蔽:手机号]" in text
    # 一段文档一段文本
    assert text.count("\n") == len(document.segments) - 1


def test_json_export_keeps_structure_without_residual() -> None:
    raw = json.dumps(
        {
            "user": {"name": NAME, "phone": PHONE, "email": EMAIL},
            "history": [{"phone": PHONE, "id_card": ID_CARD}],
        },
        ensure_ascii=False,
    )
    document = parse_json(raw, source="data.json")
    report = _analyze(document)
    content, media_type, ext = build_masked_bytes(document, report, strategy="partial", fmt="json", secret=SECRET)
    text = content.decode("utf-8")
    assert (media_type, ext) == ("application/json;charset=utf-8", "json")
    _assert_no_residual(text, PHONE, ID_CARD, EMAIL)
    data = json.loads(text)
    assert set(data) == {"user", "history"}
    assert data["user"]["phone"] == "1******"
    assert data["history"][0]["id_card"] == "1******"


def test_json_export_handles_root_string() -> None:
    # 根节点就是字符串时路径为 "$"，没有可下钻的 token，也应正常替换
    document = parse_json(f'"{PHONE}"', source="root.json")
    report = _analyze(document)
    content, _media_type, ext = build_masked_bytes(document, report, strategy="partial", fmt="json", secret=SECRET)
    assert ext == "json"
    assert PHONE not in content.decode("utf-8")
    assert json.loads(content.decode("utf-8")) == "1******"


def test_csv_export_keeps_shape_and_parses() -> None:
    raw = "姓名,手机号,邮箱\n张三,13812345678,zhangsan@example.com\n李四,13900001111,li@example.com\n"
    document = parse_csv(raw, source="data.csv")
    report = _analyze(document)
    content, media_type, ext = build_masked_bytes(document, report, strategy="generalize", fmt="csv", secret=SECRET)
    text = content.decode("utf-8")
    assert (media_type, ext) == ("text/csv;charset=utf-8", "csv")
    _assert_no_residual(text, PHONE, EMAIL)
    rows = list(csv.reader(io.StringIO(text)))
    assert len(rows) == 3
    assert all(len(row) == 3 for row in rows)
    assert rows[0] == ["姓名", "手机号", "邮箱"]
    assert rows[1][1] == "138****78"
    assert rows[2][1] == "139****11"


def test_sql_export_keeps_comments_and_statements() -> None:
    raw = (
        "-- 客户数据（注释必须保留）\n"
        "INSERT INTO users (name, phone) VALUES ('张三', '13812345678');\n"
        "UPDATE users SET phone = '13900001111' WHERE id = 1;\n"
    )
    document = parse_sql(raw, source="data.sql")
    report = _analyze(document)
    content, media_type, ext = build_masked_bytes(document, report, strategy="partial", fmt="sql", secret=SECRET)
    text = content.decode("utf-8")
    assert (media_type, ext) == ("application/sql;charset=utf-8", "sql")
    _assert_no_residual(text, PHONE, "13900001111")
    assert text.startswith("-- 客户数据（注释必须保留）")
    assert text.count(";") == 2
    assert "INSERT INTO users" in text and "UPDATE users" in text
    # 结构完整：字面量仍被引号包住
    assert "'1******'" in text


def test_docx_export_is_parseable_and_clean() -> None:
    document = parse_plain_text(TEXT, source="report.txt")
    report = _analyze(document, enable_ner=True)
    content, media_type, ext = build_masked_bytes(document, report, strategy="block", fmt="docx", secret=SECRET)
    assert ext == "docx"
    assert media_type == "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    names = set(zipfile.ZipFile(io.BytesIO(content)).namelist())
    assert {"[Content_Types].xml", "_rels/.rels", "word/document.xml"} <= names
    # 能被本仓 docx 解析器反向解析出非空段落
    parsed = parse_docx(content, source="export.docx")
    assert parsed.segments
    joined = "\n".join(segment.text for segment in parsed.segments)
    assert joined.strip()
    _assert_no_residual(joined, NAME, PHONE, ID_CARD, EMAIL)


def test_xlsx_export_is_parseable_and_clean() -> None:
    rows = row(
        1,
        text_cell("A1", "姓名"),
        text_cell("B1", "手机号"),
        text_cell("C1", "身份证"),
        text_cell("D1", "邮箱"),
    ) + row(2, text_cell("A2", NAME), number_cell("B2", PHONE), text_cell("C2", ID_CARD), text_cell("D2", EMAIL))
    document = parse_xlsx(make_xlsx([("客户表", rows)]), source="客户表.xlsx")
    report = _analyze(document)
    content, media_type, ext = build_masked_bytes(document, report, strategy="block", fmt="auto", secret=SECRET)
    assert ext == "xlsx"
    assert media_type == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        names = set(archive.namelist())
        worksheet = archive.read("xl/worksheets/sheet1.xml").decode("utf-8")
    assert {
        "[Content_Types].xml",
        "_rels/.rels",
        "xl/workbook.xml",
        "xl/_rels/workbook.xml.rels",
        "xl/worksheets/sheet1.xml",
    } <= names
    # 表头原文保留，敏感值零残留
    assert "姓名" in worksheet and "手机号" in worksheet
    _assert_no_residual(worksheet, PHONE, ID_CARD, EMAIL)
    # 能被本仓解析器反解析，脱敏值仍落在原单元格
    parsed = parse_xlsx(content, source="export.xlsx")
    assert [segment.path for segment in parsed.segments] == ["客户表!A2", "客户表!B2", "客户表!C2", "客户表!D2"]
    masked_by_hint = {segment.hint: segment.text for segment in parsed.segments}
    assert masked_by_hint["手机号"] and masked_by_hint["手机号"] != PHONE
    assert masked_by_hint["身份证"] and masked_by_hint["身份证"] != ID_CARD


def test_xlsx_only_exports_xlsx_or_txt() -> None:
    rows = row(1, text_cell("A1", "手机号")) + row(2, text_cell("A2", PHONE))
    document = parse_xlsx(make_xlsx([("表1", rows)]), source="a.xlsx")
    report = _analyze(document)
    with pytest.raises(ValueError, match="Excel 仅支持导出 xlsx 或 txt"):
        build_masked_bytes(document, report, strategy="partial", fmt="json", secret=SECRET)
    content, media_type, ext = build_masked_bytes(document, report, strategy="partial", fmt="txt", secret=SECRET)
    assert (media_type, ext) == ("text/plain;charset=utf-8", "txt")
    _assert_no_residual(content.decode("utf-8"), PHONE)


def test_encrypt_export_roundtrip_via_decrypt() -> None:
    document = parse_plain_text(f"手机：{PHONE}，邮箱：{EMAIL}", source="secret.txt")
    report = _analyze(document)
    content, _, _ = build_masked_bytes(document, report, strategy="encrypt", fmt="txt", secret=SECRET)
    text = content.decode("utf-8")
    assert "enc:" in text
    _assert_no_residual(text, PHONE, EMAIL)
    restored, replaced = decrypt_encrypted_text(text, SECRET)
    assert replaced >= 2
    assert PHONE in restored and EMAIL in restored
    assert restored == document.segments[0].text


def test_encrypt_export_requires_secret() -> None:
    document = parse_plain_text(TEXT, source="report.txt")
    report = _analyze(document)
    with pytest.raises(ValueError, match="脱敏导出密钥未配置"):
        build_masked_bytes(document, report, strategy="encrypt", fmt="txt", secret="")


def test_hash_export_deterministic_and_secret_sensitive() -> None:
    document = parse_plain_text(f"手机：{PHONE}", source="hash.txt")
    report = _analyze(document)
    first, _, _ = build_masked_bytes(document, report, strategy="hash", fmt="txt", secret=SECRET)
    second, _, _ = build_masked_bytes(document, report, strategy="hash", fmt="txt", secret=SECRET)
    other, _, _ = build_masked_bytes(document, report, strategy="hash", fmt="txt", secret="another-secret")
    assert first == second
    assert first != other
    assert b"h_" in first
    _assert_no_residual(first.decode("utf-8"), PHONE)


# ---- 格式归一化 -------------------------------------------------------------


def test_auto_fmt_follows_document_type() -> None:
    text_document = parse_plain_text(TEXT, source="a.txt")
    text_report = _analyze(text_document, enable_ner=True)
    assert build_masked_bytes(text_document, text_report, strategy="block", fmt="auto", secret=SECRET)[2] == "txt"

    json_document = parse_json('{"phone": "13812345678"}', source="a.json")
    json_report = _analyze(json_document)
    assert build_masked_bytes(json_document, json_report, strategy="partial", fmt="auto", secret=SECRET)[2] == "json"

    csv_document = parse_csv("手机号\n13812345678\n", source="a.csv")
    csv_report = _analyze(csv_document)
    assert build_masked_bytes(csv_document, csv_report, strategy="partial", fmt="auto", secret=SECRET)[2] == "csv"

    sql_document = parse_sql("INSERT INTO t (phone) VALUES ('13812345678');", source="a.sql")
    sql_report = _analyze(sql_document)
    assert build_masked_bytes(sql_document, sql_report, strategy="partial", fmt="auto", secret=SECRET)[2] == "sql"

    xlsx_document = parse_xlsx(
        make_xlsx([("表1", row(1, text_cell("A1", "手机号")) + row(2, text_cell("A2", PHONE)))]),
        source="a.xlsx",
    )
    xlsx_report = _analyze(xlsx_document)
    assert build_masked_bytes(xlsx_document, xlsx_report, strategy="partial", fmt="auto", secret=SECRET)[2] == "xlsx"


def test_pdf_only_exports_txt() -> None:
    document = Document(
        source="scan.pdf",
        content_type="pdf",
        segments=[Segment(path="page1", text=f"手机：{PHONE}", offset=0)],
        raw=f"手机：{PHONE}",
    )
    # 空报告：没有命中时导出的就是原文（只测格式分派场景）
    report = DocumentReport(source="scan.pdf", content_type="pdf", segments=1)
    content, _media_type, ext = build_masked_bytes(document, report, strategy="partial", fmt="auto", secret=SECRET)
    assert ext == "txt" and content.decode("utf-8").strip()
    with pytest.raises(ValueError, match="PDF 仅支持导出 txt"):
        build_masked_bytes(document, report, strategy="partial", fmt="json", secret=SECRET)


def test_unknown_fmt_raises_value_error() -> None:
    document = parse_plain_text(TEXT, source="report.txt")
    report = _analyze(document)
    assert "xml" not in EXPORT_FORMATS
    with pytest.raises(ValueError, match="不支持的输出格式"):
        build_masked_bytes(document, report, strategy="partial", fmt="xml", secret=SECRET)
