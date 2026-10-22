"""规则层健壮性测试：自定义规则校验、脏数据容错、捕获组越界防御。

历史缺陷：自定义规则不要求捕获组，而 ``Rule.group`` 默认 1，
``match.group(1)`` 在无捕获组时抛 ``IndexError``，让整条检测链路 500。
"""

from __future__ import annotations

import re

from app.core.db import utcnow_iso
from app.identify.rules import Rule, active_rules, detect, set_active_rules
from app.identify.types import EntityType


def test_custom_rule_without_capture_group_is_rejected(client) -> None:
    """没有捕获组的正则无法定位要脱敏的片段，创建时就该 422。"""
    no_group = client.post(
        "/api/v1/rules",
        json={"id": "x_no_group", "name": "x", "type": "secret", "pattern": "abc[0-9]+", "weight": 0.5},
    )
    assert no_group.status_code == 422
    assert "捕获组" in no_group.text

    non_capturing = client.post(
        "/api/v1/rules",
        json={
            "id": "x_non_capturing",
            "name": "x",
            "type": "secret",
            "pattern": "(?:abc)[0-9]+",
            "weight": 0.5,
        },
    )
    assert non_capturing.status_code == 422


def test_custom_rule_with_capture_group_is_accepted(client) -> None:
    created = client.post(
        "/api/v1/rules",
        json={
            "id": "x_with_group",
            "name": "x",
            "type": "secret",
            "pattern": r"工号[：:]\s*([A-Z]{2}\d{6})",
            "weight": 0.6,
        },
    )
    assert created.status_code == 201
    hit = client.post("/api/v1/detect", json={"text": "工号：AB123456"}).json()
    assert hit["findings"], "带捕获组的自定义规则应当生效"


def test_detect_tolerates_rule_group_out_of_range() -> None:
    """即使有调用方直接构造了 group 越界的 Rule，detect 也不能崩。"""
    rule = Rule(
        name="no_group_direct",
        type=EntityType.SECRET,
        pattern=re.compile(r"foo[0-9]+"),
        weight=0.5,
        confidence=0.8,
    )
    hits = detect("前缀 foo123 后缀", rules=(rule,))
    assert len(hits) == 1
    assert hits[0].value == "foo123"


def test_sync_active_rules_skips_dirty_rows(database) -> None:
    """库里存在脏数据（未知类别 / 无捕获组）时，同步不能抛异常拖垮启动。"""
    from app.api.routes_rules import sync_active_rules

    now = utcnow_iso()
    with database.write() as conn:
        conn.execute(
            "INSERT INTO rules (id, name, type, pattern, weight, confidence, enabled, updated_at)"
            " VALUES (?,?,?,?,?,?,?,?)",
            ("dirty_type", "脏类别", "unknown", "(x)", 0.5, 0.8, 1, now),
        )
        conn.execute(
            "INSERT INTO rules (id, name, type, pattern, weight, confidence, enabled, updated_at)"
            " VALUES (?,?,?,?,?,?,?,?)",
            ("dirty_group", "脏捕获组", "secret", r"\d{6}", 0.5, 0.8, 1, now),
        )

    sync_active_rules(database)  # 不抛异常

    try:
        names = {rule.name for rule in active_rules()}
        assert "dirty_type" not in names
        assert "dirty_group" in names
        # 无捕获组的脏规则退化为整段匹配，而不是让 detect 抛 IndexError
        hits = detect("验证码 123456", rules=tuple(r for r in active_rules() if r.name == "dirty_group"))
        assert hits and hits[0].value == "123456"
    finally:
        set_active_rules(None)
