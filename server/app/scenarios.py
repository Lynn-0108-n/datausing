"""使用场景模板：一次选好「脱敏到什么程度、用什么策略、要不要开 L2/L3/NER」。

场景模板是「AI 助手推荐方案」的基线与兜底：即使用户不填任何用途说明、或不启用
本机模型，也能从场景键直接得到一套可执行参数。字段与 ``api/schemas.py`` 里的
``ScenarioOut`` 严格一致；这里用不可变 dataclass 存内部结构，``scenario_to_out``
负责转成路由可返回的字典（``content_types`` 由 tuple 转 list）。

策略 key 必须与脱敏层的六类策略对齐：block / partial / hash / generalize /
pseudonym / encrypt。改这里的取值前先改 ``identify/masking.py``，否则前后端会出现
「推荐了一个不存在的策略」这种静默不一致。
"""

from __future__ import annotations

from dataclasses import dataclass

#: 脱敏策略 key（与 identify/masking.py 的策略注册保持一致）
STRATEGY_KEYS: tuple[str, ...] = ("block", "partial", "hash", "generalize", "pseudonym", "encrypt")

#: 最低级别取值：SAFE 表示不处理，MEDIUM/HIGH 表示只返回中危/高危以上。
#: 采用 HIGH/MEDIUM/SAFE 这套语义名（识别层 ``Level.parse`` 能直接解析），
#: 避免和强制脱敏级别 L1/L2/L3 混用造成「DANGER 不是合法级别」的接口 400。
LEVEL_KEYS: tuple[str, ...] = ("SAFE", "MEDIUM", "HIGH")


@dataclass(frozen=True, slots=True)
class Scenario:
    """一个使用场景模板（不可变，进程内共享同一份实例）。"""

    key: str
    label: str
    description: str
    # 适用数据形态（text/json/csv/sql/image）；内部用 tuple 保证不可变
    content_types: tuple[str, ...]
    strategy: str
    mask_level: str
    min_level: str = "SAFE"
    enable_l2: bool = False
    enable_l3: bool = False
    enable_ner: bool = False
    retention: str = ""


#: 场景模板清单。key 用小写英文（接口传参），label/description/retention 直接给前端展示。
SCENARIOS: dict[str, Scenario] = {
    "medical": Scenario(
        key="medical",
        label="医院→科研",
        description="医院数据交给科研团队做统计/建模：患者与就诊标识用哈希脱敏，保留可关联性。",
        content_types=("text", "json", "csv", "sql"),
        strategy="hash",
        mask_level="L2",
        min_level="HIGH",
        enable_l2=True,
        enable_l3=False,
        enable_ner=False,
        retention="仅输出脱敏结果，原文不落库；哈希为确定性加盐哈希，可跨表关联同一患者",
    ),
    "finance": Scenario(
        key="finance",
        label="金融风控",
        description="风控规则与模型训练：卡号/账号泛化到区间或保留后四位，姓名与证件号不可逆脱敏。",
        content_types=("text", "json", "csv", "sql"),
        strategy="generalize",
        mask_level="L3",
        min_level="SAFE",
        enable_l2=True,
        enable_l3=False,
        enable_ner=True,
        retention="账号/卡号保留后四位，姓名与证件号不可逆脱敏",
    ),
    "education": Scenario(
        key="education",
        label="教学/学生数据",
        description="成绩、名单、作业等教学数据：学号与联系方式部分遮蔽，仅保留教学分析所需形态。",
        content_types=("text", "csv"),
        strategy="partial",
        mask_level="L2",
        min_level="SAFE",
        enable_l2=False,
        enable_l3=False,
        enable_ner=False,
        retention="成绩/名单只输出脱敏副本，学生姓名与联系方式不回传",
    ),
    "audit": Scenario(
        key="audit",
        label="安全审计留痕",
        description="审计与溯源场景：保留可复算指纹而不是原文，便于事后比对同一数据是否再次出现。",
        content_types=("text", "json"),
        strategy="hash",
        mask_level="L1",
        min_level="SAFE",
        enable_l2=False,
        enable_l3=False,
        enable_ner=False,
        retention="原文不进日志，仅保留哈希指纹与命中统计，便于事后追溯",
    ),
    "publish": Scenario(
        key="publish",
        label="对外发布/演示",
        description="对外公示、比赛演示、宣传材料：直接屏蔽敏感片段，任何原文都不随文件外发。",
        content_types=("text", "json", "csv", "sql"),
        strategy="block",
        mask_level="L3",
        min_level="SAFE",
        enable_l2=False,
        enable_l3=True,
        enable_ner=False,
        retention="对外文件只含屏蔽占位，原文与映射表均不随文件分发",
    ),
    "analysis": Scenario(
        key="analysis",
        label="科研分析",
        description="内部统计与建模：用服务端伪名替代真实标识，导出文件不含映射表，便于分组关联。",
        content_types=("text", "json", "csv", "sql"),
        strategy="pseudonym",
        mask_level="L2",
        min_level="SAFE",
        enable_l2=False,
        enable_l3=False,
        enable_ner=False,
        retention="可逆伪名只在服务端使用，导出文件默认不含映射表",
    ),
    "demo": Scenario(
        key="demo",
        label="演示（算法展示）",
        description="现场演示识别与脱敏效果：保留可读上下文，只对命中片段做部分遮蔽。",
        content_types=("text", "json", "csv", "sql"),
        strategy="partial",
        mask_level="L3",
        min_level="SAFE",
        enable_l2=False,
        enable_l3=False,
        enable_ner=False,
        retention="演示数据只用虚构样本，真实数据不得进入演示环境",
    ),
}

#: 前端首次打开设置页时默认选中的场景（教学数据最贴近大赛演示素材）
DEFAULT_SCENARIO_KEY = "education"


def get_scenario(key: str | None) -> Scenario | None:
    """按场景键取模板；空值/未知键返回 ``None``（调用方决定是 400 还是走默认规则）。"""
    return SCENARIOS.get((key or "").strip().lower())


def list_scenarios() -> list[Scenario]:
    """全部场景，按声明顺序（医疗→金融→教育→审计→发布→分析→演示）。"""
    return list(SCENARIOS.values())


def scenario_to_out(scenario: Scenario) -> dict[str, object]:
    """转成 ``ScenarioOut`` 可接受的字典（与 schema 字段一一对应）。"""
    return {
        "key": scenario.key,
        "label": scenario.label,
        "description": scenario.description,
        "content_types": list(scenario.content_types),
        "strategy": scenario.strategy,
        "mask_level": scenario.mask_level,
        "min_level": scenario.min_level,
        "enable_l2": scenario.enable_l2,
        "enable_l3": scenario.enable_l3,
        "enable_ner": scenario.enable_ner,
        "retention": scenario.retention,
    }


def _validate() -> None:
    """模块导入时自检：场景少、键非法、级别非法都属于开发期错误，直接抛出来。"""
    if len(SCENARIOS) < 7:
        raise ValueError(f"场景模板至少需要 7 个，当前 {len(SCENARIOS)} 个")
    if DEFAULT_SCENARIO_KEY not in SCENARIOS:
        raise ValueError(f"默认场景 {DEFAULT_SCENARIO_KEY!r} 不在场景清单里")
    for key, scenario in SCENARIOS.items():
        if scenario.key != key:
            raise ValueError(f"场景键不一致：字典键 {key!r} / 实例键 {scenario.key!r}")
        if scenario.strategy not in STRATEGY_KEYS:
            raise ValueError(f"场景 {key} 的策略 {scenario.strategy!r} 非法")
        if scenario.mask_level not in {"L1", "L2", "L3"}:
            raise ValueError(f"场景 {key} 的强制脱敏级别 {scenario.mask_level!r} 非法")
        if scenario.min_level not in LEVEL_KEYS:
            raise ValueError(f"场景 {key} 的最低级别 {scenario.min_level!r} 非法")
        if not scenario.content_types:
            raise ValueError(f"场景 {key} 未声明适用数据形态")


_validate()
