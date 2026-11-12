"""审计溯源接口：分页查询、哈希链校验、导出。

演示要点：调一次 ``GET /api/v1/audit/verify`` 返回 ``ok=true``，
手动改一条库里的记录（或删一条）后同一个接口立刻返回 ``ok=false`` + 断链位置，
这就是"审计日志不可篡改"的可验证证明。
"""

from __future__ import annotations

import csv
import io
import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status

from ..audit.chain import (
    EXPORT_MAX_LIMIT,
    GENESIS,
    entry_hash_of,
    export_entries,
    latest_entries,
    list_entries,
    verify_chain,
)
from ..core.db import Database
from ..tasks.export import sanitize_csv_cell
from .deps import db_dep
from .schemas import AuditOut, AuditPage, VerifyOut

router = APIRouter(tags=["audit"])


@router.get("/audit", response_model=AuditPage, summary="审计记录（倒序分页）")
def list_audit(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    db: Database = Depends(db_dep),
) -> AuditPage:
    rows, total = list_entries(db, limit=page_size, offset=(page - 1) * page_size)
    items = []
    for row in rows:
        payload = dict(row)
        payload["entry_hash"] = entry_hash_of(payload)
        items.append(AuditOut(**payload))
    return AuditPage(items=items, total=total, page=page, page_size=page_size)


@router.get("/audit/verify", response_model=VerifyOut, summary="校验哈希链完整性")
def verify(db: Database = Depends(db_dep)) -> VerifyOut:
    return VerifyOut(**verify_chain(db))


@router.get("/audit/export", summary="导出审计记录（jsonl/json/csv，支持游标全量导出）")
def export_audit(
    fmt: str = Query(default="jsonl", alias="format"),
    limit: int = Query(default=200, ge=1, le=EXPORT_MAX_LIMIT, description="单次导出的条数上限"),
    after_id: int | None = Query(
        default=None,
        ge=0,
        description="游标：只导出 id 大于该值的记录（正序）；不传则导出最近 limit 条（兼容旧行为）",
    ),
    db: Database = Depends(db_dep),
) -> Response:
    """导出审计链。

    不传 ``after_id``：保持旧行为，导出**最近** ``limit`` 条（默认 200），按 id 正序——
    链很长时这只是"部分链"，离线验签脚本会据此标注。

    传 ``after_id=0`` 开始分页全量导出：每页按 id 正序返回 ``limit`` 条，
    响应头 ``X-FlowMask-Audit-Next-After-Id`` 给出下一页游标（到链尾时不返回该头），
    逐页拼起来即为完整链，可直接交给 ``scripts/verify_export.py`` 验签。
    """
    fmt = (fmt or "jsonl").lower()
    next_after_id: int | None = None
    if after_id is None:
        rows, total = latest_entries(db, limit=limit)
    else:
        rows, total, next_after_id = export_entries(db, after_id=after_id, limit=limit)
    headers = {
        "X-FlowMask-Audit-Total": str(total),
        "X-FlowMask-Audit-Returned": str(len(rows)),
        # 链尾锚点：把本页最后一条的哈希随响应返回，调用方可另行保存，
        # 之后即使整段尾部记录被删除，也能拿这个锚点对账（离线文件自身无法发现尾部截断）。
        "X-FlowMask-Audit-Head": entry_hash_of(rows[-1]) if rows else GENESIS,
    }
    if next_after_id is not None:
        headers["X-FlowMask-Audit-Next-After-Id"] = str(next_after_id)

    if fmt == "jsonl":
        body = "\n".join(json.dumps(row, ensure_ascii=False) for row in rows)
        if body:
            body += "\n"
        return Response(
            content=body.encode("utf-8"),
            media_type="application/x-ndjson; charset=utf-8",
            headers={
                **headers,
                "Content-Disposition": 'attachment; filename="flowmask-audit.jsonl"',
            },
        )
    if fmt == "json":
        return Response(
            content=json.dumps(
                {
                    "total": total,
                    "after_id": after_id,
                    "returned": len(rows),
                    "next_after_id": next_after_id,
                    "items": rows,
                },
                ensure_ascii=False,
                indent=2,
            ).encode("utf-8"),
            media_type="application/json; charset=utf-8",
            headers={
                **headers,
                "Content-Disposition": 'attachment; filename="flowmask-audit.json"',
            },
        )
    if fmt == "csv":
        buffer = io.StringIO()
        columns: list[str] = [
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
        writer = csv.DictWriter(buffer, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({key: _cell(row.get(key)) for key in columns})
        return Response(
            content=("\ufeff" + buffer.getvalue()).encode("utf-8"),
            media_type="text/csv; charset=utf-8",
            headers={
                **headers,
                "Content-Disposition": 'attachment; filename="flowmask-audit.csv"',
            },
        )
    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail=f"不支持的格式: {fmt}，可选 jsonl / json / csv",
    )


def _cell(value: Any) -> Any:
    """CSV 单元格处理：dict/list 压成 JSON 字符串，并防公式注入。"""
    if isinstance(value, (dict, list)):
        value = json.dumps(value, ensure_ascii=False)
    return sanitize_csv_cell(value)
