"""告警接口：按级别/状态查询、汇总、认领或忽略（检测 → 告警 → 处置闭环）。

告警在任务落库时由 ``app/alerts.py`` 自动生成；这里只负责查询与状态流转。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status

from ..alerts import alert_summary, get_alert, handle_alert, page_alerts
from ..core.db import Database
from .deps import Operator, db_dep, operator_dep
from .schemas import AlertAckIn, AlertOut, AlertPage, AlertSummaryOut

router = APIRouter(tags=["alerts"])


@router.get("/alerts/summary", response_model=AlertSummaryOut, summary="告警汇总（看板/角标）")
def alerts_summary(
    task_id: str | None = None,
    db: Database = Depends(db_dep),
) -> AlertSummaryOut:
    """先于 ``/alerts/{alert_id}`` 注册，避免 ``summary`` 被当成路径参数。"""
    return AlertSummaryOut(**alert_summary(db, task_id))


@router.get("/alerts", response_model=AlertPage, summary="告警列表（按级别/状态过滤）")
def list_alert(
    task_id: str | None = None,
    level: str | None = None,
    status_filter: str | None = Query(default=None, alias="status", description="open / ack / ignored"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    db: Database = Depends(db_dep),
) -> AlertPage:
    try:
        items, total = page_alerts(
            db,
            task_id=task_id,
            level=level,
            status=status_filter,
            page=page,
            page_size=page_size,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return AlertPage(
        items=[AlertOut(**item) for item in items],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/alerts/{alert_id}", response_model=AlertOut, summary="告警详情")
def alert_detail(alert_id: int, db: Database = Depends(db_dep)) -> AlertOut:
    alert = get_alert(db, alert_id)
    if alert is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="告警不存在")
    return AlertOut(**alert)


@router.post("/alerts/{alert_id}/ack", response_model=AlertOut, summary="认领 / 忽略 / 重置告警")
def ack_alert(
    alert_id: int,
    payload: AlertAckIn | None = None,
    operator: Operator = Depends(operator_dep),
    db: Database = Depends(db_dep),
) -> AlertOut:
    body = payload or AlertAckIn()
    try:
        alert = handle_alert(
            db,
            alert_id,
            status=body.status,
            operator_id=operator.operator_id,
            note=body.note,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    if alert is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="告警不存在")
    return AlertOut(**alert)
