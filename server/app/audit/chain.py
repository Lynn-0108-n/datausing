"""审计哈希链：防篡改的溯源记录。

每条记录保存"原文/脱敏副本的哈希 + 模型链路 + 置信度向量 + 操作人"，并用两种手段串起来：

1. ``prev_hash`` 指向上一条记录的 entry_hash（首条为 64 个 0），任何记录被删除/插入/改字段，
   后续记录的重算哈希都对不上；
2. ``signature = HMAC-SHA256(secret, entry_hash)``，没有密钥无法伪造一条链上记录。

entry_hash 的规范化口径固定：对固定字段做「字典序排序 + 紧凑分隔符」的 JSON，
UTF-8 编码后取 SHA-256。写库方与校验方用同一函数，因此可独立复算。

存储仍然只用标准库 sqlite3，不引第三方依赖——评审时能一句句讲清楚。
"""

from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any

from ..core.config import get_settings
from ..core.db import Database, utcnow_iso

GENESIS = "0" * 64

#: 单次导出请求的硬上限：分页大小可调，但不允许一次拉出任意大的响应。
EXPORT_MAX_LIMIT = 5000

# 参与哈希计算的字段（签名与 prev_hash 本身不参与，否则循环依赖）
HASH_FIELDS = (
    "task_id",
    "input_hash",
    "output_hash",
    "model_chain",
    "confidence_vector",
    "rule_id",
    "operator_id",
    "user_id",
    "ts",
)


def _canonical(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def canonical_hash(payload: dict[str, Any]) -> str:
    """对规范化 JSON 求 SHA-256。"""
    return hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()


def sign(secret: str, entry_hash: str) -> str:
    """HMAC-SHA256 签名；演示环境用配置里的 audit_secret，生产应放 KMS。"""
    return hmac.new(secret.encode("utf-8"), entry_hash.encode("utf-8"), hashlib.sha256).hexdigest()


def _sign_payload(prev_hash: str, fields: dict[str, Any]) -> dict[str, str]:
    """计算一条记录的哈希与签名（写入与校验共用同一口径）。

    签名覆盖 ``prev_hash``：只签本条字段的话，删掉链头若干条再把新首条的
    ``prev_hash`` 改成 GENESIS 也能通过校验（"前段截断"伪造）。
    """
    digest = canonical_hash(fields)
    return {"digest": digest, "signature": sign(get_settings().audit_secret, f"{prev_hash}:{digest}")}


def verify_signature(secret: str, prev_hash: str, digest: str, signature: str) -> str | None:
    """校验签名并返回匹配的方案（``"v2"`` / ``"v1"`` / ``None``）。

    v2 覆盖 prev_hash；v1 是旧版本只签本条哈希，仅用于兼容升级前写入的旧记录。
    """
    if hmac.compare_digest(sign(secret, f"{prev_hash}:{digest}"), signature):
        return "v2"
    if hmac.compare_digest(sign(secret, digest), signature):
        return "v1"
    return None


def _payload_of(row: Any) -> dict[str, Any]:
    return {name: row[name] for name in HASH_FIELDS}


def entry_hash_of(row: Any) -> str:
    """由一行记录（Row / dict）重算 entry_hash，校验与写入共用。"""
    return canonical_hash(_payload_of(row))


def _head_hash(conn: Any) -> str:
    row = conn.execute("SELECT task_id, input_hash, output_hash, model_chain, confidence_vector, rule_id, operator_id, user_id, ts FROM audit_log ORDER BY id DESC LIMIT 1").fetchone()
    if row is None:
        return GENESIS
    return entry_hash_of(row)


def append_entry_conn(
    conn: Any,
    *,
    task_id: str,
    input_hash: str,
    output_hash: str,
    model_chain: str,
    confidence_vector: str = "",
    rule_id: str = "",
    operator_id: str = "system",
    user_id: str = "local",
    ts: str | None = None,
) -> dict[str, Any]:
    """在调用方已持有的事务里追加审计记录（不自行 commit）。

    与 :func:`append_entry` 共用同一套哈希/签名口径；后台任务把 findings、
    告警与审计放进同一个事务时使用本函数，避免「findings 已提交、审计缺失」。
    """
    settings = get_settings()
    payload = {
        # 统一转字符串：列在 SQLite 里是 TEXT，写入用 int、读回是 str 会导致校验哈希不一致
        "task_id": str(task_id),
        "input_hash": input_hash,
        "output_hash": output_hash,
        "model_chain": model_chain,
        "confidence_vector": confidence_vector,
        "rule_id": rule_id,
        "operator_id": operator_id,
        "user_id": user_id,
        "ts": ts or utcnow_iso(),
    }
    payload["prev_hash"] = _head_hash(conn)
    sign_payload = _sign_payload(payload["prev_hash"], {name: payload[name] for name in HASH_FIELDS})
    digest = sign_payload["digest"]
    signature = sign_payload["signature"]
    conn.execute(
        """
        INSERT INTO audit_log
          (ts, task_id, input_hash, output_hash, model_chain, confidence_vector,
           rule_id, operator_id, user_id, prev_hash, signature)
        VALUES (?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            payload["ts"],
            payload["task_id"],
            payload["input_hash"],
            payload["output_hash"],
            payload["model_chain"],
            payload["confidence_vector"],
            payload["rule_id"],
            payload["operator_id"],
            payload["user_id"],
            payload["prev_hash"],
            signature,
        ),
    )
    entry_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    return {
        "id": entry_id,
        "entry_hash": digest,
        "signature": signature,
        "prev_hash": payload["prev_hash"],
        **{k: payload[k] for k in HASH_FIELDS},
    }


def append_entry(
    db: Database,
    *,
    task_id: str,
    input_hash: str,
    output_hash: str,
    model_chain: str,
    confidence_vector: str = "",
    rule_id: str = "",
    operator_id: str = "system",
    user_id: str = "local",
    ts: str | None = None,
) -> dict[str, Any]:
    """追加一条审计记录：读取链头哈希 → 计算本条哈希 → 签名 → 插入。"""
    with db.write() as conn:
        return append_entry_conn(
            conn,
            task_id=task_id,
            input_hash=input_hash,
            output_hash=output_hash,
            model_chain=model_chain,
            confidence_vector=confidence_vector,
            rule_id=rule_id,
            operator_id=operator_id,
            user_id=user_id,
            ts=ts,
        )


def list_entries(db: Database, limit: int = 50, offset: int = 0) -> tuple[list[dict[str, Any]], int]:
    """倒序分页列出审计记录（不含签名密钥说明）。"""
    limit = max(1, min(int(limit), 200))
    offset = max(0, int(offset))
    with db.connect() as conn:
        total = conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0]
        rows = conn.execute(
            "SELECT * FROM audit_log ORDER BY id DESC LIMIT ? OFFSET ?",
            (limit, offset),
        ).fetchall()
    return [dict(row) for row in rows], int(total)


def latest_entries(db: Database, limit: int = 200) -> tuple[list[dict[str, Any]], int]:
    """按 id 正序返回最近 ``limit`` 条（默认 200，保持旧导出行为）。"""
    limit = max(1, min(int(limit), EXPORT_MAX_LIMIT))
    with db.connect() as conn:
        total = conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0]
        rows = conn.execute("SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return list(reversed([dict(row) for row in rows])), int(total)


def export_entries(
    db: Database, *, after_id: int = 0, limit: int = 200
) -> tuple[list[dict[str, Any]], int, int | None]:
    """按 id 正序游标导出，供全量导出分页使用。

    返回 ``(rows, total, next_after_id)``：上一页最后一条的 id 作为下一页的
    ``after_id``；``next_after_id is None`` 表示已到链尾。多取一条用于判断是否还有下一页，
    避免调用方为「恰好整除」多跑一次空请求。
    """
    limit = max(1, min(int(limit), EXPORT_MAX_LIMIT))
    after_id = max(0, int(after_id))
    with db.connect() as conn:
        total = conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0]
        rows = conn.execute(
            "SELECT * FROM audit_log WHERE id > ? ORDER BY id ASC LIMIT ?",
            (after_id, limit + 1),
        ).fetchall()
    has_more = len(rows) > limit
    page = rows[:limit]
    next_after_id = page[-1]["id"] if has_more and page else None
    return [dict(row) for row in page], int(total), next_after_id


def verify_chain(db: Database, secret: str | None = None) -> dict[str, Any]:
    """从最早一条开始逐条校验：prev_hash 链接 + 字段哈希 + HMAC 签名。

    额外检查首条记录 id 必须是 1：若链头记录被删除，剩余记录即使字段与签名
    都没被改过，也不能再当作完整链（``prev_hash`` 覆盖 + id 检查共同堵住前段截断）。
    """
    key = get_settings().audit_secret if secret is None else secret
    expected_prev = GENESIS
    checked = 0
    legacy_rows = 0
    head = GENESIS
    with db.connect() as conn:
        rows = conn.execute("SELECT * FROM audit_log ORDER BY id ASC").fetchall()
    if rows and rows[0]["id"] != 1:
        return {
            "ok": False,
            "total": len(rows),
            "checked": 0,
            "broken_at": rows[0]["id"],
            "reason": "首条记录 id 不是 1（前段记录被删除，链不完整）",
            "head_hash": GENESIS,
            "legacy_rows": legacy_rows,
        }
    for row in rows:
        checked += 1
        if row["prev_hash"] != expected_prev:
            return {
                "ok": False,
                "total": len(rows),
                "checked": checked,
                "broken_at": row["id"],
                "reason": "prev_hash 与上一条记录的哈希不一致（记录被删除、插入或重排）",
                "head_hash": head,
                "legacy_rows": legacy_rows,
            }
        digest = entry_hash_of(row)
        scheme = verify_signature(key, row["prev_hash"], digest, row["signature"])
        if scheme is None:
            return {
                "ok": False,
                "total": len(rows),
                "checked": checked,
                "broken_at": row["id"],
                "reason": "签名校验失败（记录字段被修改，或密钥不匹配）",
                "head_hash": head,
                "legacy_rows": legacy_rows,
            }
        if scheme == "v1":
            legacy_rows += 1
        expected_prev = digest
        head = digest
    return {
        "ok": True,
        "total": len(rows),
        "checked": checked,
        "broken_at": None,
        "reason": "",
        "head_hash": head,
        "legacy_rows": legacy_rows,
    }
