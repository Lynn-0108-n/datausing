"""场景模式接口测试（Day 2.8）。

覆盖：
- 场景列表 200 + 6 个场景 + 字段齐全
- 场景详情 200 + 含 sample/recommended_strategy
- 不存在的 id → 404
- 样例文本能命中至少一条 L1 规则（保证演示有效）
"""

from __future__ import annotations

from app.scenarios import SCENARIOS


def test_scenarios_list_200_and_count(client):
    """GET /api/v1/scenarios 返回 6 个场景，字段齐全。"""
    resp = client.get("/api/v1/scenarios")
    assert resp.status_code == 200
    body = resp.json()
    assert "items" in body
    items = body["items"]
    assert len(items) == len(SCENARIOS) == 6
    # 字段齐全性
    required = {
        "id", "name", "description", "sample",
        "recommended_strategy", "recommended_min_level",
        "recommended_enable_l2", "recommended_enable_l3", "recommended_enable_ner",
        "audit_recommended",
    }
    for item in items:
        assert required.issubset(item.keys()), f"缺少字段：{required - set(item.keys())}"
        assert item["sample"], f"场景 {item['id']} 样例文本为空"


def test_scenarios_detail_200(client):
    """GET /api/v1/scenarios/medical 返回详情，含 sample 与 recommended_strategy。"""
    resp = client.get("/api/v1/scenarios/medical")
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == "medical"
    assert body["name"] == "医疗病历"
    assert body["sample"]
    assert body["recommended_strategy"] == "partial"
    assert body["recommended_min_level"] == "L2"
    assert body["recommended_enable_l2"] is True
    assert body["recommended_enable_ner"] is True
    assert body["audit_recommended"] is True


def test_hr_scenario_uses_pseudonym(client):
    """HR 场景人名用假名替换，策略为 pseudonym。"""
    resp = client.get("/api/v1/scenarios/hr")
    assert resp.status_code == 200
    assert resp.json()["recommended_strategy"] == "pseudonym"


def test_scenarios_detail_404(client):
    """不存在的场景 id 返回 404。"""
    resp = client.get("/api/v1/scenarios/nonexistent")
    assert resp.status_code == 404
    assert "不存在" in resp.json()["detail"]


def test_scenarios_ids_unique():
    """场景 id 不重复（前端用 id 做 key）。"""
    ids = [s.id for s in SCENARIOS]
    assert len(ids) == len(set(ids))


def test_scenarios_strategies_valid():
    """所有 recommended_strategy 必须是合法策略。"""
    valid = {"block", "partial", "hash", "generalize", "pseudonym", "encrypt"}
    for s in SCENARIOS:
        assert s.recommended_strategy in valid, f"场景 {s.id} 策略非法：{s.recommended_strategy}"


def test_scenarios_levels_valid():
    """所有 recommended_min_level 必须是合法级别。"""
    valid = {"SAFE", "L1", "L2", "L3"}
    for s in SCENARIOS:
        assert s.recommended_min_level in valid, f"场景 {s.id} 级别非法：{s.recommended_min_level}"


def test_scenarios_samples_have_sensitive_data():
    """每个场景的样例文本应至少命中一条 L1 规则（保证演示有效）。

    用规则引擎直接 detect，断言命中列表非空。
    """
    from app.identify import detect

    for s in SCENARIOS:
        hits = detect(s.sample)
        assert hits, f"场景 {s.id} 样例未命中任何规则，演示会空"


def test_scenarios_samples_detect_key_entities():
    """场景样例里的关键实体必须被识别，避免演示数据校验位不通过而漏检。

    身份证号、银行卡号都带校验位（MOD 11-2 / Luhn），随手编的号码会被规则丢弃；
    这里用真实识别结果守住演示数据，防止后续改样例时再次退化。
    """
    from app.identify import EntityType, detect

    expected = {
        "medical": {EntityType.ID_CARD},
        "government": {EntityType.ID_CARD},
        "finance": {EntityType.ID_CARD, EntityType.BANK_CARD},
        "hr": {EntityType.ID_CARD},
    }
    for scenario in SCENARIOS:
        required = expected.get(scenario.id)
        if not required:
            continue
        hits = detect(scenario.sample)
        types = {hit.type for hit in hits}
        missing = required - types
        assert not missing, f"场景 {scenario.id} 样例缺少命中类别：{missing}"

    finance = next(s for s in SCENARIOS if s.id == "finance")
    bank_cards = {h.value for h in detect(finance.sample) if h.type == EntityType.BANK_CARD}
    expected_cards = {"6222020200012345670", "6222020200098765437"}
    missing_cards = expected_cards - bank_cards
    assert not missing_cards, f"金融场景缺少银行卡号命中：{missing_cards}"

    # 「姓名孙七」「户名赵六」没有冒号，靠 name_label 规则兜住，避免演示时姓名漏掩
    expected_names = {"hr": "孙七", "finance": "赵六"}
    for scenario in SCENARIOS:
        expected_name = expected_names.get(scenario.id)
        if not expected_name:
            continue
        names = {hit.value for hit in detect(scenario.sample) if hit.type == EntityType.NAME}
        assert expected_name in names, f"场景 {scenario.id} 缺少姓名命中：{expected_name}"
