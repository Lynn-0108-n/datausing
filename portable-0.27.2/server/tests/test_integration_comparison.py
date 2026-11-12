"""对比后端真实 HTTP 集成测试。

前置条件：按 ``tests/README.md`` 在隔离端口启动服务，并设置环境变量：

    $env:FLOWMASK_TEST_BASE_URL = 'http://127.0.0.1:8395'
    $env:FLOWMASK_TEST_MAX_UPLOAD_MB = '2'   # 与启动服务的 FLOWMASK_MAX_UPLOAD_MB 一致
    $env:FLOWMASK_TEST_LOG_FILE = '<服务 stdout 日志文件>'   # 可选，用于"日志无原文"断言

未设置 ``FLOWMASK_TEST_BASE_URL`` 时全部自动 skip。测试数据全部自造（占位姓名 +
公共示例手机号/身份证/邮箱），不涉及真实个人信息，也不把原文写入任何测试文件。
"""

from __future__ import annotations

import hashlib
import json
import os
import time
import uuid
from pathlib import Path

import pytest
import requests

pytestmark = pytest.mark.integration

BASE_URL = os.environ.get("FLOWMASK_TEST_BASE_URL", "").rstrip("/")
MAX_UPLOAD_MB = int(os.environ.get("FLOWMASK_TEST_MAX_UPLOAD_MB", "2"))
LOG_FILE = os.environ.get("FLOWMASK_TEST_LOG_FILE", "")
API = "/api/v1"

# ---- 自造样本（勿改成真实个人信息） --------------------------------------
SAMPLE_A = (
    "患者姓名：张三，手机 13800138000，身份证 11010519491231002X。\n"
    "备注：😀 emoji 在前，邮箱 zhangsan@example.com，结束。\n"
)
SAMPLE_A_MASKED = (
    "患者姓名：[已屏蔽:姓名]，手机 [已屏蔽:手机号]，身份证 [已屏蔽:身份证号]。\n"
    "备注：😀 emoji 在前，邮箱 [已屏蔽:邮箱]，结束。\n"
)
SAMPLE_A_CHANGES = [
    dict(start=5, end=7, masked_start=5, masked_end=13, type="name", strategy="block", level="L3", status="已脱敏"),
    dict(start=11, end=22, masked_start=17, masked_end=26, type="phone", strategy="block", level="L3", status="已脱敏"),
    dict(start=27, end=45, masked_start=31, masked_end=41, type="id_card", strategy="block", level="L3", status="已脱敏"),
    dict(start=64, end=84, masked_start=60, masked_end=68, type="email", strategy="block", level="L3", status="已脱敏"),
]
SAMPLE_B = "This is a normal meeting note. Lorem ipsum dolor sit amet, consectetur adipiscing elit."

SENSITIVE_NEEDLES = ["13800138000", "11010519491231002X", "zhangsan@example.com", "张三"]


# ---- 小工具 ----------------------------------------------------------------

def _url(path: str, base: str | None = None) -> str:
    return f"{base or BASE_URL}{API}{path}"


def wait_done(session: requests.Session, task_id: str, *, base: str | None = None, timeout: float = 30.0) -> dict:
    """轮询任务到 done；failed 视为测试失败。"""
    deadline = time.time() + timeout
    task: dict = {}
    while time.time() < deadline:
        resp = session.get(_url(f"/tasks/{task_id}", base), timeout=15)
        assert resp.status_code == 200, resp.text
        task = resp.json()
        if task["status"] in {"done", "failed"}:
            assert task["status"] == "done", f"任务失败: {task.get('error')}"
            return task
        time.sleep(0.05)
    raise AssertionError(f"任务 {task_id} 未在 {timeout}s 内完成: {task}")


def assert_no_original(text: str) -> None:
    for needle in SENSITIVE_NEEDLES:
        assert needle not in text, f"响应/日志中出现原文片段: {needle!r}"


def assert_compare_headers(resp: requests.Response) -> None:
    assert resp.headers.get("Cache-Control") == "no-store"
    assert resp.headers.get("Pragma") == "no-cache"


def assert_change_invariants(segment: dict) -> None:
    """逐条校验 original/masked codepoint 切片自洽（与单元测试同一口径）。"""
    original, masked = segment["original"], segment["masked"]
    prev_original_end = -1
    prev_masked_end = -1
    for change in segment["changes"]:
        start, end = change["start"], change["end"]
        masked_start, masked_end = change["masked_start"], change["masked_end"]
        assert 0 <= start < end <= len(original)
        assert 0 <= masked_start < masked_end <= len(masked)
        assert start >= prev_original_end and masked_start >= prev_masked_end
        prev_original_end, prev_masked_end = end, masked_end
        hit = original[start:end]
        replacement = masked[masked_start:masked_end]
        assert hit and replacement and hit != replacement
        assert change["status"] == "已脱敏"
        assert change["level"] in {"L1", "L2", "L3"}
        assert change["strategy"] in {"block", "partial"}
    rebuilt = original
    for change in reversed(segment["changes"]):
        rebuilt = (
            rebuilt[: change["start"]]
            + masked[change["masked_start"]: change["masked_end"]]
            + rebuilt[change["end"]:]
        )
    assert rebuilt == masked
    if segment["changes"]:
        first, last = segment["changes"][0], segment["changes"][-1]
        assert original[: first["start"]] == masked[: first["masked_start"]]
        assert original[last["end"]:] == masked[last["masked_end"]:]
    else:
        assert masked == original


# ---- fixtures --------------------------------------------------------------

@pytest.fixture(scope="session")
def base_url() -> str:
    if not BASE_URL:
        pytest.skip("未设置 FLOWMASK_TEST_BASE_URL")
    return BASE_URL


@pytest.fixture(scope="session")
def owner_session() -> requests.Session:
    return requests.Session()


@pytest.fixture(scope="session")
def batch_result(owner_session: requests.Session, base_url: str) -> requests.Response:
    files = [
        ("files", ("sample-a.txt", SAMPLE_A.encode("utf-8"), "text/plain")),
        ("files", ("sample-b.txt", SAMPLE_B.encode("utf-8"), "text/plain")),
    ]
    resp = owner_session.post(_url("/tasks/batch", base_url), files=files, timeout=60)
    assert resp.status_code == 201, resp.text
    return resp


@pytest.fixture(scope="session")
def batch_payload(batch_result: requests.Response) -> dict:
    return batch_result.json()


@pytest.fixture(scope="session")
def task_a(batch_payload: dict, owner_session: requests.Session) -> dict:
    return wait_done(owner_session, batch_payload["items"][0]["task_id"])


@pytest.fixture(scope="session")
def task_b(batch_payload: dict, owner_session: requests.Session) -> dict:
    return wait_done(owner_session, batch_payload["items"][1]["task_id"])


def _comparison(session: requests.Session, task_id: str) -> requests.Response:
    return session.get(_url(f"/tasks/{task_id}/comparison"), timeout=15)


# ---- 0. 服务健康 -----------------------------------------------------------

def test_service_is_healthy(base_url: str) -> None:
    resp = requests.get(_url("/health", base_url), timeout=10)
    assert resp.status_code == 200, resp.text
    health = resp.json()
    assert health["status"] == "ok"
    assert health["version"] == "0.27.2"
    # 隔离验证服务应关闭 L2/L3，避免依赖 8091/8092 外部推理服务
    assert health["enable_l2"] is False and health["enable_l3"] is False


# ---- 1~2. 批量建任务 + cookie 会话 ----------------------------------------

def test_batch_creates_tasks_and_sets_session_cookie(batch_result: requests.Response, batch_payload: dict) -> None:
    assert batch_result.status_code == 201
    assert batch_payload["total"] == 2 and batch_payload["accepted"] == 2
    items = batch_payload["items"]
    assert [item["filename"] for item in items] == ["sample-a.txt", "sample-b.txt"]
    for item in items:
        assert item["error"] == ""
        assert item["content_type"] == "text"
        assert item["segments"] == 1
        assert len(item["task_id"]) == 16
        int(item["task_id"], 16)  # 十六进制 task id
    # 会话 cookie 用于 comparison 的 owner 隔离
    set_cookie = batch_result.headers.get("set-cookie", "").lower()
    assert "flowmask_detection_session=" in set_cookie
    assert "httponly" in set_cookie
    assert "samesite=strict" in set_cookie
    assert "path=/api/v1" in set_cookie
    assert_no_original(batch_result.text)


def test_tasks_done_and_findings_match(task_a: dict, task_b: dict, owner_session: requests.Session) -> None:
    assert task_a["status"] == "done"
    assert task_a["total_segments"] == 1
    assert task_a["total_findings"] == 1
    assert task_b["status"] == "done"
    assert task_b["total_findings"] == 0

    resp = owner_session.get(_url(f"/tasks/{task_a['id']}/findings"), timeout=15)
    assert resp.status_code == 200
    page = resp.json()
    assert page["total"] == 1
    finding = page["items"][0]
    assert finding["masked"] == SAMPLE_A_MASKED
    assert finding["original_hash"] == hashlib.sha256(SAMPLE_A.encode("utf-8")).hexdigest()
    assert set(finding["types"]) == {"name", "phone", "id_card", "email"}
    assert finding["hits"] == 5
    assert finding["level"] == "L3"
    assert_no_original(resp.text)


# ---- 3. comparison 结构 / 偏移 / masked 一致性 ---------------------------

def test_comparison_structure_and_offsets(task_a: dict, owner_session: requests.Session) -> None:
    resp = _comparison(owner_session, task_a["id"])
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"].startswith("application/json")
    assert_compare_headers(resp)
    payload = resp.json()

    assert set(payload) == {
        "filename", "status", "coverage", "hits", "representation", "offset_unit", "segments",
    }
    assert payload["filename"] == "sample-a.txt"
    assert payload["status"] == "done"
    assert payload["coverage"] == "complete"
    assert payload["hits"] == 5
    assert payload["representation"] == "parsed-segments"
    assert payload["offset_unit"] == "unicode-codepoint"
    assert len(payload["segments"]) == 1

    segment = payload["segments"][0]
    assert set(segment) == {"path", "original", "masked", "changes"}
    assert segment["path"] == "$"
    assert segment["original"] == SAMPLE_A
    assert segment["masked"] == SAMPLE_A_MASKED
    assert segment["changes"] == SAMPLE_A_CHANGES
    assert_change_invariants(segment)

    # codepoint 切片语义（含 astral 平面 emoji）
    assert len("😀") == 1
    assert segment["original"][5:7] == "张三"
    assert segment["original"][11:22] == "13800138000"
    assert segment["original"][27:45] == "11010519491231002X"
    assert segment["original"][64:84] == "zhangsan@example.com"
    assert segment["masked"][5:13] == "[已屏蔽:姓名]"
    assert segment["masked"][31:41] == "[已屏蔽:身份证号]"
    assert segment["masked"][60:68] == "[已屏蔽:邮箱]"
    assert segment["masked"].endswith("结束。\n")

    assert_no_original(json.dumps(payload["segments"][0]["masked"], ensure_ascii=False))


def test_comparison_masked_equals_findings_masked(task_a: dict, owner_session: requests.Session) -> None:
    comparison = _comparison(owner_session, task_a["id"]).json()
    findings = owner_session.get(_url(f"/tasks/{task_a['id']}/findings"), timeout=15).json()
    by_path = {item["path"]: item for item in findings["items"]}
    for segment in comparison["segments"]:
        assert segment["masked"] == by_path[segment["path"]]["masked"]


def test_clean_document_has_empty_changes(task_b: dict, owner_session: requests.Session) -> None:
    resp = _comparison(owner_session, task_b["id"])
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["hits"] == 0
    segment = payload["segments"][0]
    assert segment["original"] == SAMPLE_B
    assert segment["masked"] == SAMPLE_B
    assert segment["changes"] == []
    assert_change_invariants(segment)


# ---- 4. download -----------------------------------------------------------

def test_download_equals_masked_segments(task_a: dict, owner_session: requests.Session) -> None:
    comparison = _comparison(owner_session, task_a["id"]).json()
    resp = owner_session.get(_url(f"/tasks/{task_a['id']}/comparison/download"), timeout=15)
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"].startswith("text/plain")
    assert resp.headers.get("Content-Disposition") == 'attachment; filename="flowmask-masked.txt"'
    assert_compare_headers(resp)
    body = resp.content.decode("utf-8")
    assert body == "\n".join(segment["masked"] for segment in comparison["segments"])
    assert body == SAMPLE_A_MASKED
    assert_no_original(body)


# ---- 5. 异常 / 安全边界 ----------------------------------------------------

def test_missing_task_404(owner_session: requests.Session) -> None:
    missing = uuid.uuid4().hex[:16]
    for path in (f"/tasks/{missing}/comparison", f"/tasks/{missing}/comparison/download"):
        resp = owner_session.get(_url(path), timeout=15)
        assert resp.status_code == 404
        assert resp.json() == {"detail": "任务不存在"}
        assert_compare_headers(resp)
        assert_no_original(resp.text)


def test_single_file_task_without_snapshot_410(owner_session: requests.Session) -> None:
    """POST /tasks（单文件）不传 comparison_owner → 任务 done 但 comparison 必为 410。"""
    resp = owner_session.post(
        _url("/tasks"),
        files={"file": ("single.txt", SAMPLE_A.encode("utf-8"), "text/plain")},
        timeout=30,
    )
    assert resp.status_code == 200, resp.text
    task_id = resp.json()["task_id"]
    wait_done(owner_session, task_id)

    for path in (f"/tasks/{task_id}/comparison", f"/tasks/{task_id}/comparison/download"):
        resp = owner_session.get(_url(path), timeout=15)
        assert resp.status_code == 410
        body = resp.json()
        assert body["code"] == "SNAPSHOT_EXPIRED"
        assert body["detail"] == "检测快照已过期或重启失效，请重新检测"
        assert body["error_id"]
        assert_compare_headers(resp)
        assert_no_original(resp.text)


def test_ownership_isolation_403(base_url: str, task_a: dict) -> None:
    # (a) 不带任何 cookie 的新会话：owner 为空 → 403
    anonymous = requests.Session()
    resp = _comparison(anonymous, task_a["id"])
    assert resp.status_code == 403
    body = resp.json()
    assert body["code"] == "SNAPSHOT_FORBIDDEN"
    assert set(body) == {"detail", "code", "error_id"}
    assert_no_original(resp.text)
    assert_compare_headers(resp)

    # (b) 另一个有自己 cookie 的会话（自己批量上传过任务）：owner 不同 → 403
    other = requests.Session()
    other_resp = other.post(
        _url("/tasks/batch", base_url),
        files=[("files", ("other.txt", SAMPLE_B.encode("utf-8"), "text/plain"))],
        timeout=30,
    )
    assert other_resp.status_code == 201
    assert "flowmask_detection_session=" in other_resp.headers.get("set-cookie", "").lower()
    resp = _comparison(other, task_a["id"])
    assert resp.status_code == 403
    assert resp.json()["code"] == "SNAPSHOT_FORBIDDEN"
    assert_no_original(resp.text)


def _slow_json(entries: int) -> bytes:
    """多 segment JSON：解析/逐段检测明显慢于小 txt，用于稳定复现"任务未完成" 409。"""
    body = "{" + ",".join(f'"k{i}":"a"' for i in range(entries)) + "}"
    return body.encode("utf-8")


def test_unfinished_task_409(base_url: str) -> None:
    session = requests.Session()
    for entries, files_n in ((30_000, 3), (60_000, 3)):
        blob = _slow_json(entries)
        if len(blob) > (MAX_UPLOAD_MB * 1024 * 1024) * 0.8:
            pytest.skip("慢任务样本超过当前上传上限，跳过 409 竞态用例")
        files = [("files", (f"slow{index}.json", blob, "application/json")) for index in range(files_n)]
        resp = session.post(_url("/tasks/batch", base_url), files=files, timeout=60)
        assert resp.status_code == 201, resp.text
        items = resp.json()["items"]
        assert all(item["task_id"] for item in items), items
        last_task = items[-1]["task_id"]
        probe = _comparison(session, last_task)
        if probe.status_code == 409:
            assert probe.json()["detail"] == "任务未完成或脱敏失败"
            assert_compare_headers(probe)
            assert_no_original(probe.text)
            return
        assert probe.status_code in {200, 410}, probe.text
        wait_done(session, last_task)
    pytest.skip("任务在 comparison 请求前已完成，无法稳定复现 409 竞态（服务过快）")


def test_oversize_upload_413(base_url: str) -> None:
    payload = b"a" * (MAX_UPLOAD_MB * 1024 * 1024 + 4096)
    session = requests.Session()
    resp = session.post(
        _url("/tasks", base_url),
        files={"file": ("too-big.txt", payload, "text/plain")},
        timeout=60,
    )
    assert resp.status_code == 413
    assert resp.json()["detail"] == f"文件超过上限 {MAX_UPLOAD_MB} MB"
    assert len(resp.content) < 2048  # 错误响应不夹带文件内容

    # 批量接口是"逐文件失败"：一个超限文件不应拖垮整批
    batch = session.post(
        _url("/tasks/batch", base_url),
        files=[
            ("files", ("too-big.txt", payload, "text/plain")),
            ("files", ("ok.txt", SAMPLE_B.encode("utf-8"), "text/plain")),
        ],
        timeout=60,
    )
    assert batch.status_code == 201
    items = batch.json()["items"]
    assert items[0]["error"] == f"文件超过上限 {MAX_UPLOAD_MB} MB"
    assert items[1]["error"] == "" and items[1]["task_id"]


# ---- 6. 日志无原文 ---------------------------------------------------------

def test_server_log_has_no_original_text() -> None:
    if not LOG_FILE:
        pytest.skip("未设置 FLOWMASK_TEST_LOG_FILE，跳过日志检查")
    log_text = Path(LOG_FILE).read_text(encoding="utf-8", errors="replace")
    assert_no_original(log_text)
