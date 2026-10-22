"""离线验签：复算审计导出文件（JSONL / JSON / CSV）的哈希链。

用途：把 ``GET /api/v1/audit/export`` 导出的文件拷到任意机器，不连数据库、不起服务，
用与写库方完全相同的口径（``app/audit/chain.py``）复算 ``prev_hash`` 链与 HMAC 签名，
确认导出件没有被改过。演示时可以先跑一次通过，再手动改一行字段或删一行，
脚本会准确报出断链位置与原因。

用法（在 ``server/`` 目录下）::

    python -m scripts.verify_export audit.jsonl
    python -m scripts.verify_export audit.json --secret flowmask-demo-secret
    python -m scripts.verify_export audit.csv --json

退出码：0 校验通过；1 断链或签名不符；2 文件或参数错误。
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import sys
from pathlib import Path
from typing import Any

# 同时支持 `python -m scripts.verify_export` 与 `python scripts/verify_export.py`
_SERVER_DIR = Path(__file__).resolve().parents[1]
if str(_SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(_SERVER_DIR))

from app.audit.chain import GENESIS, HASH_FIELDS, entry_hash_of, verify_signature  # noqa: E402

SUPPORTED_SUFFIXES = (".jsonl", ".ndjson", ".json", ".csv")


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    """逐行读取 JSONL；跳过空行与带 ``type: meta`` 的元信息行。"""
    items: list[dict[str, Any]] = []
    for lineno, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"第 {lineno} 行不是合法 JSON：{exc}") from exc
        if not isinstance(obj, dict):
            raise ValueError(f"第 {lineno} 行不是 JSON 对象")
        if obj.get("type") == "meta":
            continue
        items.append(obj)
    return items


def _load_json(path: Path) -> list[dict[str, Any]]:
    """读取 ``/audit/export?format=json`` 的 ``{"total": n, "items": [...]}``。"""
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"不是合法 JSON：{exc}") from exc
    if isinstance(data, list):
        items = data
    elif isinstance(data, dict) and isinstance(data.get("items"), list):
        items = data["items"]
    else:
        raise ValueError("不是审计导出的 JSON 结构（缺少 items 数组）")
    if not all(isinstance(item, dict) for item in items):
        raise ValueError("items 里存在非对象元素")
    return list(items)


def _load_csv(path: Path) -> list[dict[str, Any]]:
    """读取 CSV（兼容带 BOM 的文件）。"""
    reader = csv.DictReader(io.StringIO(path.read_text(encoding="utf-8-sig")))
    if not reader.fieldnames:
        raise ValueError("CSV 为空或缺表头")
    return [dict(row) for row in reader]


def load_rows(path: Path) -> list[dict[str, Any]]:
    """按扩展名读取导出文件，统一成可复算哈希的字符串字段行。"""
    path = Path(path)
    if not path.is_file():
        raise ValueError(f"文件不存在：{path}")
    suffix = path.suffix.lower()
    if suffix in (".jsonl", ".ndjson"):
        raws = _load_jsonl(path)
    elif suffix == ".json":
        raws = _load_json(path)
    elif suffix == ".csv":
        raws = _load_csv(path)
    else:
        raise ValueError(f"不支持的文件类型：{suffix or '（无扩展名）'}，可选 .jsonl / .json / .csv")
    rows = [_coerce_row(raw) for raw in raws]
    if rows and not any(row["signature"] for row in rows):
        raise ValueError(
            "文件缺少 signature 字段，不像是 /audit/export 的审计导出（findings 导出没有签名）"
        )
    return rows


def _coerce_row(raw: dict[str, Any]) -> dict[str, Any]:
    """把一行导出数据收敛成写库时的字符串口径，避免 JSON/CSV 类型差异影响哈希。"""
    row: dict[str, Any] = {}
    for name in HASH_FIELDS:
        value = raw.get(name, "")
        row[name] = "" if value is None else str(value)
    row["prev_hash"] = "" if raw.get("prev_hash") is None else str(raw.get("prev_hash"))
    row["signature"] = "" if raw.get("signature") is None else str(raw.get("signature"))
    raw_id = raw.get("id")
    try:
        row["id"] = int(raw_id) if raw_id not in (None, "") else None
    except (TypeError, ValueError):
        row["id"] = None
    return row


def _ordered(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """按 id 升序排列；缺 id 时保持文件原序（导出本身已是正序）。"""
    if all(row["id"] is not None for row in rows):
        return sorted(rows, key=lambda row: row["id"])
    return rows


def verify_rows(
    rows: list[dict[str, Any]],
    secret: str,
    *,
    require_genesis: bool = False,
) -> dict[str, Any]:
    """校验一行行导出记录的 prev_hash 链与 HMAC 签名。

    返回结构与 ``app.audit.chain.verify_chain`` 对齐，额外带 ``partial``：
    文件首条 ``prev_hash`` 不是全 0 时说明只导出了最近一段链（接口默认最多 200 条）。
    """
    result: dict[str, Any] = {
        "ok": True,
        "total": len(rows),
        "checked": 0,
        "broken_at": None,
        "reason": "",
        "head_hash": GENESIS,
        "partial": False,
        "legacy_rows": 0,
    }
    if not rows:
        # 空文件说明不了任何事，不能报「通过」：否则删光导出内容即可伪造"验签通过"。
        result.update(ok=False, partial=True, reason="导出文件为空，没有可校验的审计记录")
        return result

    ordered = _ordered(rows)
    first_prev = ordered[0]["prev_hash"]
    partial = first_prev != GENESIS
    result["partial"] = partial
    if partial and require_genesis:
        result.update(
            ok=False,
            broken_at=ordered[0]["id"],
            reason=(
                "导出不是完整链（首条 prev_hash 不是 64 个 0，可能只含最近 200 条记录），"
                "未通过 --require-genesis 检查"
            ),
        )
        return result
    # 完整链必须从 id=1 开始：只删链头若干条再把新首条 prev_hash 改成 GENESIS 的
    # 前段截断，单看 prev_hash 是自洽的，必须靠首条 id 兜住。
    if not partial and ordered[0]["id"] not in (None, 1):
        result.update(
            ok=False,
            broken_at=ordered[0]["id"],
            reason="首条记录 id 不是 1（前段记录被删除，链不完整）",
        )
        return result

    expected_prev = first_prev if partial else GENESIS
    head = expected_prev
    for row in ordered:
        result["checked"] += 1
        if row["prev_hash"] != expected_prev:
            result.update(
                ok=False,
                broken_at=row["id"],
                reason="prev_hash 与上一条记录的哈希不一致（记录被删除、插入或重排）",
                head_hash=head,
            )
            return result
        digest = entry_hash_of(row)
        scheme = verify_signature(secret, row["prev_hash"], digest, row["signature"])
        if scheme is None:
            result.update(
                ok=False,
                broken_at=row["id"],
                reason="签名校验失败（记录字段被修改，或密钥不匹配）",
                head_hash=head,
            )
            return result
        if scheme == "v1":
            result["legacy_rows"] += 1
        expected_prev = digest
        head = digest
    result["head_hash"] = head
    return result


def resolve_secret(explicit: str | None = None) -> tuple[str, str]:
    """确定 HMAC 密钥：优先 ``--secret``，否则读配置（环境变量 FLOWMASK_AUDIT_SECRET）。"""
    if explicit is not None:
        return explicit, "--secret"
    from app.core.config import get_settings

    return get_settings().audit_secret, "配置（FLOWMASK_AUDIT_SECRET，缺省为演示密钥）"


def format_report(result: dict[str, Any], path: Path) -> str:
    """把校验结果排版成便于评审/演示阅读的多行文本。"""
    status = "通过" if result["ok"] else "失败"
    lines = [f"离线验签{status}：{path}"]
    if result["total"] == 0:
        lines.append("  记录数 0（空导出，没有可校验的审计记录）")
        if not result["ok"]:
            lines.append(f"  原因：{result['reason']}")
        return "\n".join(lines)
    if result["partial"]:
        chain_desc = f"部分链（不是从 genesis 开始，只含 {result['total']} 条）"
    else:
        chain_desc = "完整链（从 genesis 开始）"
    lines.append(f"  记录数 {result['total']}，{chain_desc}")
    if result["ok"]:
        lines.append(f"  链尾哈希 {result['head_hash']}")
    else:
        lines.append(f"  断在 id={result['broken_at']}（已校验 {result['checked']} 条）")
        lines.append(f"  原因：{result['reason']}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    """命令行入口：返回 0 通过 / 1 校验失败 / 2 文件或参数错误。"""
    parser = argparse.ArgumentParser(
        prog="python -m scripts.verify_export",
        description="离线校验 /audit/export 导出的审计文件（不连数据库、不起服务）",
    )
    parser.add_argument("path", type=Path, help="审计导出文件：.jsonl / .json / .csv")
    parser.add_argument(
        "--secret",
        default=None,
        help="HMAC 密钥；缺省读配置 FLOWMASK_AUDIT_SECRET（未设置时为演示密钥）",
    )
    parser.add_argument(
        "--require-genesis",
        action="store_true",
        help="要求导出是完整链（首条 prev_hash 为 64 个 0），部分链直接判失败",
    )
    parser.add_argument("--json", action="store_true", help="以 JSON 输出结果，便于脚本消费")
    args = parser.parse_args(argv)

    try:
        rows = load_rows(args.path)
        secret, _source = resolve_secret(args.secret)
    except (ValueError, OSError) as exc:
        print(f"验签失败：{exc}", file=sys.stderr)
        return 2

    result = verify_rows(rows, secret, require_genesis=args.require_genesis)
    if args.json:
        payload = dict(result)
        payload["path"] = str(args.path)
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(format_report(result, args.path))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
