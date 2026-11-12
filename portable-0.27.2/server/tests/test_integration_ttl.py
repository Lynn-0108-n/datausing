"""快照过期（410）的端到端测试：配合 ``tests/ttl_harness.py`` 启动的服务使用。

生产 TTL 固定 300s，不能通过环境变量修改；本用例需要另一个把 ``SnapshotStore``
TTL 缩短到 2s 的测试服务（见 ``tests/README.md``）：

    $env:FLOWMASK_TEST_TTL_BASE_URL = 'http://127.0.0.1:8396'
    $env:FLOWMASK_TEST_TTL_WAIT = '3'
"""

from __future__ import annotations

import os
import time

import pytest
import requests

pytestmark = pytest.mark.integration

TTL_BASE_URL = os.environ.get("FLOWMASK_TEST_TTL_BASE_URL", "").rstrip("/")
TTL_WAIT_S = float(os.environ.get("FLOWMASK_TEST_TTL_WAIT", "3"))
API = "/api/v1"

SAMPLE = "患者姓名：张三，手机 13800138000。\n"
# 该样本无 L3 命中（姓名 L1、手机 L2）→ 按 finding 级别走 partial：张* / 1******
SAMPLE_MASKED = "患者姓名：张*，手机 1******。\n"


def _url(path: str) -> str:
    return f"{TTL_BASE_URL}{API}{path}"


def _wait_done(session: requests.Session, task_id: str, timeout: float = 30.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        resp = session.get(_url(f"/tasks/{task_id}"), timeout=15)
        assert resp.status_code == 200, resp.text
        task = resp.json()
        if task["status"] in {"done", "failed"}:
            assert task["status"] == "done", task.get("error")
            return task
        time.sleep(0.05)
    raise AssertionError(f"任务 {task_id} 未在 {timeout}s 内完成")


def test_snapshot_expires_after_ttl_410() -> None:
    if not TTL_BASE_URL:
        pytest.skip("未设置 FLOWMASK_TEST_TTL_BASE_URL（需用 tests/ttl_harness.py 启动短 TTL 服务）")

    session = requests.Session()
    resp = session.post(
        _url("/tasks/batch"),
        files=[("files", ("ttl.txt", SAMPLE.encode("utf-8"), "text/plain"))],
        timeout=30,
    )
    assert resp.status_code == 201, resp.text
    task = resp.json()["items"][0]
    assert task["error"] == ""
    task_id = task["task_id"]
    _wait_done(session, task_id)

    # TTL 内：可正常取对比结果
    inside = session.get(_url(f"/tasks/{task_id}/comparison"), timeout=15)
    assert inside.status_code == 200, inside.text
    assert inside.json()["segments"][0]["masked"] == SAMPLE_MASKED

    time.sleep(TTL_WAIT_S)  # 默认 3s > 测试服务的 2s TTL

    # TTL 后：快照被清理/过期 → 410，且不泄露原文
    expired = session.get(_url(f"/tasks/{task_id}/comparison"), timeout=15)
    assert expired.status_code == 410, expired.text
    body = expired.json()
    assert body["code"] == "SNAPSHOT_EXPIRED"
    assert body["detail"] == "检测快照已过期或重启失效，请重新检测"
    assert set(body) == {"detail", "code", "error_id"}
    assert expired.headers.get("Cache-Control") == "no-store"
    assert expired.headers.get("Pragma") == "no-cache"
    for needle in ("13800138000", "张三"):
        assert needle not in expired.text

    expired_download = session.get(_url(f"/tasks/{task_id}/comparison/download"), timeout=15)
    assert expired_download.status_code == 410
    assert expired_download.json()["code"] == "SNAPSHOT_EXPIRED"
