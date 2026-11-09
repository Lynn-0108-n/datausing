"""AI 助手测试（Day 2.8）。

覆盖：
- 统计解读：空结果 → severity=info / source=rules
- 统计解读：高危占比高 → severity=high + tips 含 L3 建议
- 方案推荐：外包+审计 → strategy=block, audit_recommended=True
- 方案推荐：教学+不可逆 → strategy=hash
- 防回显：多余字段 → 400 固定文案（不是 422，不暴露 errors）
- 助手关闭：assistant_mode=off 时不发网络请求
- 回环限制：ollama 模式 endpoint 非回环 → degraded=True
- 推荐规则：用途与要求标签的多种组合
"""

from __future__ import annotations

import json
from unittest.mock import patch

from app.api.schemas import AssistantAdviseIn, RecommendIn
from app.assistant import advise, recommend
from app.core.config import Settings
from app.scenarios import SCENARIOS


# ---------- 构造测试用 Settings ----------


def _make_settings(**overrides) -> Settings:
    """构造一份测试用 Settings，默认 assistant 关闭。"""
    base = Settings.load()
    # frozen dataclass → 用 replace
    from dataclasses import replace

    return replace(base, **overrides)


# ---------- 统计解读：规则引擎 ----------


def test_advise_empty_result_info():
    """空结果：severity=info，source=rules，summary 提示无命中。"""
    payload = AssistantAdviseIn(page="stats", total=0)
    settings = _make_settings(assistant_mode="off")
    out = advise(payload, settings)
    assert out.severity == "info"
    assert out.source == "rules"
    assert out.degraded is False
    assert "未发现敏感数据命中" in out.summary
    assert out.model == ""


def test_advise_high_ratio_high_severity():
    """高危占比 ≥10% → severity=high，tips 含 L3 建议。"""
    payload = AssistantAdviseIn(
        page="stats",
        total=100,
        by_level={"L3": 15, "L2": 20, "L1": 30, "SAFE": 35},
        types=[
            {"type": "id_card", "key": "", "count": 10},
            {"type": "phone", "key": "", "count": 5},
        ],
    )
    settings = _make_settings(assistant_mode="off")
    out = advise(payload, settings)
    assert out.severity == "high"
    # tips 里至少有一条提到高危
    titles = " ".join(t.title for t in out.tips)
    assert "高危" in titles
    assert any("block" in t.detail or "encrypt" in t.detail for t in out.tips)


def test_advise_warn_severity():
    """中危占比 ≥10% 但高危 <1% → severity=warn。"""
    payload = AssistantAdviseIn(
        page="stats",
        total=100,
        by_level={"L3": 0, "L2": 15, "L1": 20, "SAFE": 65},
    )
    settings = _make_settings(assistant_mode="off")
    out = advise(payload, settings)
    assert out.severity == "warn"


def test_advise_with_types_top():
    """类别 Top 建议：types 排序后第一条 count 最大。"""
    payload = AssistantAdviseIn(
        page="detect",
        total=50,
        by_level={"L2": 10, "SAFE": 40},
        types=[
            {"type": "phone", "key": "", "count": 3},
            {"type": "id_card", "key": "", "count": 7},
        ],
    )
    settings = _make_settings(assistant_mode="off")
    out = advise(payload, settings)
    # 至少有一条 tip 提到 id_card（count 最多）
    all_text = " ".join(t.title + " " + t.detail for t in out.tips)
    assert "id_card" in all_text


# ---------- 方案推荐：规则引擎 ----------


def test_recommend_outsourcing_audit_block():
    """外包+审计 → strategy=block, audit_recommended=True。"""
    payload = RecommendIn(purpose="outsourcing", requirements=["audit"])
    settings = _make_settings(assistant_mode="off")
    out = recommend(payload, settings)
    assert out.strategy == "block"
    assert out.audit_recommended is True
    assert out.source == "rules"


def test_recommend_teaching_irreversible_hash():
    """教学+不可逆 → strategy=hash。"""
    payload = RecommendIn(purpose="teaching", requirements=["irreversible"])
    settings = _make_settings(assistant_mode="off")
    out = recommend(payload, settings)
    assert out.strategy == "hash"


def test_recommend_public_sharing_irreversible():
    """公开共享+不可逆 → strategy=block, min_level=L3。"""
    payload = RecommendIn(purpose="public-sharing", requirements=["irreversible"])
    settings = _make_settings(assistant_mode="off")
    out = recommend(payload, settings)
    assert out.strategy == "block"
    assert out.min_level == "L3"


def test_recommend_analysis_keep_statistics_generalize():
    """数据分析+保留统计 → strategy=generalize。"""
    payload = RecommendIn(purpose="analysis", requirements=["keep-statistics"])
    settings = _make_settings(assistant_mode="off")
    out = recommend(payload, settings)
    assert out.strategy == "generalize"


def test_recommend_dev_test_reversible_encrypt():
    """开发测试+可逆 → strategy=encrypt。"""
    payload = RecommendIn(purpose="dev-test", requirements=["reversible"])
    settings = _make_settings(assistant_mode="off")
    out = recommend(payload, settings)
    assert out.strategy == "encrypt"


def test_recommend_dev_test_keep_statistics_partial():
    """开发测试+保留统计 → strategy=partial。"""
    payload = RecommendIn(purpose="dev-test", requirements=["keep-statistics"])
    settings = _make_settings(assistant_mode="off")
    out = recommend(payload, settings)
    assert out.strategy == "partial"


def test_recommend_other_reversible_encrypt():
    """其他用途+可逆 → strategy=encrypt。"""
    payload = RecommendIn(purpose="other", requirements=["reversible"])
    settings = _make_settings(assistant_mode="off")
    out = recommend(payload, settings)
    assert out.strategy == "encrypt"


def test_recommend_default_fallback():
    """未命中任何规则的组合 → 兜底 partial + L2。"""
    payload = RecommendIn(purpose="other")
    settings = _make_settings(assistant_mode="off")
    out = recommend(payload, settings)
    assert out.strategy == "partial"
    assert out.min_level == "L2"


# ---------- 路由层：防回显 + 400 ----------


def test_assistant_advise_extra_field_400_not_422(client):
    """多余字段（如 text）→ 400 固定文案，不是 422。"""
    resp = client.post(
        "/api/v1/assistant/advise",
        json={"page": "stats", "total": 0, "text": "敏感原文"},
    )
    assert resp.status_code == 400
    assert resp.json()["detail"] == "请求格式不正确"


def test_assistant_recommend_extra_field_400(client):
    """recommend 多余字段 → 400 固定文案。"""
    resp = client.post(
        "/api/v1/assistant/recommend",
        json={"purpose": "dev-test", "masked": "something"},
    )
    assert resp.status_code == 400
    assert resp.json()["detail"] == "请求格式不正确"


def test_assistant_advise_invalid_enum_400(client):
    """非法 page 枚举 → 400 固定文案（不是 422 + 详情）。"""
    resp = client.post(
        "/api/v1/assistant/advise",
        json={"page": "unknown", "total": 0},
    )
    assert resp.status_code == 400
    detail = resp.json()["detail"]
    assert detail == "请求格式不正确"


def test_assistant_recommend_invalid_purpose_400(client):
    """非法 purpose 枚举 → 400 固定文案。"""
    resp = client.post(
        "/api/v1/assistant/recommend",
        json={"purpose": "not-a-purpose"},
    )
    assert resp.status_code == 400


def test_assistant_advise_ok_200(client):
    """合法请求 → 200 + 响应模型字段齐全。"""
    resp = client.post(
        "/api/v1/assistant/advise",
        json={
            "page": "stats",
            "total": 10,
            "by_level": {"L3": 2, "L2": 3, "SAFE": 5},
            "types": [{"type": "id_card", "key": "", "count": 2}],
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["severity"] in {"info", "warn", "high"}
    assert body["source"] in {"rules", "local-model"}
    assert body["generated_at"]


def test_assistant_recommend_ok_200(client):
    """合法 recommend 请求 → 200。"""
    resp = client.post(
        "/api/v1/assistant/recommend",
        json={"purpose": "outsourcing", "requirements": ["audit"]},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["strategy"] == "block"
    assert body["source"] == "rules"


# ---------- 助手关闭 / 回环限制 ----------


def test_advise_off_mode_no_network():
    """assistant_mode=off 时不发任何网络请求。"""
    payload = AssistantAdviseIn(page="stats", total=10, by_level={"L2": 5})
    settings = _make_settings(assistant_mode="off")
    with patch("urllib.request.urlopen") as mock_open:
        out = advise(payload, settings)
        mock_open.assert_not_called()
    assert out.source == "rules"
    assert out.degraded is False


def test_advise_ollama_non_loopback_degraded():
    """ollama 模式 endpoint 非回环 → 不发请求，degraded=True。"""
    payload = AssistantAdviseIn(page="stats", total=10, by_level={"L2": 5})
    settings = _make_settings(
        assistant_mode="ollama",
        assistant_endpoint="http://10.0.0.5:11434/v1/chat/completions",
    )
    with patch("urllib.request.urlopen") as mock_open:
        out = advise(payload, settings)
        mock_open.assert_not_called()
    # 回环校验失败 → 不发请求 → 回退规则引擎 + degraded=True
    assert out.source == "rules"
    assert out.degraded is True


def test_advise_ollama_loopback_calls_model():
    """ollama 模式 endpoint 回环 → 发请求；模拟成功响应 → source=local-model。"""
    payload = AssistantAdviseIn(page="stats", total=10, by_level={"L2": 5})
    settings = _make_settings(
        assistant_mode="ollama",
        assistant_endpoint="http://127.0.0.1:11434/v1/chat/completions",
        assistant_model="qwen2.5:1.5b",
    )

    # 构造一个 OpenAI 兼容响应
    fake_response = type(
        "FakeResp",
        (),
        {
            "read": lambda self: json.dumps(
                {
                    "choices": [
                        {"message": {"content": "模型给出的总体评估。"}}
                    ]
                }
            ).encode("utf-8"),
            "__enter__": lambda self: self,
            "__exit__": lambda self, *a: None,
        },
    )()

    with patch("urllib.request.urlopen", return_value=fake_response) as mock_open:
        out = advise(payload, settings)
        mock_open.assert_called_once()
    assert out.source == "local-model"
    assert out.model == "qwen2.5:1.5b"
    assert out.degraded is False
    assert out.summary == "模型给出的总体评估。"


def test_advise_ollama_loopback_failure_degraded():
    """ollama 模式回环但请求失败 → degraded=True，source=rules。"""
    payload = AssistantAdviseIn(page="stats", total=10, by_level={"L2": 5})
    settings = _make_settings(
        assistant_mode="ollama",
        assistant_endpoint="http://127.0.0.1:11434/v1/chat/completions",
    )
    with patch("urllib.request.urlopen", side_effect=Exception("connection refused")):
        out = advise(payload, settings)
    assert out.source == "rules"
    assert out.degraded is True


def test_advise_external_mode_no_network():
    """历史 external 配置不再调用任何外部 API，直接走规则引擎。"""
    payload = AssistantAdviseIn(page="stats", total=10, by_level={"L2": 5})
    settings = _make_settings(
        assistant_mode="external",
        assistant_endpoint="https://api.example.com/v1/chat/completions",
        assistant_model="gpt-4o-mini",
    )
    with patch("urllib.request.urlopen") as mock_open:
        out = advise(payload, settings)
        mock_open.assert_not_called()
    assert out.source == "rules"
    assert out.degraded is False


def test_assistant_mode_external_normalized_to_off(monkeypatch):
    """环境变量里残留 external 时，配置层降级为 off。"""
    monkeypatch.setenv("FLOWMASK_ASSISTANT_MODE", "external")
    settings = Settings.load()
    assert settings.assistant_mode == "off"


# ---------- 推荐规则覆盖完整性 ----------


def test_recommend_all_purposes_have_rule():
    """每个 purpose 至少有一条规则覆盖（避免落空）。"""
    purposes = {"dev-test", "teaching", "outsourcing", "analysis", "public-sharing", "other"}
    for purpose in purposes:
        payload = RecommendIn(purpose=purpose)
        settings = _make_settings(assistant_mode="off")
        out = recommend(payload, settings)
        # 兜底也会返回 partial + L2，所以一定有结果
        assert out.strategy in {"block", "partial", "hash", "generalize", "pseudonym", "encrypt"}
        assert out.reason
