"""离线验签脚本测试：不连数据库、不起服务，直接复算导出文件的哈希链。"""

from __future__ import annotations

import csv
import io
import json
from pathlib import Path

import pytest

from app.audit import append_entry, canonical_hash, list_entries
from app.core.config import get_settings
from scripts import verify_export as ve

COLUMNS = [
    "id",
    "ts",
    "task_id",
    "input_hash",
    "output_hash",
    "model_chain",
    "confidence_vector",
    "rule_id",
    "operator_id",
    "user_id",
    "prev_hash",
    "signature",
]


def _secret() -> str:
    """与写库方一致的 HMAC 密钥（测试环境为默认演示密钥）。"""
    return get_settings().audit_secret


def _append_three(db) -> None:
    for index in range(3):
        append_entry(
            db,
            task_id=f"task{index}",
            input_hash=canonical_hash({"i": index}),
            output_hash=canonical_hash({"o": index}),
            model_chain="l1",
            confidence_vector="[]",
            rule_id="idcard18",
        )


def _ascending_rows(db) -> list[dict]:
    """接口按 id 倒序返回，导出文件是正序。"""
    rows, _total = list_entries(db, limit=200)
    return list(reversed(rows))


def _write_jsonl(path: Path, rows: list[dict], *, bom: bool = False) -> Path:
    body = "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n"
    path.write_text(("\ufeff" if bom else "") + body, encoding="utf-8")
    return path


def _write_json(path: Path, rows: list[dict]) -> Path:
    payload = {"total": len(rows), "items": rows}
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _write_csv(path: Path, rows: list[dict], *, bom: bool = False) -> Path:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=COLUMNS, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow({key: row.get(key, "") for key in COLUMNS})
    path.write_text(("\ufeff" if bom else "") + buffer.getvalue(), encoding="utf-8")
    return path


def test_verify_rows_accepts_exported_jsonl(database, tmp_path: Path) -> None:
    _append_three(database)
    rows = _ascending_rows(database)
    path = _write_jsonl(tmp_path / "audit.jsonl", rows)

    result = ve.verify_rows(ve.load_rows(path), _secret())
    assert result["ok"] is True
    assert result["total"] == result["checked"] == 3
    assert result["partial"] is False
    assert result["head_hash"] != ve.GENESIS


@pytest.mark.parametrize(
    ("fmt", "writer"),
    [("jsonl", _write_jsonl), ("json", _write_json), ("csv", _write_csv)],
)
def test_verify_rows_accepts_all_export_formats(database, tmp_path: Path, fmt: str, writer) -> None:
    _append_three(database)
    path = writer(tmp_path / f"audit.{fmt}", _ascending_rows(database))

    result = ve.verify_rows(ve.load_rows(path), _secret())
    assert result["ok"] is True and result["total"] == 3


def test_jsonl_and_csv_with_bom_still_load(database, tmp_path: Path) -> None:
    _append_three(database)
    rows = _ascending_rows(database)
    for path in (
        _write_jsonl(tmp_path / "bom.jsonl", rows, bom=True),
        _write_csv(tmp_path / "bom.csv", rows, bom=True),
    ):
        assert ve.verify_rows(ve.load_rows(path), _secret())["ok"] is True


@pytest.mark.parametrize("fmt", ["jsonl", "json", "csv"])
def test_api_export_file_is_verifiable(database, client, tmp_path: Path, fmt: str) -> None:
    """直接拿接口产物验签，保证脚本与 /audit/export 的真实格式兼容。"""
    _append_three(database)
    resp = client.get(f"/api/v1/audit/export?format={fmt}")
    assert resp.status_code == 200
    path = tmp_path / f"api-audit.{fmt}"
    path.write_bytes(resp.content)

    result = ve.verify_rows(ve.load_rows(path), _secret())
    assert result["ok"] is True and result["total"] == 3


def test_tampered_field_is_reported(database) -> None:
    _append_three(database)
    rows = _ascending_rows(database)
    rows[1]["operator_id"] = "hacker"

    result = ve.verify_rows(rows, _secret())
    assert result["ok"] is False
    assert result["broken_at"] == rows[1]["id"]
    assert "签名" in result["reason"]


def test_deleted_row_is_reported(database) -> None:
    _append_three(database)
    rows = _ascending_rows(database)
    rows.pop(1)

    result = ve.verify_rows(rows, _secret())
    assert result["ok"] is False
    assert result["broken_at"] == rows[1]["id"]
    assert "prev_hash" in result["reason"]


def test_wrong_secret_fails(database) -> None:
    _append_three(database)
    result = ve.verify_rows(_ascending_rows(database), "另一个密钥")
    assert result["ok"] is False and "签名" in result["reason"]


def test_partial_chain_verifies_but_is_flagged(database) -> None:
    _append_three(database)
    rows = _ascending_rows(database)
    full = ve.verify_rows(rows, _secret())

    result = ve.verify_rows(rows[1:], _secret())
    assert result["ok"] is True
    assert result["partial"] is True
    assert result["total"] == 2
    assert result["head_hash"] == full["head_hash"]


def test_require_genesis_rejects_partial_chain(database) -> None:
    _append_three(database)
    rows = _ascending_rows(database)

    result = ve.verify_rows(rows[1:], _secret(), require_genesis=True)
    assert result["ok"] is False
    assert result["broken_at"] == rows[1]["id"]
    assert "genesis" in result["reason"]


def test_empty_export_passes(tmp_path: Path) -> None:
    result = ve.verify_rows(ve.load_rows(_write_jsonl(tmp_path / "empty.jsonl", [])), _secret())
    assert result["ok"] is True
    assert result["total"] == 0 and result["checked"] == 0


def test_rows_without_signature_are_rejected(database, tmp_path: Path) -> None:
    _append_three(database)
    rows = _ascending_rows(database)
    for row in rows:
        row["signature"] = ""
    with pytest.raises(ValueError, match="signature"):
        ve.load_rows(_write_jsonl(tmp_path / "no-sign.jsonl", rows))


def test_findings_export_is_rejected(tmp_path: Path) -> None:
    jsonl = tmp_path / "findings.jsonl"
    jsonl.write_text('{"type":"meta","n":1}\n{"id":1,"text":"x"}\n', encoding="utf-8")
    with pytest.raises(ValueError, match="signature"):
        ve.load_rows(jsonl)

    as_json = tmp_path / "findings.json"
    as_json.write_text(json.dumps({"total": 1, "items": [{"id": 1, "text": "x"}]}), encoding="utf-8")
    with pytest.raises(ValueError, match="signature"):
        ve.load_rows(as_json)


def test_unsupported_file_type_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "audit.txt"
    path.write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="不支持"):
        ve.load_rows(path)


def test_main_returns_zero_for_valid_file(database, tmp_path: Path, capsys) -> None:
    _append_three(database)
    path = _write_jsonl(tmp_path / "audit.jsonl", _ascending_rows(database))

    assert ve.main([str(path), "--secret", _secret()]) == 0
    out = capsys.readouterr().out
    assert "离线验签通过" in out and "完整链" in out


def test_main_default_secret_reads_settings(database, tmp_path: Path) -> None:
    """不传 --secret 时走 FLOWMASK_AUDIT_SECRET（未设置则为演示密钥）。"""
    _append_three(database)
    path = _write_jsonl(tmp_path / "audit.jsonl", _ascending_rows(database))
    assert ve.main([str(path)]) == 0


def test_main_returns_one_for_tampered_file(database, tmp_path: Path, capsys) -> None:
    _append_three(database)
    rows = _ascending_rows(database)
    rows[1]["operator_id"] = "hacker"
    path = _write_jsonl(tmp_path / "tampered.jsonl", rows)

    assert ve.main([str(path), "--secret", _secret()]) == 1
    out = capsys.readouterr().out
    assert "离线验签失败" in out and "签名" in out


def test_main_returns_two_for_missing_file(tmp_path: Path, capsys) -> None:
    assert ve.main([str(tmp_path / "nope.jsonl")]) == 2
    assert "文件不存在" in capsys.readouterr().err


def test_main_require_genesis_flag(database, tmp_path: Path, capsys) -> None:
    _append_three(database)
    path = _write_jsonl(tmp_path / "partial.jsonl", _ascending_rows(database)[1:])

    assert ve.main([str(path), "--secret", _secret(), "--require-genesis"]) == 1
    assert "完整链" in capsys.readouterr().out


def test_main_json_output_is_machine_readable(database, tmp_path: Path, capsys) -> None:
    _append_three(database)
    path = _write_jsonl(tmp_path / "audit.jsonl", _ascending_rows(database))

    assert ve.main([str(path), "--secret", _secret(), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["total"] == 3 and payload["checked"] == 3
    assert payload["path"] == str(path)
