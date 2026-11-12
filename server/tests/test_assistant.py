"""本地 AI 助手（第一次优化）：规则建议、风险研判、健康探测与 ollama 降级。

测试约定：
- 全部虚构数据，不访问真实网络；ollama 相关路径一律 monkeypatch ``_generate`` /
  ``_fetch_tags``，断网环境下也能稳定跑。
- 敏感值只用于验证「不会被回显」，不写入任何断言产物。
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.assistant import SOURCE_DEGRADED, SOURCE_LLM, SOURCE_RULES, Assistant
from app.scenarios import STRATEGY_KEYS


def make_settings(**overrides) -> SimpleNamespace:
    """构造只含助手所需字段的 settings 替身（不依赖环境变量与数据库）。"""
    base = {
        "assistant_enabled": True,
        "assistant_mode": "rules",
        "assistant_endpoint": "http://127.0.0.1:11434/api/generate",
        "assistant_model": "qwen2.5:1.5b",
        "assistant_timeout_s": 5.0,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


@pytest.fixture()
def app_client(tmp_env):
    """模拟 main.py 的注册方式：assistant 路由挂进现有 /api/v1 外层路由。"""
    from fastapi import APIRouter

    from app.api.routes_assistant import router

    app = FastAPI()
    api = APIRouter(prefix="/api/v1")
    api.include_router(router)
    app.include_router(api)
    with TestClient(app) as client:
        yield client


# ---------------------------------------------------------------------------
# 规则模式：suggest
# ---------------------------------------------------------------------------


def test_suggest_medical_scenario_uses_hash_and_l2() -> None:
    assistant = Assistant(make_settings())
    result = assistant.suggest(
        {
            "scenario": "medical",
            "content_type": "csv",
            "fields": ["patient_id", "visit_no"],
            "level_counts": {"L2": 4},
            "text_length": 320,
            "prompt": "科研统计用",
        }
    )
    assert result["source"] == SOURCE_RULES
    assert result["strategy"] == "hash"
    assert result["mask_level"] == "L2"
    assert result["min_level"] == "HIGH"
    assert result["enable_l2"] is True
    assert result["enable_l3"] is False
    assert "场景" in result["rationale"]


def test_suggest_publish_scenario_blocks_public_content() -> None:
    assistant = Assistant(make_settings())
    result = assistant.suggest(
        {
            "scenario": "publish",
            "content_type": "text",
            "fields": [],
            "level_counts": {},
            "text_length": 100,
            "prompt": "对外发布",
        }
    )
    assert result["strategy"] == "block"
    assert result["mask_level"] == "L3"
    assert result["enable_l3"] is True


def test_suggest_unknown_scenario_falls_back_to_rules() -> None:
    """没有场景时按字段打分：身份类字段 → 哈希 + L3，最低级别回落 SAFE。"""
    assistant = Assistant(make_settings())
    result = assistant.suggest(
        {
            "scenario": None,
            "content_type": "json",
            "fields": ["id_card", "patient_name"],
            "level_counts": {"DANGER": 2},
            "text_length": 50,
            "prompt": "",
        }
    )
    assert result["source"] == SOURCE_RULES
    assert result["strategy"] in ("hash", "block")
    assert result["mask_level"] == "L3"
    assert result["min_level"] == "SAFE"
    assert result["enable_l3"] is True
    # 字段名含 name → 开启 NER 辅助识别自由文本人名
    assert result["enable_ner"] is True


def test_suggest_contact_fields_use_partial_and_l2() -> None:
    assistant = Assistant(make_settings())
    result = assistant.suggest(
        {
            "content_type": "csv",
            "fields": ["phone", "email", "address"],
            "level_counts": {},
            "text_length": 10,
            "prompt": "",
        }
    )
    assert result["strategy"] == "partial"
    assert result["mask_level"] == "L2"
    assert result["enable_l2"] is True


def test_suggest_escalates_scenario_mask_level_on_danger_counts() -> None:
    """审计场景默认 L1，但检测到 DANGER 且字段含联系方式时至少提升到 L2。"""
    assistant = Assistant(make_settings())
    result = assistant.suggest(
        {
            "scenario": "audit",
            "content_type": "json",
            "fields": ["phone"],
            "level_counts": {"DANGER": 3},
            "text_length": 20,
            "prompt": "",
        }
    )
    assert result["strategy"] == "hash"
    assert result["mask_level"] == "L2"
    assert result["enable_l2"] is True


def test_suggest_public_intent_never_returns_partial() -> None:
    """对外/演示用途下，即使场景基线与字段倾向 partial，也要升级为 block。"""
    assistant = Assistant(make_settings())
    result = assistant.suggest(
        {
            "scenario": "demo",
            "content_type": "text",
            "fields": ["name"],
            "level_counts": {},
            "text_length": 10,
            "prompt": "对外演示，请不要泄露隐私",
        }
    )
    assert result["strategy"] == "block"
    assert result["mask_level"] == "L3"


def test_suggest_output_has_all_fields_and_never_echoes_values() -> None:
    assistant = Assistant(make_settings())
    sensitive_marker = "13800138000"
    result = assistant.suggest(
        {
            "content_type": "text",
            "fields": ["姓名", "phone"],
            "level_counts": {"L2": 1},
            "text_length": 12,
            "prompt": f"对外发布 {sensitive_marker}",
        }
    )
    assert set(result) == {
        "strategy",
        "mask_level",
        "min_level",
        "enable_l2",
        "enable_l3",
        "enable_ner",
        "retention",
        "rationale",
        "source",
    }
    assert result["strategy"] in STRATEGY_KEYS
    assert result["mask_level"] in {"L1", "L2", "L3"}
    assert result["min_level"] in {"SAFE", "MEDIUM", "HIGH"}
    assert isinstance(result["rationale"], str) and result["rationale"]
    # 输入里出现的手机号值不得出现在任何输出字段（含 rationale/retention）
    assert sensitive_marker not in json.dumps(result, ensure_ascii=False)


# ---------------------------------------------------------------------------
# 规则模式：advise / health / models
# ---------------------------------------------------------------------------


def test_advise_returns_summary_findings_recommendations() -> None:
    assistant = Assistant(make_settings())
    result = assistant.advise(
        {
            "by_level": {"SAFE": 5, "L2": 2, "L3": 3},
            "by_type": [{"type": "token", "count": 3}, {"type": "phone", "count": 2}],
            "total_findings": 10,
            "tasks": 2,
            "scenario": "finance",
        }
    )
    assert result["source"] == SOURCE_RULES
    assert isinstance(result["summary"], str) and "10" in result["summary"]
    assert 1 <= len(result["findings"]) <= 3
    assert 1 <= len(result["recommendations"]) <= 3
    joined = "".join(result["findings"] + result["recommendations"])
    assert "访问令牌" in joined  # token 热点应被翻译成中文可执行建议
    assert "L2" in joined or "L3" in joined


def test_advise_handles_empty_payload() -> None:
    assistant = Assistant(make_settings())
    result = assistant.advise({})
    assert result["summary"]
    assert 1 <= len(result["findings"]) <= 3
    assert 1 <= len(result["recommendations"]) <= 3


def test_health_rules_mode_is_always_available() -> None:
    assistant = Assistant(make_settings())
    health = assistant.health()
    assert health["enabled"] is True
    assert health["mode"] == "rules"
    assert health["model"] == "qwen2.5:1.5b"
    assert health["available"] is True
    assert "规则" in health["note"]


def test_health_disabled_assistant() -> None:
    assistant = Assistant(make_settings(assistant_enabled=False))
    health = assistant.health()
    assert health["enabled"] is False
    assert health["available"] is False


def test_models_rules_mode_lists_nothing() -> None:
    assistant = Assistant(make_settings())
    models = assistant.models()
    assert models["models"] == []
    assert models["endpoint"] == "http://127.0.0.1:11434/api/generate"
    assert models["note"]


def test_tags_url_is_derived_from_endpoint() -> None:
    assistant = Assistant(make_settings())
    assert assistant.tags_url() == "http://127.0.0.1:11434/api/tags"
    assert Assistant(make_settings(assistant_endpoint="")).tags_url() == ""


# ---------------------------------------------------------------------------
# ollama 模式：成功走模型、失败一律降级且策略仍合法
# ---------------------------------------------------------------------------


def test_ollama_success_source_is_local_llm(monkeypatch) -> None:
    assistant = Assistant(make_settings(assistant_mode="ollama"))

    def fake_generate(self, prompt: str) -> str:
        assert "id_card" in prompt  # prompt 只带字段名/计数/用途
        return json.dumps(
            {
                "strategy": "generalize",
                "mask_level": "L3",
                "min_level": "SAFE",
                "enable_l2": True,
                "enable_l3": True,
                "enable_ner": False,
                "retention": "原文不落库",
                "rationale": "模型建议泛化处理。",
            }
        )

    monkeypatch.setattr(Assistant, "_generate", fake_generate)
    result = assistant.suggest(
        {
            "scenario": "finance",
            "content_type": "csv",
            "fields": ["id_card", "bank_card"],
            "level_counts": {"L3": 2},
            "text_length": 64,
            "prompt": "风控建模",
        }
    )
    assert result["source"] == SOURCE_LLM
    assert result["strategy"] == "generalize"
    assert result["mask_level"] == "L3"


def test_ollama_invalid_json_degrades_to_rules(monkeypatch) -> None:
    assistant = Assistant(make_settings(assistant_mode="ollama"))
    monkeypatch.setattr(Assistant, "_generate", lambda self, prompt: "抱歉，我无法输出 JSON")
    result = assistant.suggest({"scenario": "medical", "content_type": "text", "fields": [], "level_counts": {}})
    assert result["source"] == SOURCE_DEGRADED
    assert result["strategy"] in STRATEGY_KEYS
    assert result["mask_level"] in {"L1", "L2", "L3"}


def test_ollama_exception_degrades_to_rules(monkeypatch) -> None:
    assistant = Assistant(make_settings(assistant_mode="ollama"))

    def boom(self, prompt: str) -> str:
        raise OSError("connection refused")

    monkeypatch.setattr(Assistant, "_generate", boom)
    result = assistant.suggest({"content_type": "text", "fields": ["phone"], "level_counts": {}})
    assert result["source"] == SOURCE_DEGRADED
    assert result["strategy"] == "partial"
    assert result["mask_level"] == "L2"


def test_ollama_advise_success_and_failure(monkeypatch) -> None:
    assistant = Assistant(make_settings(assistant_mode="ollama"))
    payload = {"by_level": {"L3": 2, "SAFE": 3}, "by_type": [{"type": "token", "count": 2}], "total_findings": 5}

    monkeypatch.setattr(
        Assistant,
        "_generate",
        lambda self, prompt: json.dumps(
            {"summary": "风险总体可控。", "findings": ["存在高危令牌。"], "recommendations": ["开启 L2 复核。"]}
        ),
    )
    result = assistant.advise(payload)
    assert result["source"] == SOURCE_LLM
    assert result["summary"] == "风险总体可控。"

    monkeypatch.setattr(Assistant, "_generate", lambda self, prompt: "{}")
    degraded = assistant.advise(payload)
    assert degraded["source"] == SOURCE_DEGRADED
    assert degraded["summary"]


def test_ollama_health_and_models_use_tags_probe(monkeypatch) -> None:
    assistant = Assistant(make_settings(assistant_mode="ollama"))
    monkeypatch.setattr(Assistant, "_fetch_tags", lambda self, timeout_s: ["qwen2.5:1.5b"])
    health = assistant.health()
    assert health["available"] is True
    models = assistant.models()
    assert models["models"] == ["qwen2.5:1.5b"]
    assert models["note"] == ""

    monkeypatch.setattr(Assistant, "_fetch_tags", lambda self, timeout_s: None)
    unreachable = assistant.health()
    assert unreachable["available"] is False
    assert "不可达" in unreachable["note"]
    assert assistant.models()["models"] == []
    assert assistant.models()["note"] == "本机模型服务不可达（规则模式仍可用）"


# ---------------------------------------------------------------------------
# 路由层：/scenarios 与 /assistant/*（独立挂载，不依赖 main.py 注册）
# ---------------------------------------------------------------------------


def test_api_scenarios_returns_seven_templates(app_client) -> None:
    response = app_client.get("/api/v1/scenarios")
    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 7
    assert len(payload["scenarios"]) == 7
    keys = {item["key"] for item in payload["scenarios"]}
    assert {"medical", "publish", "education"} <= keys
    for item in payload["scenarios"]:
        assert set(item) == {
            "key",
            "label",
            "description",
            "content_types",
            "strategy",
            "mask_level",
            "min_level",
            "enable_l2",
            "enable_l3",
            "enable_ner",
            "retention",
        }
        assert item["strategy"] in STRATEGY_KEYS
        assert item["mask_level"] in {"L1", "L2", "L3"}
        assert item["min_level"] in {"SAFE", "MEDIUM", "HIGH"}


def test_api_suggest_medical(app_client) -> None:
    response = app_client.post(
        "/api/v1/assistant/suggest",
        json={"scenario": "medical", "content_type": "csv", "fields": ["patient_id"], "level_counts": {"L2": 1}},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["strategy"] == "hash"
    assert payload["mask_level"] == "L2"
    assert payload["source"] == SOURCE_RULES


def test_api_suggest_unknown_scenario_returns_400(app_client) -> None:
    response = app_client.post("/api/v1/assistant/suggest", json={"scenario": "nope", "content_type": "text"})
    assert response.status_code == 400
    assert response.json()["detail"] == "未知场景"


def test_api_health_models_advise(app_client) -> None:
    health = app_client.get("/api/v1/assistant/health")
    assert health.status_code == 200
    assert health.json()["available"] is True
    assert health.json()["mode"] == "rules"

    models = app_client.get("/api/v1/assistant/models")
    assert models.status_code == 200
    assert models.json()["models"] == []

    advise = app_client.post(
        "/api/v1/assistant/advise",
        json={"by_level": {"L3": 2, "SAFE": 3}, "by_type": [{"type": "phone", "count": 2}], "total_findings": 5},
    )
    assert advise.status_code == 200
    body = advise.json()
    assert body["summary"]
    assert body["source"] == SOURCE_RULES


def test_api_ollama_mode_uses_mocked_engine(app_client, monkeypatch) -> None:
    """路由在 ollama 模式下也走降级逻辑：模型不可用 → 200 + rules/degraded。"""
    from app.core.config import get_settings

    monkeypatch.setenv("FLOWMASK_ASSISTANT_MODE", "ollama")
    get_settings.cache_clear()

    def boom(self, prompt: str) -> str:
        raise OSError("connection refused")

    monkeypatch.setattr(Assistant, "_generate", boom)
    response = app_client.post("/api/v1/assistant/suggest", json={"content_type": "text", "fields": ["phone"]})
    assert response.status_code == 200
    assert response.json()["source"] == SOURCE_DEGRADED
    assert response.json()["strategy"] == "partial"
