"""动态脱敏视图：授权切换（服务端判定）、级别覆盖与审计留痕。"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.identify.pipeline import sha256_text

SENSITIVE_TEXT = "客户张三，手机 13800138000，身份证 11010519491231002X"
PHONE = "13800138000"
ID_CARD = "11010519491231002X"


def _set_reveal_operators(monkeypatch, value: str | None) -> None:
    """模拟服务端配置：FLOWMASK_REVEAL_OPERATORS（None 表示未配置）。"""
    if value is None:
        monkeypatch.delenv("FLOWMASK_REVEAL_OPERATORS", raising=False)
    else:
        monkeypatch.setenv("FLOWMASK_REVEAL_OPERATORS", value)
    get_settings.cache_clear()


def test_policy_table(client: TestClient) -> None:
    body = client.get("/api/v1/dynamic/policy").json()
    levels = {row["level"] for row in body["levels"]}
    assert levels == {"L3", "L2", "L1", "SAFE"}
    l3 = next(row for row in body["levels"] if row["level"] == "L3")
    assert "不展示任何原文" in l3["without_auth"]
    assert "展示原文" in l3["with_auth"]


def test_render_masked_by_default(client: TestClient) -> None:
    response = client.post("/api/v1/dynamic/render", json={"text": SENSITIVE_TEXT})
    assert response.status_code == 200
    body = response.json()
    assert body["authorized"] is False
    assert body["policy"] == "masked"
    assert body["level"] == "L3"
    assert body["level_label"] == "高危"
    # 未授权：只能拿到脱敏副本，原文里的号码一个都不能出现
    assert PHONE not in body["text"]
    assert ID_CARD not in body["text"]
    assert "已屏蔽" in body["masked"]
    # 命中区间仍返回，前端才能在原文上高亮
    assert body["spans"]
    assert {span["type"] for span in body["spans"]} >= {"phone", "id_card"}
    assert "手机号" in body["type_labels"]


def test_render_authorized_returns_original_and_audits(
    client: TestClient, monkeypatch
) -> None:
    # 服务端白名单包含 alice，才允许她看原文（客户端自报不算数）
    _set_reveal_operators(monkeypatch, "alice")
    response = client.post(
        "/api/v1/dynamic/render",
        json={"text": SENSITIVE_TEXT, "authorized": True},
        headers={"X-Operator-Id": "alice", "X-User-Id": "bob"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["authorized"] is True
    assert body["policy"] == "full_access"
    assert body["text"] == SENSITIVE_TEXT
    assert body["operator_id"] == "alice"
    assert "审计链" in body["note"]

    # 授权访问必须留痕：审计链里能找到这条动态渲染，且链仍然完整
    entries = client.get("/api/v1/audit?limit=10").json()["items"]
    dynamic = [entry for entry in entries if entry["rule_id"] == "dynamic-render"]
    assert dynamic, "授权渲染没有写审计记录"
    assert dynamic[0]["operator_id"] == "alice"
    assert dynamic[0]["user_id"] == "bob"
    assert client.get("/api/v1/audit/verify").json()["ok"] is True


def test_client_claim_authorized_without_allowlist_stays_masked(
    client: TestClient, monkeypatch
) -> None:
    """客户端自报 authorized=true，但服务端白名单没有该操作人 → 仍只返回脱敏副本。"""
    _set_reveal_operators(monkeypatch, "")
    response = client.post(
        "/api/v1/dynamic/render",
        json={"text": SENSITIVE_TEXT, "authorized": True},
        headers={"X-Operator-Id": "alice"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["authorized"] is False
    assert body["policy"] == "masked"
    assert PHONE not in body["text"]
    assert ID_CARD not in body["text"]
    assert "服务端授权不足" in body["note"]
    assert "FLOWMASK_REVEAL_OPERATORS" in body["note"]

    # 审计链记录的应是「实际展示出去的内容」（脱敏副本），不是客户端声称的原文
    entries = client.get("/api/v1/audit?limit=10").json()["items"]
    dynamic = [entry for entry in entries if entry["rule_id"] == "dynamic-render"]
    assert dynamic and dynamic[0]["output_hash"] == sha256_text(body["text"])
    assert client.get("/api/v1/audit/verify").json()["ok"] is True


def test_server_allowlist_grants_original(client: TestClient, monkeypatch) -> None:
    """白名单命中 + 客户端请求授权 → 返回原文。"""
    _set_reveal_operators(monkeypatch, "alice,system")
    body = client.post(
        "/api/v1/dynamic/render",
        json={"text": SENSITIVE_TEXT, "authorized": True},
        headers={"X-Operator-Id": "alice"},
    ).json()
    assert body["authorized"] is True
    assert body["policy"] == "full_access"
    assert body["text"] == SENSITIVE_TEXT


def test_allowlisted_operator_stays_masked_without_request(
    client: TestClient, monkeypatch
) -> None:
    """白名单只决定「可以看」，不代替客户端的授权开关：没请求授权仍是脱敏副本。"""
    _set_reveal_operators(monkeypatch, "alice")
    body = client.post(
        "/api/v1/dynamic/render",
        json={"text": SENSITIVE_TEXT},
        headers={"X-Operator-Id": "alice"},
    ).json()
    assert body["authorized"] is False
    assert body["policy"] == "masked"
    assert PHONE not in body["text"]


def test_render_mask_level_override(client: TestClient) -> None:
    """强制按 L3 脱敏：即便只是中危手机号，也整段屏蔽。"""
    response = client.post(
        "/api/v1/dynamic/render",
        json={"text": "联系电话 13800138000", "mask_level": "L3"},
    )
    body = response.json()
    assert response.status_code == 200
    assert PHONE not in body["text"]
    assert "[已屏蔽:手机号]" in body["text"]


def test_render_invalid_mask_level(client: TestClient) -> None:
    response = client.post(
        "/api/v1/dynamic/render",
        json={"text": "任意文本", "mask_level": "L9"},
    )
    assert response.status_code == 400
    assert "mask_level" in response.json()["detail"]


def test_render_safe_text_unchanged(client: TestClient) -> None:
    text = "今天天气不错，适合出门散步。"
    body = client.post("/api/v1/dynamic/render", json={"text": text}).json()
    assert body["level"] == "SAFE"
    assert body["text"] == text
    assert body["masked"] == text
    assert body["spans"] == []
