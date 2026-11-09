"""场景模式：6 个内置预设，给内容检测页一键套用检测+脱敏参数。

设计要点：
- **纯静态、不进库**：内置场景写死在代码里，前端只是把它们铺成一个下拉。
  用户自定义场景走 localStorage（前端层），不经过这里；
- **只给推荐参数**：场景本身不改变识别逻辑，前端拿到 ``recommended_*`` 字段后
  把它们填到检测表单里，用户仍可微调（与"无模式"流程一致）；
- **样例文本要可识别**：每个场景的 ``sample`` 必须能命中至少一条 L1 规则，
  方便评委打开就能看到效果。
"""

from __future__ import annotations

from dataclasses import dataclass, asdict


@dataclass(slots=True, frozen=True)
class Scenario:
    """一个场景预设。

    字段含义：
    - ``id``：稳定标识，前端用它做 i18n key 与 localStorage 关联；
    - ``name``：中文显示名（与菜单一致）；
    - ``description``：一句话说明，给评委看；
    - ``sample``：样例文本，点"试一段"按钮直接灌进检测框；
    - ``recommended_strategy``：导出时建议的脱敏策略（block/partial/hash/generalize/pseudonym/encrypt）；
    - ``recommended_min_level``：建议的最小返回级别（SAFE/L1/L2/L3）；
    - ``recommended_enable_l2/l3/ner``：建议的识别开关；
    - ``audit_recommended``：是否建议启用审计链（前端可据此默认勾选）。
    """

    id: str
    name: str
    description: str
    sample: str
    recommended_strategy: str
    recommended_min_level: str
    recommended_enable_l2: bool
    recommended_enable_l3: bool
    recommended_enable_ner: bool
    audit_recommended: bool


# 6 个内置场景：覆盖个人开发者/师生/外包最常见的几类数据形态。
# 推荐参数的取舍逻辑写在 description 里，便于评审理解。
SCENARIOS: list[Scenario] = [
    Scenario(
        id="medical",
        name="医疗病历",
        description="病历摘要、检验报告：主诉与初步诊断保留字段名、值用星号，姓名/证件同步遮掩；建议启用 L2 与 NER。",
        sample=(
            "患者张三，男，身份证号 110101199003078881，联系电话 13800138000。"
            "主诉：反复头痛 3 个月；初步诊断：偏头痛。"
        ),
        recommended_strategy="partial",
        recommended_min_level="L2",
        recommended_enable_l2=True,
        recommended_enable_l3=False,
        recommended_enable_ner=True,
        audit_recommended=True,
    ),
    Scenario(
        id="government",
        name="政务公开",
        description="政务数据公开前脱敏：证件号、住址需哈希化处理，建议启用 L2 语义识别。",
        sample=(
            "申请人李四，身份证号 31010119851122012X，现居上海市浦东新区世纪大道 1 号，"
            "手机 13912345678。"
        ),
        recommended_strategy="hash",
        recommended_min_level="L2",
        recommended_enable_l2=True,
        recommended_enable_l3=False,
        recommended_enable_ner=False,
        audit_recommended=True,
    ),
    Scenario(
        id="ecommerce",
        name="电商订单",
        description="订单导出/对账：手机号、地址需假名化替换，保留订单结构；不需要 L2。",
        sample=(
            "订单 #100235：收件人王五，手机 13700001234，"
            "收货地址北京市海淀区中关村大街 1 号，金额 199.00。"
        ),
        recommended_strategy="pseudonym",
        recommended_min_level="L2",
        recommended_enable_l2=False,
        recommended_enable_l3=False,
        recommended_enable_ner=False,
        audit_recommended=True,
    ),
    Scenario(
        id="finance",
        name="金融流水",
        description="银行流水/账户明细：高危数据，建议启用 L2+L3 双重复核，加密导出。",
        sample=(
            "账户 6222020200012345670，户名赵六，身份证 440101199001011233，"
            "交易金额 5000.00，对方账户 6222020200098765437。"
        ),
        recommended_strategy="encrypt",
        recommended_min_level="L3",
        recommended_enable_l2=True,
        recommended_enable_l3=True,
        recommended_enable_ner=False,
        audit_recommended=True,
    ),
    Scenario(
        id="hr",
        name="HR 简历",
        description="简历批量处理：人名用假名替换，身份证、手机号、邮箱同步遮掩；建议启用 NER。",
        sample=(
            "姓名孙七，身份证号 120101198706050006，手机 13600001111，"
            "邮箱 sunqi@example.com，本科毕业 5 年。"
        ),
        recommended_strategy="pseudonym",
        recommended_min_level="L2",
        recommended_enable_l2=True,
        recommended_enable_l3=False,
        recommended_enable_ner=True,
        audit_recommended=True,
    ),
    Scenario(
        id="audit",
        name="日志审计",
        description="系统日志/审计导出：手机号、IP、token 一律屏蔽，不留可逆痕迹。",
        sample=(
            "[2026-01-01 10:00:00] user=13800138000 ip=10.0.0.1 "
            "token=ak1f2c9d4e6b action=login result=success"
        ),
        recommended_strategy="block",
        recommended_min_level="L3",
        recommended_enable_l2=False,
        recommended_enable_l3=False,
        recommended_enable_ner=False,
        audit_recommended=True,
    ),
]


def list_scenarios() -> list[dict]:
    """返回所有场景的字典形式（前端列表渲染用）。"""
    return [asdict(s) for s in SCENARIOS]


def get_scenario(scenario_id: str) -> Scenario | None:
    """按 id 查找单个场景；不存在返回 None（路由层负责转 404）。"""
    for s in SCENARIOS:
        if s.id == scenario_id:
            return s
    return None
