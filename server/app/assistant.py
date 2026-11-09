"""AI 助手：统计解读 + 方案推荐。

设计要点（与 ``项目第一次优化.md`` 2.3 节一致）：

- **规则引擎为主**：默认 ``assistant_mode=off``，纯本地规则给结论，不调任何模型；
- **可选模型增强**：``ollama``（仅限本机回环），失败回退规则引擎；
- **数据最小化**：调模型时只发聚合计数 / 用途标签，**绝不发原文 / masked / path**；
- **回环校验**：``ollama`` 模式要求 endpoint host 在 ``{127.0.0.1, ::1, localhost}``，
  非回环直接降级（``degraded=True``），不发请求；
- **超时短**：默认 3s，超时即降级，不阻塞前端。
"""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from datetime import datetime

from .api.schemas import (
    AdviceItem,
    AssistantAdviseIn,
    AssistantAdviseOut,
    RecommendIn,
    RecommendOut,
)
from .core.config import Settings

# ollama 模式允许的回环主机名
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})


# ---------- 方案推荐（规则引擎，写死）----------

# 按 purpose + requirements 匹配；命中第一个就返回。
# 顺序很重要：更严格的用途（公开共享）排在更宽松的用途前面。
_RECOMMEND_RULES: list[tuple[RecommendIn, dict]] = [
    (
        RecommendIn(purpose="public-sharing", requirements=["irreversible"]),
        {
            "strategy": "block",
            "min_level": "L3",
            "enable_l2": True,
            "enable_l3": True,
            "enable_ner": False,
            "audit_recommended": True,
            "reason": "公开共享需彻底不可逆，建议 L3 双重复核 + block 全屏蔽。",
        },
    ),
    (
        RecommendIn(purpose="public-sharing"),
        {
            "strategy": "hash",
            "min_level": "L3",
            "enable_l2": True,
            "enable_l3": False,
            "enable_ner": False,
            "audit_recommended": True,
            "reason": "公开数据建议哈希脱敏，保留格式但不可逆。",
        },
    ),
    (
        RecommendIn(purpose="outsourcing", requirements=["audit"]),
        {
            "strategy": "block",
            "min_level": "L2",
            "enable_l2": True,
            "enable_l3": False,
            "enable_ner": False,
            "audit_recommended": True,
            "reason": "外包交付留痕，敏感字段全屏蔽并启用审计链。",
        },
    ),
    (
        RecommendIn(purpose="outsourcing"),
        {
            "strategy": "partial",
            "min_level": "L2",
            "enable_l2": True,
            "enable_l3": False,
            "enable_ner": False,
            "audit_recommended": True,
            "reason": "外包场景建议部分脱敏 + 审计链，保留数据可用性同时留痕。",
        },
    ),
    (
        RecommendIn(purpose="teaching", requirements=["irreversible"]),
        {
            "strategy": "hash",
            "min_level": "L2",
            "enable_l2": True,
            "enable_l3": False,
            "enable_ner": True,
            "audit_recommended": False,
            "reason": "教学演示需彻底不可逆，哈希保留格式便于讲解。",
        },
    ),
    (
        RecommendIn(purpose="teaching"),
        {
            "strategy": "partial",
            "min_level": "L2",
            "enable_l2": False,
            "enable_l3": False,
            "enable_ner": True,
            "audit_recommended": False,
            "reason": "教学演示建议部分脱敏，直观展示脱敏过程。",
        },
    ),
    (
        RecommendIn(purpose="analysis", requirements=["keep-statistics"]),
        {
            "strategy": "generalize",
            "min_level": "L2",
            "enable_l2": True,
            "enable_l3": False,
            "enable_ner": False,
            "audit_recommended": False,
            "reason": "数据分析需保留统计特征，泛化既能保护个体又不破坏聚合指标。",
        },
    ),
    (
        RecommendIn(purpose="dev-test", requirements=["keep-statistics"]),
        {
            "strategy": "partial",
            "min_level": "L2",
            "enable_l2": False,
            "enable_l3": False,
            "enable_ner": False,
            "audit_recommended": False,
            "reason": "开发测试保留数据形态，部分脱敏 + 数值泛化保护个体。",
        },
    ),
    (
        RecommendIn(purpose="dev-test", requirements=["reversible"]),
        {
            "strategy": "encrypt",
            "min_level": "L2",
            "enable_l2": False,
            "enable_l3": False,
            "enable_ner": False,
            "audit_recommended": False,
            "reason": "需要可逆还原时用 AES-GCM 加密，授权后可解密还原。",
        },
    ),
    (
        RecommendIn(purpose="other", requirements=["reversible"]),
        {
            "strategy": "encrypt",
            "min_level": "L2",
            "enable_l2": False,
            "enable_l3": False,
            "enable_ner": False,
            "audit_recommended": False,
            "reason": "需要可逆解密时用加密策略，授权后可还原原文。",
        },
    ),
    (
        RecommendIn(purpose="other"),
        {
            "strategy": "partial",
            "min_level": "L2",
            "enable_l2": True,
            "enable_l3": False,
            "enable_ner": True,
            "audit_recommended": True,
            "reason": "通用场景建议部分脱敏 + 启用 L2 与 NER，平衡可用性与安全。",
        },
    ),
    (
        RecommendIn(purpose="analysis"),
        {
            "strategy": "generalize",
            "min_level": "L2",
            "enable_l2": True,
            "enable_l3": False,
            "enable_ner": False,
            "audit_recommended": False,
            "reason": "数据分析建议泛化脱敏，保留统计特征。",
        },
    ),
    (
        RecommendIn(purpose="dev-test"),
        {
            "strategy": "partial",
            "min_level": "L2",
            "enable_l2": False,
            "enable_l3": False,
            "enable_ner": False,
            "audit_recommended": False,
            "reason": "开发测试建议部分脱敏，保留数据形态便于测试。",
        },
    ),
]


def _match_recommend_rule(payload: RecommendIn) -> dict:
    """按 purpose + requirements 子集匹配规则；命中第一个就返回。

    匹配规则：``requirements`` 是用户提交的标签集合，规则里的 ``requirements``
    必须全部出现在用户提交里（子集匹配）；规则没指定 ``requirements`` 时
    仅按 ``purpose`` 匹配。
    """
    user_reqs = set(payload.requirements)
    for rule_payload, rule_result in _RECOMMEND_RULES:
        if rule_payload.purpose != payload.purpose:
            continue
        rule_reqs = set(rule_payload.requirements)
        if rule_reqs and not rule_reqs.issubset(user_reqs):
            continue
        return rule_result
    # 兜底：partial + L2（最保守的可用配置）
    return {
        "strategy": "partial",
        "min_level": "L2",
        "enable_l2": True,
        "enable_l3": False,
        "enable_ner": True,
        "audit_recommended": True,
        "reason": "默认建议部分脱敏 + 启用 L2 与 NER，平衡可用性与安全。",
    }


def recommend(payload: RecommendIn, settings: Settings) -> RecommendOut:
    """方案推荐：规则引擎给结论，可选模型增强。

    MVP 阶段**方案推荐不调模型**（用途标签足够规则匹配，模型增益小、风险高），
    统一返回 ``source=rules``；``degraded=False``。
    """
    result = _match_recommend_rule(payload)
    return RecommendOut(
        strategy=result["strategy"],
        min_level=result["min_level"],
        enable_l2=result["enable_l2"],
        enable_l3=result["enable_l3"],
        enable_ner=result["enable_ner"],
        audit_recommended=result["audit_recommended"],
        reason=result["reason"],
        source="rules",
        degraded=False,
    )


# ---------- 统计解读（规则引擎 + 可选模型）----------


def _severity_for(ratio_high: float, ratio_mid: float) -> str:
    """按高危/中危占比定级。"""
    if ratio_high >= 0.10 or ratio_mid >= 0.40:
        return "high"
    if ratio_high >= 0.01 or ratio_mid >= 0.10:
        return "warn"
    return "info"


def _rule_advise(payload: AssistantAdviseIn) -> AssistantAdviseOut:
    """规则引擎解读：按级别占比 + 类别分布给建议。"""
    by_level = payload.by_level or {}
    total = payload.total or 0
    # 兼容大小写键
    l3 = by_level.get("L3", 0)
    l2 = by_level.get("L2", 0)
    l1 = by_level.get("L1", 0)
    safe = by_level.get("SAFE", 0)
    # 也接受小写
    l3 += by_level.get("l3", 0)
    l2 += by_level.get("l2", 0)
    l1 += by_level.get("l1", 0)
    safe += by_level.get("safe", 0)

    high_count = l3
    mid_count = l2
    findings_count = l3 + l2 + l1

    if total > 0:
        ratio_high = high_count / total
        ratio_mid = mid_count / total
    else:
        ratio_high = ratio_mid = 0.0

    severity = _severity_for(ratio_high, ratio_mid)
    tips: list[AdviceItem] = []

    if total == 0:
        summary = "当前范围内未发现敏感数据命中。"
        tips.append(
            AdviceItem(
                title="暂无命中",
                detail="本次统计窗口未采集到任何敏感数据命中，可继续保持现有检测策略。",
                action="",
            )
        )
    else:
        # 总体评估
        if severity == "high":
            summary = f"本次共检测 {total} 个片段，命中 {findings_count} 处，其中高危 {high_count} 处，建议立即收紧脱敏策略。"
        elif severity == "warn":
            summary = f"本次共检测 {total} 个片段，命中 {findings_count} 处，存在中危数据，建议复核脱敏覆盖范围。"
        else:
            summary = f"本次共检测 {total} 个片段，命中 {findings_count} 处，整体风险可控。"

        # 高危建议
        if high_count > 0:
            tips.append(
                AdviceItem(
                    title=f"高危命中 {high_count} 处",
                    detail="高危数据（L3）建议立即启用 block 全屏蔽或 encrypt 加密导出，并启用审计链留痕。",
                    action="前往内容检测导出",
                )
            )
        if mid_count > 0:
            tips.append(
                AdviceItem(
                    title=f"中危命中 {mid_count} 处",
                    detail="中危数据（L2）建议启用 L2 语义识别复核，避免误报；导出时用 partial 或 hash。",
                    action="启用 L2 识别",
                )
            )
        # 类别 Top 建议
        types_sorted = sorted(payload.types, key=lambda t: t.count, reverse=True)
        if types_sorted:
            top = types_sorted[0]
            tips.append(
                AdviceItem(
                    title=f"类别分布：{top.type} 命中最多（{top.count} 处）",
                    detail=f"建议在识别规则页检查「{top.type}」相关规则的覆盖与阈值，必要时调整启用状态。",
                    action="前往识别规则",
                )
            )
        # 审计建议
        if findings_count > 0 and not by_level.get("audit"):
            tips.append(
                AdviceItem(
                    title="建议启用审计链",
                    detail="本次有命中但未见审计记录，建议在导出时勾选审计留痕，便于后续溯源。",
                    action="启用审计",
                )
            )

    return AssistantAdviseOut(
        summary=summary,
        tips=tips,
        severity=severity,
        source="rules",
        degraded=False,
        model="",
        generated_at=datetime.now().isoformat(timespec="seconds"),
    )


def _is_loopback(endpoint: str) -> bool:
    """判断 URL 的 host 是否在允许的回环集合里。"""
    try:
        parsed = urllib.parse.urlparse(endpoint)
    except ValueError:
        return False
    host = (parsed.hostname or "").lower()
    return host in _LOOPBACK_HOSTS


def _call_model(
    payload: dict, settings: Settings, *, prompt: str
) -> str | None:
    """调用本机 Ollama；失败返回 None。

    约束：
    - 仅 ``ollama`` 模式可用，endpoint 必须是回环，否则不发请求；
    - 请求体：OpenAI 兼容格式（``/v1/chat/completions``），temperature=0、max_tokens=400；
    - 超时：``settings.assistant_timeout_s``，超时即返回 None。
    """
    if settings.assistant_mode != "ollama":
        return None
    if not _is_loopback(settings.assistant_endpoint):
        return None

    body = {
        "model": settings.assistant_model,
        "messages": [
            {"role": "system", "content": "你是数据安全助手，根据聚合计数给出简短评估，不要输出原文。"},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0,
        "max_tokens": 400,
        "stream": False,
    }
    headers = {"Content-Type": "application/json"}

    try:
        req = urllib.request.Request(
            settings.assistant_endpoint,
            data=json.dumps(body).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=settings.assistant_timeout_s) as resp:  # noqa: S310
            data = json.loads(resp.read().decode("utf-8"))
        # OpenAI 兼容：choices[0].message.content
        choices = data.get("choices") or []
        if not choices:
            return None
        message = choices[0].get("message") or {}
        content = message.get("content")
        return content.strip() if isinstance(content, str) and content.strip() else None
    except Exception:  # noqa: BLE001
        # 任何异常都降级到规则引擎，不让推理服务成为单点
        return None


def advise(payload: AssistantAdviseIn, settings: Settings) -> AssistantAdviseOut:
    """统计解读：规则引擎给结论，可选模型增强 summary。"""
    base = _rule_advise(payload)

    # 只在本机 Ollama 模式下尝试模型增强
    if settings.assistant_mode == "ollama":
        # 构造给模型的 prompt：只含聚合计数，不含原文
        prompt_lines = [
            f"页面来源: {payload.page}",
            f"范围: {payload.scope}",
            f"总片段数: {payload.total}",
            f"级别分布: {payload.by_level}",
            "类别计数:",
        ]
        for t in payload.types[:10]:  # 最多传 10 类，控制 token
            prompt_lines.append(f"  - {t.type}: {t.count}")
        prompt_lines.append("请用一段话（不超过 100 字）给出总体评估与建议。")
        prompt = "\n".join(prompt_lines)

        content = _call_model(payload.model_dump(), settings, prompt=prompt)
        if content:
            base.summary = content
            base.source = "local-model"
            base.model = settings.assistant_model
            base.degraded = False
        else:
            # 模型调用失败 → 回退规则引擎结论，标记降级
            base.degraded = True
    return base
