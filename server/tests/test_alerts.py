"""告警 + 脱敏建议联动测试（需求 9.2）。

覆盖三块：
1. 建议文案与阈值判定（纯函数，不碰数据库）；
2. 告警落库与状态流转（``insert_alerts`` / 分页 / 认领 / 汇总）；
3. 接口端到端：任务完成后自动生成告警、按级别/状态过滤、汇总、认领与错误码。
"""

from __future__ import annotations

import time

import pytest

from app.alerts import (
    ALERT_STATUSES,
    alert_summary,
    alerts_for_tasks,
    get_alert,
    handle_alert,
    insert_alerts,
    page_alerts,
    severity_label,
    should_alert,
    suggestion_for,
    title_for,
)
from app.identify import Level, SegmentFinding

ID_CARD = "11010519491231002X"
PHONE = "13800138000"


def _wait_done(client, task_id: str, timeout: float = 5.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        body = client.get(f"/api/v1/tasks/{task_id}").json()
        if body["status"] in {"done", "failed"}:
            return body
        time.sleep(0.05)
    raise AssertionError("任务在超时时间内未完成")


def _create_text_task(client, text: str) -> str:
    response = client.post("/api/v1/tasks/text", json={"text": text})
    assert response.status_code == 200, response.text
    return response.json()["task_id"]


def _finding(level: Level, types: list[str], path: str = "$.text") -> SegmentFinding:
    return SegmentFinding(
        path=path,
        original_hash="a" * 64,
        masked="[已屏蔽]",
        level=level,
        score=0.9,
        types=types,
        hits=1,
        rule_ids=types,
        confidence_vector=[],
        model_chain="l1",
    )


# --- 1. 纯函数：建议文案与阈值 ------------------------------------------------


def test_severity_labels_follow_level() -> None:
    assert severity_label(Level.HIGH) == "紧急"
    assert severity_label(Level.MID) == "重要"
    assert severity_label(Level.LOW) == "提示"


def test_suggestion_matches_masking_templates() -> None:
    high = suggestion_for(Level.HIGH, ["id_card"])
    assert "身份证号" in high and "已屏蔽" in high and "禁止明文留存" in high
    mid = suggestion_for(Level.MID, ["phone"])
    assert "手机号" in mid and "星号掩码" in mid
    low = suggestion_for(Level.LOW, ["email"])
    assert "邮箱" in low and "[低危]" in low
    # 认不出的类别键原样保留，不因解析失败丢信息
    assert "unknown_type" in suggestion_for(Level.HIGH, ["unknown_type"])


def test_title_contains_severity_type_and_path() -> None:
    title = title_for(Level.HIGH, ["id_card"], "$.id")
    assert title.startswith("紧急告警：身份证号") and "$.id" in title


def test_should_alert_respects_threshold(tmp_env, monkeypatch) -> None:
    from app.core.config import get_settings

    assert should_alert(Level.HIGH) is True
    assert should_alert(Level.MID) is True
    assert should_alert(Level.LOW) is True
    assert should_alert(Level.SAFE) is False

    monkeypatch.setenv("FLOWMASK_ALERT_MIN_LEVEL", "L3")
    get_settings.cache_clear()
    assert should_alert(Level.HIGH) is True
    assert should_alert(Level.MID) is False
    assert should_alert(Level.LOW) is False


# --- 2. 存储层：落库、过滤、认领、汇总 ----------------------------------------


def test_insert_alerts_and_page_filters(database) -> None:
    with database.write() as conn:
        count = insert_alerts(
            conn,
            task_id="t1",
            findings=[
                (1, _finding(Level.HIGH, ["id_card"])),
                (2, _finding(Level.MID, ["phone"])),
                (3, _finding(Level.SAFE, [])),
            ],
        )
    assert count == 2  # SAFE 不告警

    items, total = page_alerts(database, task_id="t1")
    assert total == 2
    # 新的在前：finding_id=2（L2）先出现
    assert [item["finding_id"] for item in items] == [2, 1]
    assert items[0]["level"] == "L2" and items[0]["status"] == "open"
    assert items[0]["types"] == ["phone"] and items[0]["status_label"] == "待处理"
    assert items[0]["suggestion"] and items[0]["title"]

    assert page_alerts(database, level="L3")[1] == 1
    assert page_alerts(database, status="open")[1] == 2
    assert page_alerts(database, status="ack")[1] == 0
    with pytest.raises(ValueError):
        page_alerts(database, level="L9")
    with pytest.raises(ValueError):
        page_alerts(database, status="bogus")


def test_handle_alert_flow(database) -> None:
    with database.write() as conn:
        insert_alerts(conn, task_id="t2", findings=[(7, _finding(Level.LOW, ["email"]))])
    alert = page_alerts(database, task_id="t2")[0][0]
    assert get_alert(database, alert["id"])["status"] == "open"

    acked = handle_alert(database, alert["id"], status="ack", operator_id="alice", note="已通知业务方")
    assert acked["status"] == "ack" and acked["status_label"] == "已认领"
    assert acked["handled_by"] == "alice" and acked["note"] == "已通知业务方"
    assert acked["handled_at"]

    reset = handle_alert(database, alert["id"], status="open")
    assert reset["status"] == "open" and reset["handled_at"] is None and reset["handled_by"] is None

    assert handle_alert(database, 999999, status="ack") is None
    with pytest.raises(ValueError):
        handle_alert(database, alert["id"], status="weird")

    summary = alert_summary(database, "t2")
    assert summary["total"] == 1 and summary["open"] == 1
    assert summary["by_status"] == {"open": 1, "ack": 0, "ignored": 0}
    assert set(summary["by_level"]) == {"L1", "L2", "L3"}
    assert ALERT_STATUSES == ("open", "ack", "ignored")


# --- 3. 接口端到端 ------------------------------------------------------------


def test_task_generates_alert_with_suggestion(client) -> None:
    task_id = _create_text_task(client, f"身份证号：{ID_CARD}，联系电话 {PHONE}")
    assert _wait_done(client, task_id)["status"] == "done"

    body = client.get("/api/v1/alerts", params={"task_id": task_id}).json()
    assert body["total"] >= 1
    high = next(item for item in body["items"] if item["level"] == "L3")
    assert high["status"] == "open" and high["status_label"] == "待处理"
    assert high["severity"] == "紧急" and "身份证号" in high["title"]
    assert "已屏蔽" in high["suggestion"]
    assert high["task_id"] == task_id and high["finding_id"] > 0

    # 汇总接口（注册在 /alerts/{id} 前，不能被当成路径参数）
    summary = client.get("/api/v1/alerts/summary", params={"task_id": task_id}).json()
    assert summary["total"] == body["total"] and summary["open"] == body["total"]
    assert summary["by_level"]["L3"] >= 1
    assert summary["by_status"]["open"] == body["total"]


def test_alert_filters_and_detail(client) -> None:
    task_id = _create_text_task(client, f"联系电话 {PHONE}")
    _wait_done(client, task_id)
    alerts = client.get("/api/v1/alerts", params={"task_id": task_id}).json()["items"]
    assert alerts and alerts[0]["level"] == "L2"

    level2 = client.get("/api/v1/alerts", params={"level": "L2"}).json()
    assert level2["total"] >= 1 and all(item["level"] == "L2" for item in level2["items"])
    detail = client.get(f"/api/v1/alerts/{alerts[0]['id']}").json()
    assert detail["id"] == alerts[0]["id"] and detail["types"] == ["phone"]

    assert client.get("/api/v1/alerts", params={"level": "L9"}).status_code == 400
    assert client.get("/api/v1/alerts", params={"status": "bogus"}).status_code == 400
    assert client.get("/api/v1/alerts/999999").status_code == 404
    assert client.post("/api/v1/alerts/999999/ack", json={"status": "ack"}).status_code == 404


def test_ack_via_api_updates_summary(client) -> None:
    task_id = _create_text_task(client, f"身份证号：{ID_CARD}")
    _wait_done(client, task_id)
    alert = client.get("/api/v1/alerts", params={"task_id": task_id}).json()["items"][0]

    response = client.post(
        f"/api/v1/alerts/{alert['id']}/ack",
        json={"status": "ack", "note": "已联系业务方脱敏"},
        headers={"X-Operator-Id": "alice"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ack" and body["status_label"] == "已认领"
    assert body["handled_by"] == "alice" and body["note"] == "已联系业务方脱敏"

    acked = client.get("/api/v1/alerts", params={"status": "ack", "task_id": task_id}).json()
    assert acked["total"] == 1
    summary = client.get("/api/v1/alerts/summary", params={"task_id": task_id}).json()
    assert summary["by_status"]["ack"] == 1

    bad = client.post(f"/api/v1/alerts/{alert['id']}/ack", json={"status": "weird"})
    assert bad.status_code == 400


def test_safe_text_has_no_alerts(client) -> None:
    task_id = _create_text_task(client, "今天天气不错，适合出门散步")
    _wait_done(client, task_id)
    assert client.get("/api/v1/alerts", params={"task_id": task_id}).json()["total"] == 0


def test_alerts_for_tasks_aggregates_session_windows(client, database) -> None:
    """实时旁路面板用：按会话的一组窗口任务聚合告警（不含明细）。"""
    task_high = _create_text_task(client, f"身份证号：{ID_CARD}")
    task_low = _create_text_task(client, f"联系电话 {PHONE}")
    for task_id in (task_high, task_low):
        assert _wait_done(client, task_id)["status"] == "done"

    summary = alerts_for_tasks(database, [task_high, task_low])
    assert summary["total"] >= 2
    assert summary["open"] == summary["total"]
    assert summary["by_level"]["L3"] >= 1 and summary["by_level"]["L2"] >= 1
    last = summary["last_alert"]
    assert last is not None and last["id"] > 0 and last["title"]
    # 重复的任务 ID 不会重复计数
    deduped = alerts_for_tasks(database, [task_high, task_high])
    assert deduped["total"] == alerts_for_tasks(database, [task_high])["total"]

    empty = alerts_for_tasks(database, [])
    assert empty["total"] == 0 and empty["open"] == 0
    assert empty["last_alert"] is None
    assert set(empty["by_level"]) == {"L1", "L2", "L3"}


def test_threshold_config_suppresses_low_level_alerts(tmp_env, monkeypatch) -> None:
    """阈值设为 L3 时，L2 命中只落 findings、不进告警队列。"""
    from fastapi.testclient import TestClient

    from app.core.config import get_settings
    from app.main import create_app

    monkeypatch.setenv("FLOWMASK_ALERT_MIN_LEVEL", "L3")
    get_settings.cache_clear()

    with TestClient(create_app()) as test_client:
        task_id = _create_text_task(test_client, f"联系电话 {PHONE}")
        _wait_done(test_client, task_id)
        findings = test_client.get(f"/api/v1/tasks/{task_id}/findings").json()
        assert findings["total"] >= 1
        assert test_client.get("/api/v1/alerts", params={"task_id": task_id}).json()["total"] == 0
