"""导出接口测试：/detect/export 与 /detect/file/export。

用 FastAPI TestClient 跑真实接口，覆盖：
- 文本导出（各策略）
- 文件导出（各格式）
- 不落库（task 总数不变）
- 错误处理（非法策略、非法格式、超大文件）
"""

from __future__ import annotations

import csv
import io
import json
from urllib.parse import unquote

import pytest

ID_CARD = "11010519491231002X"
PHONE = "13800138000"


# ---------- /detect/export（文本入口）----------


def test_detect_export_text_partial(client) -> None:
    """文本导出 partial 策略：返回文件流，命中值已脱敏。"""
    resp = client.post(
        "/api/v1/detect/export",
        json={"text": f"手机：{PHONE}", "strategy": "partial"},
    )
    assert resp.status_code == 200
    assert "attachment" in resp.headers.get("content-disposition", "")
    content = resp.content.decode("utf-8")
    assert PHONE not in content
    assert content.startswith("手机：1")
    assert "*" in content


def test_detect_export_text_block(client) -> None:
    resp = client.post(
        "/api/v1/detect/export",
        json={"text": f"手机：{PHONE}", "strategy": "block"},
    )
    assert resp.status_code == 200
    content = resp.content.decode("utf-8")
    assert "手机号" in content
    assert PHONE not in content


def test_detect_export_text_hash(client) -> None:
    resp = client.post(
        "/api/v1/detect/export",
        json={"text": f"手机：{PHONE}", "strategy": "hash"},
    )
    assert resp.status_code == 200
    content = resp.content.decode("utf-8")
    assert PHONE not in content
    # 哈希是 8 位十六进制
    import re
    hashes = re.findall(r"\b[0-9a-f]{8}\b", content)
    assert len(hashes) >= 1


def test_detect_export_text_encrypt_returns_key_header(client) -> None:
    """encrypt 策略：响应头含 X-FlowMask-Encrypt-Key。"""
    resp = client.post(
        "/api/v1/detect/export",
        json={"text": f"手机：{PHONE}", "strategy": "encrypt"},
    )
    assert resp.status_code == 200
    key = resp.headers.get("x-flowmask-encrypt-key")
    assert key is not None
    assert key  # 非空
    content = resp.content.decode("utf-8")
    assert PHONE not in content


def test_detect_export_text_json_format(client) -> None:
    """JSON 文本导出：自动探测为 json，返回 .json 文件。"""
    raw = json.dumps({"phone": PHONE}, ensure_ascii=False)
    resp = client.post(
        "/api/v1/detect/export",
        json={"text": raw, "strategy": "partial"},
    )
    assert resp.status_code == 200
    content = resp.content.decode("utf-8")
    parsed = json.loads(content)
    assert PHONE not in parsed["phone"]
    # Content-Type 是 application/json
    assert "application/json" in resp.headers.get("content-type", "")


def test_detect_export_text_invalid_strategy_400(client) -> None:
    resp = client.post(
        "/api/v1/detect/export",
        json={"text": "test", "strategy": "unknown"},
    )
    assert resp.status_code == 400


def test_detect_export_text_invalid_format_400(client) -> None:
    resp = client.post(
        "/api/v1/detect/export",
        json={"text": "test", "strategy": "partial", "format": "docx"},
    )
    # text 文档强制导成 docx 不支持（docx 只能从上传文件来）
    assert resp.status_code == 400


def test_detect_export_not_create_task(client, database) -> None:
    """导出不落库：调用前后 task 总数不变。"""
    # 先查当前 task 数
    resp_before = client.get("/api/v1/tasks")
    total_before = resp_before.json()["total"]
    # 调导出
    resp = client.post(
        "/api/v1/detect/export",
        json={"text": f"手机：{PHONE}", "strategy": "partial"},
    )
    assert resp.status_code == 200
    # 再查 task 数
    resp_after = client.get("/api/v1/tasks")
    total_after = resp_after.json()["total"]
    assert total_after == total_before


# ---------- /detect/file/export（文件入口）----------


def test_detect_export_file_txt(client) -> None:
    content = f"手机：{PHONE}".encode("utf-8")
    resp = client.post(
        "/api/v1/detect/file/export",
        files={"file": ("note.txt", content, "text/plain")},
        data={"strategy": "block"},
    )
    assert resp.status_code == 200
    body = resp.content.decode("utf-8")
    assert "手机号" in body
    assert PHONE not in body


def test_detect_export_file_json(client) -> None:
    raw = json.dumps({"phone": PHONE, "age": 25}, ensure_ascii=False)
    resp = client.post(
        "/api/v1/detect/file/export",
        files={"file": ("data.json", raw.encode("utf-8"), "application/json")},
        data={"strategy": "partial"},
    )
    assert resp.status_code == 200
    parsed = json.loads(resp.content)
    assert PHONE not in parsed["phone"]
    assert parsed["age"] == 25


def test_detect_export_file_csv(client) -> None:
    raw = f"name,phone\n张三,{PHONE}\n"
    resp = client.post(
        "/api/v1/detect/file/export",
        files={"file": ("data.csv", raw.encode("utf-8"), "text/csv")},
        data={"strategy": "block"},
    )
    assert resp.status_code == 200
    # CSV 用 utf-8-sig
    content = resp.content.decode("utf-8-sig")
    rows = list(csv.reader(io.StringIO(content)))
    assert rows[0] == ["name", "phone"]
    assert PHONE not in content


def test_detect_export_file_sql(client) -> None:
    raw = (
        "CREATE TABLE users (id INT, phone VARCHAR(20));\n"
        f"INSERT INTO users VALUES (1, '{PHONE}');\n"
    )
    resp = client.post(
        "/api/v1/detect/file/export",
        files={"file": ("data.sql", raw.encode("utf-8"), "application/sql")},
        data={"strategy": "block"},
    )
    assert resp.status_code == 200
    content = resp.content.decode("utf-8")
    assert "CREATE TABLE" in content
    assert PHONE not in content


def test_detect_export_file_unsupported_415(client) -> None:
    resp = client.post(
        "/api/v1/detect/file/export",
        files={"file": ("data.xyz", b"content", "application/octet-stream")},
        data={"strategy": "partial"},
    )
    assert resp.status_code == 415


def test_detect_export_file_invalid_strategy_400(client) -> None:
    resp = client.post(
        "/api/v1/detect/file/export",
        files={"file": ("note.txt", b"hello", "text/plain")},
        data={"strategy": "bad"},
    )
    assert resp.status_code == 400


def test_detect_export_file_chinese_filename(client) -> None:
    """中文文件名：Content-Disposition 同时含 ASCII 与 RFC 5987。"""
    content = f"手机：{PHONE}".encode("utf-8")
    resp = client.post(
        "/api/v1/detect/file/export",
        files={"file": ("中文数据.txt", content, "text/plain")},
        data={"strategy": "partial"},
    )
    assert resp.status_code == 200
    disposition = resp.headers.get("content-disposition", "")
    assert 'filename="' in disposition
    assert "filename*=UTF-8''" in disposition
    import re
    match = re.search(r"filename\*=UTF-8''([^;]+)", disposition)
    assert match is not None
    decoded = unquote(match.group(1))
    assert "中文数据" in decoded


def test_detect_export_min_level_safe_overrides(client) -> None:
    """证明导出不受界面分级过滤影响：min_level 不在请求里，导出仍脱敏。

    场景：/detect 在 min_level=L3 时无命中（手机号只是 L2），
    但 /detect/export 仍脱敏（因为内部固定 min_level=SAFE）。
    """
    # 先调 /detect 用 min_level=L3，应无命中
    resp_detect = client.post(
        "/api/v1/detect",
        json={"text": f"手机：{PHONE}", "min_level": "L3"},
    )
    assert resp_detect.status_code == 200
    assert len(resp_detect.json()["findings"]) == 0
    # 再调 /detect/export，应脱敏
    resp_export = client.post(
        "/api/v1/detect/export",
        json={"text": f"手机：{PHONE}", "strategy": "block"},
    )
    assert resp_export.status_code == 200
    body = resp_export.content.decode("utf-8")
    assert "手机号" in body
    assert PHONE not in body
