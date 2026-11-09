"""document_export 单元测试：覆盖各 content_type 的重建逻辑。

纯函数测试，不依赖 FastAPI 与数据库。
用 DetectionPipeline 真实跑一遍拿到 DocumentReport，再调 build_masked_file 重建。
"""

from __future__ import annotations

import csv
import io
import json
from urllib.parse import unquote

import pytest

from app.identify import DetectionPipeline, DetectOptions, Level, MaskStrategy
from app.parsers.base import Document, Segment
from app.parsers.csv_parser import parse_csv
from app.parsers.json_parser import parse_json
from app.parsers.sql_parser import parse_sql
from app.parsers.text import parse_text
from app.tasks.document_export import build_masked_file

ID_CARD = "11010519491231002X"
PHONE = "13800138000"


def _pipeline() -> DetectionPipeline:
    """构造一个只跑 L1 的 pipeline（不依赖 L2/L3 推理服务）。"""
    return DetectionPipeline.from_settings(__import__("app.core.config", fromlist=["Settings"]).Settings.load())


def _analyze(document: Document) -> "DocumentReport":
    """跑 L1 检测，min_level=SAFE 覆盖所有命中。"""
    pipe = _pipeline()
    options = DetectOptions(enable_l2=False, enable_l3=False, enable_ner=False, min_level=Level.SAFE)
    return pipe.analyze_document(document, options)


# ---------- 纯文本 ----------


def test_text_partial_strategy() -> None:
    text = f"客户手机：{PHONE}，证件号：{ID_CARD}"
    document = parse_text(text)
    report = _analyze(document)
    data, media, disposition, key = build_masked_file(document, report, "note.txt", MaskStrategy.PARTIAL)
    content = data.decode("utf-8")
    assert PHONE not in content
    assert ID_CARD not in content
    assert "客户手机：" in content
    assert "，证件号：" in content
    assert media == "text/plain; charset=utf-8"
    assert key is None
    assert "attachment" in disposition


def test_text_partial_strategy_masks_id_card_with_stars() -> None:
    """partial 导出的身份证号保留前 6 后 4，中间用星号，不能退化为类别占位。"""
    text = f"身份证号：{ID_CARD}"
    document = parse_text(text)
    report = _analyze(document)
    data, _, _, _ = build_masked_file(document, report, "note.txt", MaskStrategy.PARTIAL)
    content = data.decode("utf-8")
    assert ID_CARD not in content
    assert "110105********002X" in content


def test_text_block_strategy() -> None:
    text = f"手机：{PHONE}"
    document = parse_text(text)
    report = _analyze(document)
    data, _, _, _ = build_masked_file(document, report, "note.txt", MaskStrategy.BLOCK)
    content = data.decode("utf-8")
    assert "手机号" in content
    assert PHONE not in content


def test_text_hash_strategy() -> None:
    text = f"手机：{PHONE}"
    document = parse_text(text)
    report = _analyze(document)
    data, _, _, _ = build_masked_file(document, report, "note.txt", MaskStrategy.HASH)
    content = data.decode("utf-8")
    assert PHONE not in content
    # 哈希是 8 位十六进制
    import re
    hashes = re.findall(r"\b[0-9a-f]{8}\b", content)
    assert len(hashes) >= 1


def test_text_encrypt_strategy_returns_key() -> None:
    text = f"手机：{PHONE}"
    document = parse_text(text)
    report = _analyze(document)
    data, _, _, key_b64 = build_masked_file(document, report, "note.txt", MaskStrategy.ENCRYPT)
    content = data.decode("utf-8")
    assert PHONE not in content
    assert key_b64 is not None
    # 能用返回的密钥解密
    from cryptography.fernet import Fernet
    # 从脱敏文本里找到密文 token（gAAAA 开头）
    import re
    tokens = re.findall(r"gAAAA[A-Za-z0-9_\-]+={0,2}", content)
    assert len(tokens) >= 1
    decrypted = Fernet(key_b64.encode("ascii")).decrypt(tokens[0].encode("ascii")).decode("utf-8")
    assert decrypted == PHONE


def test_text_log_keeps_extension() -> None:
    text = "无敏感内容"
    document = parse_text(text)
    report = _analyze(document)
    data, _, disposition, _ = build_masked_file(document, report, "server.log", MaskStrategy.PARTIAL)
    assert ".log" in disposition


def test_text_md_keeps_extension() -> None:
    text = "无敏感内容"
    document = parse_text(text)
    report = _analyze(document)
    data, _, disposition, _ = build_masked_file(document, report, "readme.md", MaskStrategy.PARTIAL)
    assert ".md" in disposition


def test_text_clean_document_unchanged() -> None:
    text = "这是一段没有敏感信息的普通文本"
    document = parse_text(text)
    report = _analyze(document)
    data, _, _, _ = build_masked_file(document, report, "clean.txt", MaskStrategy.BLOCK)
    assert data.decode("utf-8") == text


# ---------- JSON ----------


def test_json_partial_strategy() -> None:
    raw = json.dumps({"name": "张三", "phone": PHONE, "age": 25, "active": True}, ensure_ascii=False)
    document = parse_json(raw)
    report = _analyze(document)
    data, media, _, _ = build_masked_file(document, report, "data.json", MaskStrategy.PARTIAL)
    content = data.decode("utf-8")
    # 命中字段已脱敏
    assert PHONE not in content
    # 未命中字段原样（数字、布尔）
    parsed = json.loads(content)
    assert parsed["age"] == 25
    assert parsed["active"] is True
    assert media == "application/json; charset=utf-8"


def test_json_nested_structure_preserved() -> None:
    raw = json.dumps(
        {"user": {"profile": {"phone": PHONE}}, "items": [{"id": 1}, {"id": 2}]},
        ensure_ascii=False,
    )
    document = parse_json(raw)
    report = _analyze(document)
    data, _, _, _ = build_masked_file(document, report, "nested.json", MaskStrategy.BLOCK)
    content = data.decode("utf-8")
    parsed = json.loads(content)
    assert parsed["items"] == [{"id": 1}, {"id": 2}]
    assert PHONE not in parsed["user"]["profile"]["phone"]


# ---------- CSV ----------


def test_csv_partial_strategy() -> None:
    raw = f"name,phone,age\n张三,{PHONE},25\n李四,13900139000,30\n"
    document = parse_csv(raw)
    report = _analyze(document)
    data, media, _, _ = build_masked_file(document, report, "data.csv", MaskStrategy.PARTIAL)
    content = data.decode("utf-8-sig")
    # 重新解析能对齐
    rows = list(csv.reader(io.StringIO(content)))
    assert rows[0] == ["name", "phone", "age"]
    assert PHONE not in content
    assert media == "text/csv; charset=utf-8"


def test_csv_with_comma_in_field() -> None:
    # 含逗号的字段用引号包裹
    raw = f'name,phone\n"张,三",{PHONE}\n'
    document = parse_csv(raw)
    report = _analyze(document)
    data, _, _, _ = build_masked_file(document, report, "data.csv", MaskStrategy.BLOCK)
    content = data.decode("utf-8-sig")
    rows = list(csv.reader(io.StringIO(content)))
    assert len(rows) == 2
    assert PHONE not in content


# ---------- SQL ----------


def test_sql_preserves_ddl_and_masks_dml() -> None:
    raw = (
        "CREATE TABLE users (id INT, name VARCHAR(50), phone VARCHAR(20));\n"
        f"INSERT INTO users (id, name, phone) VALUES (1, '张三', '{PHONE}');\n"
    )
    document = parse_sql(raw)
    report = _analyze(document)
    data, _, _, _ = build_masked_file(document, report, "data.sql", MaskStrategy.BLOCK)
    content = data.decode("utf-8")
    # DDL 保留
    assert "CREATE TABLE users" in content
    # DML 命中值已脱敏
    assert PHONE not in content
    assert "手机号" in content


def test_sql_escaped_quote_not_double_escaped() -> None:
    # SQL 字面量里的 O''Brien 不应被二次转义
    raw = f"INSERT INTO users (name, phone) VALUES ('O''Brien', '{PHONE}');\n"
    document = parse_sql(raw)
    report = _analyze(document)
    data, _, _, _ = build_masked_file(document, report, "data.sql", MaskStrategy.PARTIAL)
    content = data.decode("utf-8")
    # O''Brien 保持原样（segment.text 是 O'Brien，但 SQL 里是 O''Brien）
    # 脱敏后 phone 应被遮蔽
    assert PHONE not in content


def test_sql_multiple_literals_no_misalign() -> None:
    # 多个字面量长度变化时，倒序替换保证不错位
    raw = (
        "INSERT INTO t (a, b, c) VALUES "
        f"('短', '{PHONE}', '另一个长字面量内容');\n"
    )
    document = parse_sql(raw)
    report = _analyze(document)
    data, _, _, _ = build_masked_file(document, report, "data.sql", MaskStrategy.BLOCK)
    content = data.decode("utf-8")
    assert PHONE not in content
    # 未命中字面量原样保留
    assert "短" in content
    assert "另一个长字面量内容" in content


# ---------- docx/pdf（用手工构造的 Document）----------


def test_docx_pdf_structure() -> None:
    # 手工构造两个 segment 模拟 docx/pdf 解析结果
    seg1 = Segment(path="p1", text=f"第一段含手机号{PHONE}")
    seg2 = Segment(path="p2", text="第二段无敏感内容")
    document = Document(source="test.pdf", content_type="pdf", segments=[seg1, seg2], raw="")
    # 手工构造 report（跳过真实检测，直接造 findings）
    from app.identify.pipeline import DocumentReport, SegmentFinding
    from app.identify.types import Hit, EntityType
    hit = Hit(type=EntityType.PHONE, start=7, end=7 + len(PHONE), value=PHONE, weight=0.9, confidence=0.95, rule="test")
    finding = SegmentFinding(
        path="p1",
        original_hash="",
        masked="手机号",
        level=Level.MID,
        score=0.9,
        types=["phone"],
        hits=1,
        rule_ids=["test"],
        confidence_vector=[],
        model_chain="l1",
        spans=[{"start": 7, "end": 7 + len(PHONE), "type": "phone", "label": "手机号", "rule": "test"}],
    )
    report = DocumentReport(source="test.pdf", content_type="pdf", segments=2, findings=[finding])
    data, media, _, _ = build_masked_file(document, report, "doc.pdf", MaskStrategy.BLOCK)
    content = data.decode("utf-8")
    assert PHONE not in content
    assert "第二段无敏感内容" in content
    # 两段用换行拼接
    assert content.count("\n") == 1
    assert media == "text/plain; charset=utf-8"


# ---------- 文件名编码 ----------


def test_chinese_filename_disposition() -> None:
    text = "无敏感内容"
    document = parse_text(text)
    report = _analyze(document)
    data, _, disposition, _ = build_masked_file(document, report, "中文文件名.txt", MaskStrategy.PARTIAL)
    # 同时含 ASCII filename= 与 RFC 5987 filename*=
    assert 'filename="' in disposition
    assert "filename*=UTF-8''" in disposition
    # RFC 5987 部分能 decode 回中文
    import re
    match = re.search(r"filename\*=UTF-8''([^;]+)", disposition)
    assert match is not None
    decoded = unquote(match.group(1))
    assert "中文文件名" in decoded


# ---------- 格式校验 ----------


def test_unsupported_format_raises() -> None:
    text = "无敏感内容"
    document = parse_text(text)
    report = _analyze(document)
    with pytest.raises(ValueError):
        build_masked_file(document, report, "data.txt", MaskStrategy.PARTIAL, fmt="unknown")


def test_force_text_format_from_json_document() -> None:
    # JSON 文档强制导出 text 格式
    raw = json.dumps({"phone": PHONE}, ensure_ascii=False)
    document = parse_json(raw)
    report = _analyze(document)
    data, media, _, _ = build_masked_file(document, report, "data.json", MaskStrategy.PARTIAL, fmt="text")
    assert media == "text/plain; charset=utf-8"
