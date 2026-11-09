"""接口层端到端测试：检测、任务、导出、统计、规则、审计、鉴权。"""

from __future__ import annotations

import json
import time

ID_CARD = "11010519491231002X"
PHONE = "13800138000"
CUSTOM_PATTERN = r"工号[:：]\s*([A-Z]{2}\d{6})"


def _wait_done(client, task_id: str, timeout: float = 5.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        body = client.get(f"/api/v1/tasks/{task_id}").json()
        if body["status"] in {"done", "failed"}:
            return body
        time.sleep(0.05)
    raise AssertionError("任务在超时时间内未完成")


def test_health(client) -> None:
    body = client.get("/api/v1/health").json()
    assert body["status"] == "ok" and body["version"]


def test_detect_text_returns_masked_only(client) -> None:
    response = client.post("/api/v1/detect", json={"text": f"身份证号：{ID_CARD}"})
    assert response.status_code == 200
    body = response.json()
    finding = body["findings"][0]
    assert finding["level"] == "L3"
    assert "110105********002X" in finding["masked"]
    assert ID_CARD not in finding["masked"]
    # 整个响应里不能出现原文敏感值（只允许哈希与脱敏副本）
    assert ID_CARD not in json.dumps(body, ensure_ascii=False)
    # Day 3.3：检测自动建 task，task_id 非空
    assert body.get("task_id"), "检测应自动创建 task 记录"
    # 创建的 task 能在任务列表里查到
    task = client.get(f"/api/v1/tasks/{body['task_id']}").json()
    assert task["id"] == body["task_id"]


def test_detect_file_returns_task_id(client) -> None:
    """Day 3.3：文件入口检测也应自动建 task。"""
    response = client.post(
        "/api/v1/detect/file",
        files={"file": ("a.txt", f"联系电话 {PHONE}\n".encode(), "text/plain")},
    )
    assert response.status_code == 200
    assert response.json()["task_id"]


def test_batch_upload_masks_id_card_with_stars(client) -> None:
    """批量导入的脱敏副本必须对身份证号做星号遮掩，不能回退成类别占位。"""
    response = client.post(
        "/api/v1/tasks/batch",
        files=[
            ("files", ("a.txt", f"身份证号：{ID_CARD}\n".encode(), "text/plain")),
            ("files", ("b.txt", "无敏感内容\n".encode(), "text/plain")),
        ],
    )
    assert response.status_code == 201
    body = response.json()
    assert body["accepted"] == 2
    task_id = body["items"][0]["task_id"]
    assert _wait_done(client, task_id)["status"] == "done"
    findings = client.get(f"/api/v1/tasks/{task_id}/findings").json()["items"]
    assert findings
    assert "110105********002X" in findings[0]["masked"]
    assert ID_CARD not in findings[0]["masked"]


def test_detect_returns_highlight_spans(client) -> None:
    text = f"客户张三，手机 {PHONE}，身份证 {ID_CARD}"
    body = client.post("/api/v1/detect", json={"text": text}).json()
    spans = body["findings"][0]["spans"]
    assert spans, "应返回命中区间供前端高亮"
    # 区间必须能切回原文命中值，且类别键/中文名都在
    types = {span["type"] for span in spans}
    assert {"phone", "id_card"} <= types
    cut = {text[span["start"]:span["end"]] for span in spans}
    assert PHONE in cut and ID_CARD in cut
    assert all(span["label"] and span["rule"] for span in spans)


def test_ui_page_and_root_redirect(client) -> None:
    page = client.get("/ui/")
    assert page.status_code == 200
    assert "FlowMask" in page.text and "左右对照" in page.text
    redirect = client.get("/", follow_redirects=False)
    assert redirect.status_code in {302, 307}
    # 构建过正式前端（web/dist）时优先跳 /app/，否则回落到自带单页 /ui
    assert redirect.headers["location"] in {"/ui", "/app/"}


def test_delete_single_task_cascades_findings_and_audit(client) -> None:
    """删除单条任务：task / findings / audit_log 级联清理。"""
    # 建一个任务
    body = client.post("/api/v1/detect", json={"text": f"身份证号：{ID_CARD}"}).json()
    task_id = body["task_id"]
    assert task_id
    # 删除前应能查到
    assert client.get(f"/api/v1/tasks/{task_id}").status_code == 200
    # 删除
    resp = client.delete(f"/api/v1/tasks/{task_id}")
    assert resp.status_code == 200
    assert resp.json() == {"task_id": task_id, "deleted": True}
    # 删除后再查应 404
    assert client.get(f"/api/v1/tasks/{task_id}").status_code == 404
    # findings 也应查不到（接口返回 404 或空 items）
    findings_resp = client.get(f"/api/v1/tasks/{task_id}/findings")
    assert findings_resp.status_code in {200, 404}
    if findings_resp.status_code == 200:
        assert findings_resp.json()["items"] == []


def test_delete_nonexistent_task_returns_404(client) -> None:
    resp = client.delete("/api/v1/tasks/nonexistent_id_12345")
    assert resp.status_code == 404


def test_delete_tasks_batch(client) -> None:
    """批量删除：返回实际删除条数，不存在的 task_id 不计入。"""
    # 建两个任务
    id1 = client.post("/api/v1/detect", json={"text": f"手机 {PHONE}"}).json()["task_id"]
    id2 = client.post("/api/v1/detect", json={"text": f"身份证号：{ID_CARD}"}).json()["task_id"]
    # 批量删除 + 一个不存在的（TestClient 的 delete 不支持 json，用 request）
    resp = client.request(
        "DELETE",
        "/api/v1/tasks",
        json={"task_ids": [id1, id2, "nonexistent_xyz"]},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["requested"] == 3
    assert body["deleted"] == 2
    # 验证确实删了
    assert client.get(f"/api/v1/tasks/{id1}").status_code == 404
    assert client.get(f"/api/v1/tasks/{id2}").status_code == 404


def test_delete_tasks_batch_rejects_bad_payload(client) -> None:
    # 空 task_ids
    resp = client.request("DELETE", "/api/v1/tasks", json={"task_ids": []})
    assert resp.status_code == 400
    # 非字符串
    resp = client.request("DELETE", "/api/v1/tasks", json={"task_ids": [12345]})
    assert resp.status_code == 400
    # 缺字段
    resp = client.request("DELETE", "/api/v1/tasks", json={"foo": "bar"})
    assert resp.status_code == 400


def test_frontend_dist_is_optional(monkeypatch) -> None:
    """未构建正式前端时必须优雅降级：没有 /app 挂载，根路径回落到 /ui。"""
    from pathlib import Path

    import app.main as main_module
    from fastapi.testclient import TestClient

    monkeypatch.setattr(main_module, "FRONTEND_DIR", Path("/nonexistent-flowmask-dist"))
    fallback = TestClient(main_module.create_app())
    assert fallback.get("/app/").status_code == 404
    redirect = fallback.get("/", follow_redirects=False)
    assert redirect.headers["location"] == "/ui"


def test_frontend_serves_built_assets() -> None:
    """构建过正式前端时 /app/ 能直接打开（Vue3 页面），无构建环境则跳过。"""
    from pathlib import Path

    import pytest
    from fastapi.testclient import TestClient

    import app.main as main_module

    dist = Path(main_module.__file__).resolve().parents[2] / "web" / "dist"
    if not dist.is_dir():
        pytest.skip("未构建 web/dist，跳过正式前端静态资源用例")
    page = TestClient(main_module.create_app()).get("/app/")
    assert page.status_code == 200
    assert "<div id=\"app\">" in page.text


def test_detect_file(client) -> None:
    response = client.post(
        "/api/v1/detect/file",
        files={"file": ("a.txt", f"联系电话 {PHONE}\n".encode(), "text/plain")},
    )
    assert response.status_code == 200
    assert response.json()["findings"][0]["level"] == "L2"


def test_detect_invalid_level_is_400(client) -> None:
    response = client.post("/api/v1/detect", json={"text": "abc", "min_level": "L9"})
    assert response.status_code == 400


def test_detect_unsupported_upload_is_415(client) -> None:
    response = client.post("/api/v1/detect/file", files={"file": ("a.bin", b"\x00\x01")})
    assert response.status_code == 415


def test_task_flow_findings_export_and_stats(client) -> None:
    files = {"file": ("a.txt", f"姓名：张三\n联系电话 {PHONE}\n".encode(), "text/plain")}
    task_id = client.post("/api/v1/tasks", files=files).json()["task_id"]
    task = _wait_done(client, task_id)
    assert task["status"] == "done" and task["total_findings"] >= 1

    findings = client.get(f"/api/v1/tasks/{task_id}/findings").json()
    assert findings["total"] >= 1
    assert findings["items"][0]["masked"]

    cases = {
        "jsonl": '"type": "meta"',
        "json": '"findings"',
        "csv": "path,level",
        "sql": "INSERT INTO findings",
    }
    for fmt, marker in cases.items():
        response = client.get(f"/api/v1/tasks/{task_id}/export", params={"format": fmt})
        assert response.status_code == 200, fmt
        assert marker in response.text, fmt

    stats = client.get("/api/v1/stats").json()
    assert stats["total_findings"] >= 1
    assert stats["by_level"]["L2"] >= 1 or stats["by_level"]["L3"] >= 1
    # 看板类别要带中文名（前端优先读 key），英文键保留在 type 里
    by_type = {item["type"]: item["key"] for item in stats["by_type"]}
    assert by_type, "类别统计不应为空"
    assert by_type.get("phone") == "手机号", stats["by_type"]

    # 审计记录在任务完成后应已写入，且链条可校验
    audit = client.get("/api/v1/audit").json()
    assert audit["total"] >= 1
    verify = client.get("/api/v1/audit/verify").json()
    assert verify["ok"] is True

    exported = client.get("/api/v1/audit/export", params={"format": "jsonl"})
    assert exported.status_code == 200 and exported.text.strip()


def test_task_not_found_and_bad_export_format(client) -> None:
    assert client.get("/api/v1/tasks/nope").status_code == 404
    assert client.get("/api/v1/tasks/nope/export").status_code == 404
    files = {"file": ("a.txt", b"plain text", "text/plain")}
    task_id = client.post("/api/v1/tasks", files=files).json()["task_id"]
    _wait_done(client, task_id)
    assert client.get(f"/api/v1/tasks/{task_id}/export", params={"format": "xml"}).status_code == 400


def test_custom_rule_is_effective_and_reversible(client) -> None:
    # 内置规则条数会随迭代增减（如新增 sk- 密钥规则），这里用动态基线，
    # 只要求"是一套完整规则"，避免每加一条规则就改测试。
    baseline = len(client.get("/api/v1/rules").json())
    assert baseline >= 12
    created = client.post(
        "/api/v1/rules",
        json={
            "id": "employee_no",
            "name": "工号",
            "type": "secret",
            "pattern": CUSTOM_PATTERN,
            "weight": 1.0,
            "confidence": 0.95,
        },
    )
    assert created.status_code == 201

    hit = client.post("/api/v1/detect", json={"text": "工号：AB123456"}).json()
    assert hit["findings"][0]["level"] == "L3"

    client.patch("/api/v1/rules/employee_no", json={"enabled": False})
    assert client.post("/api/v1/detect", json={"text": "工号：AB123456"}).json()["findings"] == []

    # 停用内置规则：手机号规则下线后，纯手机号不再命中
    assert client.patch("/api/v1/rules/mobile", json={"enabled": False}).status_code == 200
    assert client.post("/api/v1/detect", json={"text": f"电话 {PHONE}"}).json()["findings"] == []

    # 删除覆盖行即恢复内置规则
    assert client.delete("/api/v1/rules/mobile").status_code == 200
    assert client.post("/api/v1/detect", json={"text": f"电话 {PHONE}"}).json()["findings"]
    assert client.delete("/api/v1/rules/employee_no").status_code == 200
    assert len(client.get("/api/v1/rules").json()) == baseline


def test_custom_rule_without_id_gets_generated(client) -> None:
    """前端表单只有规则名：英文名直接当 id，中文名回退 custom_ 前缀。"""
    named = client.post(
        "/api/v1/rules",
        json={"name": "employee_no", "type": "secret", "pattern": CUSTOM_PATTERN, "weight": 0.6},
    )
    assert named.status_code == 201, named.text
    assert named.json()["id"] == "employee_no"
    assert named.json()["builtin"] is False

    unnamed = client.post(
        "/api/v1/rules",
        json={"name": "工号", "type": "secret", "pattern": CUSTOM_PATTERN, "weight": 0.6},
    )
    assert unnamed.status_code == 201, unnamed.text
    generated = unnamed.json()
    assert generated["id"].startswith("custom_")
    assert generated["name"] == "工号"

    # 生成出来的规则同样即时生效
    hit = client.post("/api/v1/detect", json={"text": "工号：AB123456"}).json()
    assert hit["findings"], hit

    assert client.delete("/api/v1/rules/employee_no").status_code == 200
    assert client.delete(f"/api/v1/rules/{generated['id']}").status_code == 200


def test_rule_validation_errors(client) -> None:
    bad_type = client.post(
        "/api/v1/rules",
        json={"id": "x_rule", "name": "x", "type": "unknown", "pattern": "abc", "weight": 0.5},
    )
    assert bad_type.status_code == 422
    bad_regex = client.post(
        "/api/v1/rules",
        json={"id": "x_rule", "name": "x", "type": "secret", "pattern": "([", "weight": 0.5},
    )
    assert bad_regex.status_code == 422
    duplicate = client.post(
        "/api/v1/rules",
        json={"id": "mobile", "name": "x", "type": "secret", "pattern": "abc", "weight": 0.5},
    )
    assert duplicate.status_code == 409


def test_bearer_token_enforced(tmp_env, monkeypatch) -> None:
    monkeypatch.setenv("FLOWMASK_API_TOKEN", "s3cret")
    from fastapi.testclient import TestClient

    from app.core.config import get_settings
    from app.main import create_app

    get_settings.cache_clear()
    with TestClient(create_app()) as protected:
        assert protected.get("/api/v1/health").status_code == 401
        assert protected.get("/api/v1/health", headers={"Authorization": "Bearer wrong"}).status_code == 401
        ok = protected.get("/api/v1/health", headers={"Authorization": "Bearer s3cret"})
        assert ok.status_code == 200

def test_health_reports_ner_availability(client) -> None:
    """默认没开 NER 时，/health 明确告诉前端“不可用”，前端据此隐藏开关。"""
    body = client.get("/api/v1/health").json()
    assert body["ner"] is False
    assert body["ner_backend"] == ""


def test_detect_enable_ner_switch(client, monkeypatch) -> None:
    """enable_ner 只在显式打开时生效：打开后补出地名，关闭时保持纯 L1 结果。"""
    from app.api import deps
    from app.identify.ner import NerSpan
    from app.identify.pipeline import DetectionPipeline

    class FakeNer:
        name = "fake-ner"

        def available(self) -> bool:
            return True

        def detect(self, text: str) -> list[NerSpan]:
            start = text.find("宁德")
            return [NerSpan(start=start, end=start + 2, label="B-LOC")] if start >= 0 else []

    monkeypatch.setattr(deps, "_pipeline", DetectionPipeline(ner=FakeNer()))  # type: ignore[arg-type]
    # 用「宁德」而不是「福州」：地名库只收省会/主要地级市，宁德既不在地名库、
    # 又没有省/市/区/路后缀与方位词，L1 一定抓不到，只有 NER 能补出来。
    text = "他昨天从宁德赶过来，商量下季度的方案。"

    plain = client.post("/api/v1/detect", json={"text": text}).json()
    assert plain["findings"] == []

    with_ner = client.post("/api/v1/detect", json={"text": text, "enable_ner": True}).json()
    assert with_ner["findings"], "开 NER 后应该补出地名"
    finding = with_ner["findings"][0]
    assert "address" in finding["types"]
    span = next(item for item in finding["spans"] if item["type"] == "address")
    assert text[span["start"] : span["end"]] == "宁德"
    assert "ner" in finding["model_chain"]

