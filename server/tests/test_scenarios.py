"""场景模板（第一次优化）：数量、字段契约、参数取值与查询辅助函数。"""

from __future__ import annotations

import dataclasses

import pytest

from app.scenarios import (
    DEFAULT_SCENARIO_KEY,
    LEVEL_KEYS,
    SCENARIOS,
    STRATEGY_KEYS,
    get_scenario,
    list_scenarios,
    scenario_to_out,
)


def test_scenarios_at_least_seven() -> None:
    """场景模板至少 7 个，且 list_scenarios 与字典声明顺序一致。"""
    assert len(SCENARIOS) >= 7
    scenarios = list_scenarios()
    assert [item.key for item in scenarios] == list(SCENARIOS)
    assert len({item.key for item in scenarios}) == len(scenarios)


def test_scenario_fields_match_contract() -> None:
    """每个模板转成 ScenarioOut 后字段齐全、取值合法（与 schemas.ScenarioOut 对齐）。"""
    expected_keys = {
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
    allowed_content_types = {"text", "json", "csv", "sql", "md", "log", "docx", "pdf", "image"}
    for scenario in list_scenarios():
        data = scenario_to_out(scenario)
        assert set(data) == expected_keys
        assert data["key"] == scenario.key
        assert isinstance(data["label"], str) and data["label"]
        assert isinstance(data["description"], str) and data["description"]
        assert isinstance(data["retention"], str) and data["retention"]
        # 对外是 list（Pydantic 字段是 list[str]），内部不可变用 tuple
        assert isinstance(data["content_types"], list) and data["content_types"]
        assert set(data["content_types"]) <= allowed_content_types
        assert data["strategy"] in STRATEGY_KEYS
        assert data["mask_level"] in {"L1", "L2", "L3"}
        assert data["min_level"] in LEVEL_KEYS
        assert isinstance(data["enable_l2"], bool)
        assert isinstance(data["enable_l3"], bool)
        assert isinstance(data["enable_ner"], bool)


def test_scenario_is_immutable() -> None:
    """场景是进程内共享配置，不能被调用方改掉（frozen dataclass）。"""
    scenario = get_scenario("medical")
    assert scenario is not None
    with pytest.raises(dataclasses.FrozenInstanceError):
        scenario.strategy = "partial"  # type: ignore[misc]


def test_medical_and_education_templates() -> None:
    """医疗场景走哈希 + L2 + 最低 HIGH；教育场景走部分遮蔽 + L2。"""
    medical = get_scenario("medical")
    assert medical is not None
    assert medical.label == "医院→科研"
    assert medical.strategy == "hash"
    assert medical.mask_level == "L2"
    assert medical.min_level == "HIGH"
    assert medical.enable_l2 is True
    assert medical.enable_l3 is False
    assert medical.enable_ner is False
    assert "原文不落库" in medical.retention
    assert "哈希" in medical.retention

    education = get_scenario("education")
    assert education is not None
    assert education.label == "教学/学生数据"
    assert education.strategy == "partial"
    assert education.mask_level == "L2"
    assert education.min_level == "SAFE"
    assert education.enable_l2 is False
    assert set(education.content_types) == {"text", "csv"}


def test_default_scenario_exists() -> None:
    assert DEFAULT_SCENARIO_KEY == "education"
    scenario = get_scenario(DEFAULT_SCENARIO_KEY)
    assert scenario is not None
    assert scenario.key == DEFAULT_SCENARIO_KEY


def test_get_scenario_normalizes_key_and_unknown_returns_none() -> None:
    assert get_scenario("MEDICAL") is not None
    assert get_scenario("  publish  ") is not None
    assert get_scenario("unknown") is None
    assert get_scenario("") is None
    assert get_scenario(None) is None
