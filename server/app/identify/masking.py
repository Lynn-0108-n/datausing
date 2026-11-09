"""四级脱敏执行。

模板与 Go 版一致，也是《需求分析》"四级分级脱敏"的落点：

| 级别 | 处置方式 | 示例 |
| --- | --- | --- |
| L3 高危 | 全屏蔽为类别占位；身份证/地址/医疗按结构脱敏 | ``11010519491231002X`` → ``110105********002X`` |
| L2 中危 | 按类别遮掩（手机号等保留首字符，姓名/地址/医疗/证件收紧） | ``13800138000`` → ``1**********`` |
| L1 低危 | 前置警示 + 按 L2 模板遮掩 | ``13800138000`` → ``[低危]1**********`` |
| SAFE 安全 | 不处理 | 原文 |

默认只改命中片段、不动上下文，这样"这条数据是在什么业务里泄露的"仍然可读；
唯一例外是医疗信息：症状本身可反推诊断，命中医疗字段时按句整体遮蔽（见
``expand_medical_hits``）。类别名用中文占位符，演示与报告里一眼能看懂被屏蔽的是什么。

地址保留省/市/区，从街道起用 ``*`` 代替；医疗保留「主诉 / 初步诊断」等字段名，
字段值用 ``*`` 遮盖。
"""

from __future__ import annotations

import base64
import hashlib
import os
import re
from enum import Enum

from .operators import generalize_numeric
from .types import EntityType, Hit, Level, Result, entity_label

MAX_STARS = 10


def _stars(length: int) -> str:
    return "*" * max(1, min(length - 1, MAX_STARS))


# ---------- 地址：保留省/市/区，街道及门牌用 * ----------

# 行政区后缀，长的在前，避免「自治区」被「区」提前截断
_ADMIN_SUFFIXES: tuple[str, ...] = ("特别行政区", "自治区", "省", "市", "区", "县")
# 街道及以下的起始标记（「大道/大街」含「街」/「道」，这里单列「大道」）
_STREET_MARKERS: tuple[str, ...] = ("路", "街", "大道", "巷", "弄", "胡同", "号", "镇", "村", "乡")


def _admin_ends_before(value: str, limit: int) -> list[int]:
    """返回 ``[0, limit)`` 内所有行政区后缀的结束位置（升序去重）。"""
    ends: set[int] = set()
    for suffix in _ADMIN_SUFFIXES:
        start = 0
        while True:
            index = value.find(suffix, start, limit)
            if index < 0:
                break
            ends.add(index + len(suffix))
            start = index + 1
    return sorted(ends)


def _mask_address(value: str) -> str:
    """保留省/市/区，从街道起用 * 代替。

    - 有街道标记（路/街/大道/巷/号/镇/村…）：保留最后一个行政区后缀，遮住其后全部；
    - 没有街道标记：保留到倒数第二个行政区后缀，遮住最后一段（如「深圳市南山区」→「深圳市***」）；
    - 识别不到两级行政区或没有可遮内容：回退整体占位，避免原样放行。
    """
    street_positions = [value.find(marker) for marker in _STREET_MARKERS if marker in value]
    street_at = min(street_positions) if street_positions else -1
    ends = _admin_ends_before(value, street_at if street_at >= 0 else len(value))
    if street_at >= 0:
        if not ends:
            return _mask_block(value, EntityType.ADDRESS)
        keep = ends[-1]
    else:
        if len(ends) < 2:
            return _mask_block(value, EntityType.ADDRESS)
        keep = ends[-2]
    tail = value[keep:]
    if not tail:
        return _mask_block(value, EntityType.ADDRESS)
    return value[:keep] + "*" * max(1, min(len(tail), MAX_STARS))


# ---------- 身份证：保留前 6 位与后 4 位，中间用 * ----------

def _mask_id_card(value: str) -> str:
    """身份证号保留前 6 位地区码与后 4 位，中间用星号。

    18 位身份证：``11010519491231002X`` → ``110105********002X``。
    非明文证件形态（如 base64 编码后的命中）整体遮盖，避免保留编码片段。
    """
    is_plain = (
        len(value) == 15 and value.isdigit()
    ) or (
        len(value) == 18
        and value[:17].isdigit()
        and (value[17].isdigit() or value[17] in "Xx")
    )
    if not is_plain:
        return "*" * max(1, min(len(value), MAX_STARS))
    return value[:6] + "*" * max(1, min(len(value) - 10, MAX_STARS)) + value[-4:]


# ---------- 医疗：保留字段名，字段值用 * ----------

# 只匹配值开头的字段名，避免把「患者姓名：张三，主诉：…」里的姓名一起保留
_MEDICAL_LABEL_RE = re.compile(
    r"^\s*(主诉|初步诊断|出院诊断|诊断|现病史|既往史|辅助检查|病情|症状|查体|住院|手术|用药|病历(?:记录)?)"
    r"\s*([:：=])\s*"
)


def _mask_medical(value: str) -> str:
    """保留「主诉 / 初步诊断」等字段名，字段值整体用 * 代替。"""
    match = _MEDICAL_LABEL_RE.match(value)
    if match:
        label, sep = match.group(1), match.group(2)
        rest = value[match.end():]
        return f"{label}{sep}" + "*" * max(1, min(len(rest), MAX_STARS))
    return "*" * max(1, min(len(value), MAX_STARS))


# 敏感词表（knowledge base）命中指向的是「类别词」而不是敏感值，
# 例如正文里出现「身份证」三个字本身，不该被替换成 [已屏蔽:身份证号]。
KEYWORD_RULE_PREFIX = "kw-"


def mask_value(value: str, entity: EntityType, level: Level) -> str:
    """按级别对单个敏感值做脱敏。"""
    if not value or level == Level.SAFE:
        return value
    # 医疗字段保留「主诉 / 初步诊断」等字段名，值用星号；L3 也不整句替换成类别名。
    if entity == EntityType.MEDICAL:
        masked = _mask_medical(value)
        return "[低危]" + masked if level == Level.LOW else masked
    # 地址统一保留省/市/区、街道起用星号，不因整段升级到 L3 而改成整体占位。
    if entity == EntityType.ADDRESS:
        masked = _mask_address(value)
        return "[低危]" + masked if level == Level.LOW else masked
    # 身份证保留地区码与末 4 位，中间统一星号：既保留可核对性，又不暴露出生日期。
    if entity == EntityType.ID_CARD:
        masked = _mask_id_card(value)
        return "[低危]" + masked if level == Level.LOW else masked
    if level == Level.HIGH:
        return entity_label(entity)
    if level == Level.MID:
        # 姓名首字是姓氏、证件首字符同样可用于缩小范围，统一收紧。
        if entity == EntityType.NAME:
            return "*" * len(value)
        if entity == EntityType.BANK_CARD:
            return entity_label(entity)
        return value[0] + _stars(len(value))
    if level == Level.LOW:
        # 低危只表示「需要复核」，不代表可以把敏感值明文展示。
        return "[低危]" + mask_value(value, entity, Level.MID)
    return value


def mask(text: str, result: Result, level: Level | None = None) -> str:
    """按命中区间替换脱敏副本；``level`` 可覆盖（用于"强制某种脱敏强度"的演示）。"""
    if not text or not result.hits:
        return text
    effective = level or result.level
    if effective == Level.SAFE:
        return text

    parts: list[str] = []
    cursor = 0
    for hit in sorted(result.hits, key=lambda item: item.start):
        if hit.rule.startswith(KEYWORD_RULE_PREFIX):
            continue
        if hit.start < cursor:
            continue
        parts.append(text[cursor:hit.start])
        parts.append(mask_value(hit.value, hit.type, effective))
        cursor = hit.end
    parts.append(text[cursor:])
    return "".join(parts)


# 医疗句级遮蔽的句子边界：不把逗号/顿号当边界，否则「症状，伴呕吐」会被拆开。
_MEDICAL_SENTENCE_DELIMS = frozenset("。！？!?；;\n")
# 单句长度上限：超过则不扩，避免无标点的长文整段被替换成「医疗信息」。
MAX_MEDICAL_SENTENCE = 200


def _sentence_bounds(text: str, start: int, end: int) -> tuple[int, int]:
    """返回命中所在句子的 [起, 止)：向左、向右各找到最近的分句标点。"""
    left = 0
    for index in range(start - 1, -1, -1):
        if text[index] in _MEDICAL_SENTENCE_DELIMS:
            left = index + 1
            break
    right = len(text)
    for index in range(end, len(text)):
        if text[index] in _MEDICAL_SENTENCE_DELIMS:
            right = index
            break
    while left < start and text[left].isspace():
        left += 1
    while right > end and text[right - 1].isspace():
        right -= 1
    return left, right


def expand_medical_hits(text: str, hits: list[Hit]) -> list[Hit]:
    """把医疗命中扩展到所在句子，切断「症状 → 诊断」的上下文反推。

    只扩 L1 规则命中：``kw-`` 敏感词表指向的是类别词本身，不是敏感值。
    同一句里的多个医疗命中合并成一条；句子过长（> MAX_MEDICAL_SENTENCE）保持原区间。
    其他类别若落在被扩展的句子里，不再单独保留（整句已被医疗占位覆盖）。
    """
    if not text or not hits:
        return list(hits)
    ranges: list[tuple[int, int]] = []
    rest: list[Hit] = []
    for hit in hits:
        if hit.type != EntityType.MEDICAL or hit.rule.startswith(KEYWORD_RULE_PREFIX):
            rest.append(hit)
            continue
        start, end = _sentence_bounds(text, hit.start, hit.end)
        if end - start > MAX_MEDICAL_SENTENCE:
            rest.append(hit)
            continue
        ranges.append((start, end))
    if not ranges:
        return list(hits)

    ranges.sort()
    merged: list[list[int]] = []
    for start, end in ranges:
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])

    expanded = [
        Hit(
            type=EntityType.MEDICAL,
            start=start,
            end=end,
            value=text[start:end],
            weight=0.90,
            confidence=0.95,
            rule="medical-sentence",
        )
        for start, end in merged
    ]
    for hit in rest:
        if any(start < hit.end and hit.start < end for start, end in merged):
            continue
        expanded.append(hit)
    expanded.sort(key=lambda item: item.start)
    return expanded


def mask_hits(text: str, hits: list[Hit], level: Level) -> str:
    """只给命中列表与级别，直接产出脱敏文本（脱敏算子的通用入口）。"""
    if not text or not hits or level == Level.SAFE:
        return text
    parts: list[str] = []
    cursor = 0
    for hit in sorted(hits, key=lambda item: item.start):
        if hit.rule.startswith(KEYWORD_RULE_PREFIX):
            continue
        if hit.start < cursor:
            continue
        parts.append(text[cursor:hit.start])
        parts.append(mask_value(hit.value, hit.type, level))
        cursor = hit.end
    parts.append(text[cursor:])
    return "".join(parts)


# ============================================================================
# 导出侧多策略脱敏（与四级分级正交：分级决定"要不要脱"，策略决定"怎么脱"）
#
# 现有 mask_value / mask / mask_hits 仍由 pipeline 用于"分级脱敏副本"，
# 这里新增 MaskStrategy 枚举与 mask_by_strategy，专供 /detect/export 导出使用：
# 用户在前端选择策略（block/partial/hash/generalize/pseudonym/encrypt），
# 系统按策略对命中值重新脱敏，重建完整文件后返回。
# ============================================================================


class MaskStrategy(str, Enum):
    """导出策略（与四级分级正交）。"""

    BLOCK = "block"           # 全屏蔽为类别占位（身份证保留前 6 后 4）
    PARTIAL = "partial"       # 保留首字符 + 星号
    HASH = "hash"             # SHA256 前 8 位
    GENERALIZE = "generalize"  # 数值泛化 / 地址保留省市区 / 手机号保留前 3
    PSEUDONYM = "pseudonym"   # 假名替换
    ENCRYPT = "encrypt"       # AES-GCM 加密（密钥随文件返回）

    @classmethod
    def parse(cls, value: str) -> "MaskStrategy":
        """字符串转枚举；非法值抛 ValueError（路由层捕获后返 400）。"""
        text = (value or "").strip().lower()
        for item in cls:
            if item.value == text:
                return item
        raise ValueError(f"未知脱敏策略: {value!r}，可选 {sorted(s.value for s in cls)}")


# 假名姓氏池（pseudonym 策略按 hash 选姓，避免可逆）
_PSEUDONYM_SURNAMES: tuple[str, ...] = (
    "赵", "钱", "孙", "李", "周", "吴", "郑", "王",
    "冯", "陈", "褚", "卫", "蒋", "沈", "韩", "杨",
)


def _mask_block(value: str, entity: EntityType) -> str:
    """全屏蔽为类别占位（block 策略；身份证保留前 6 后 4 的星号形式）。"""
    if entity == EntityType.ID_CARD:
        return _mask_id_card(value)
    return entity_label(entity)


def _mask_partial(value: str, entity: EntityType) -> str:
    """部分遮掩：按类别决定保留多少首字符。

    「保留首字符」只适合手机号、邮箱这类首字符本身信息量低的字段；
    姓名首字是姓氏、地址首字是行政区、诊断首字几乎等于病名，
    保留它们会让 partial 退化成可反推，因此按类别收紧：
    - 姓名：全星号，不保留姓氏；
    - 地址：保留省/市/区，街道及门牌用星号；
    - 医疗：保留字段名，字段值用星号；
    - 身份证：保留前 6 位与后 4 位，中间用星号；
    - 银行卡：直接占位，避免首尾数字泄漏。
    """
    if not value:
        return value
    if entity == EntityType.NAME:
        return "*" * len(value)
    if entity == EntityType.ADDRESS:
        return _mask_address(value)
    if entity == EntityType.MEDICAL:
        return _mask_medical(value)
    if entity == EntityType.ID_CARD:
        return _mask_id_card(value)
    if entity == EntityType.BANK_CARD:
        return entity_label(entity)
    return value[0] + _stars(len(value))


def _mask_hash(value: str, entity: EntityType) -> str:
    """SHA256 前 8 位（不可逆，可做对位审计）。"""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:8]


def _mask_generalize(value: str, entity: EntityType) -> str:
    """数值泛化 / 地址保留省市区 / 手机号保留前 3 / 其余回退 block。"""
    if entity == EntityType.PHONE and len(value) >= 7:
        return value[:3] + "****" + value[-1:]
    if entity == EntityType.ADDRESS:
        return _mask_address(value)
    if entity == EntityType.ID_CARD and len(value) >= 6:
        return value[:6] + "********"
    # 数值类（金额、年龄等）：尝试解析为 float 后泛化到区间
    try:
        num = float(value)
        return generalize_numeric(num, bucket=10)
    except ValueError:
        pass
    return _mask_block(value, entity)


def _mask_pseudonym(value: str, entity: EntityType) -> str:
    """假名替换：姓名→某姓；身份证→保留前后；其余回退 partial。"""
    if entity == EntityType.NAME:
        digest = hashlib.sha256(value.encode("utf-8")).digest()
        surname = _PSEUDONYM_SURNAMES[digest[0] % len(_PSEUDONYM_SURNAMES)]
        return f"{surname}某某"
    if entity == EntityType.ID_CARD and len(value) >= 2:
        return value[:1] + "*" * (len(value) - 2) + value[-1:]
    if entity == EntityType.PHONE and len(value) >= 7:
        return value[:3] + "****" + value[-4:]
    return _mask_partial(value, entity)


def _generate_encrypt_key() -> bytes:
    """生成 32 字节 Fernet 密钥（base64 编码后 44 字符）。"""
    try:
        from cryptography.fernet import Fernet
    except ImportError as exc:
        raise RuntimeError("encrypt 策略需要 cryptography：pip install cryptography") from exc
    return Fernet.generate_key()


def _mask_encrypt(value: str, entity: EntityType, encrypt_key: bytes) -> str:
    """AES-GCM 加密（Fernet 封装，密钥随文件返回）。"""
    try:
        from cryptography.fernet import Fernet
    except ImportError as exc:
        raise RuntimeError("encrypt 策略需要 cryptography：pip install cryptography") from exc
    token = Fernet(encrypt_key).encrypt(value.encode("utf-8"))
    return token.decode("ascii")


def mask_by_strategy(
    value: str,
    entity: EntityType,
    strategy: MaskStrategy,
    *,
    encrypt_key: bytes | None = None,
) -> str:
    """按策略对单个敏感值脱敏。

    encrypt 策略需要 encrypt_key；其余策略忽略该参数。
    空值原样返回。
    """
    if not value:
        return value
    if strategy == MaskStrategy.BLOCK:
        return _mask_block(value, entity)
    if strategy == MaskStrategy.PARTIAL:
        return _mask_partial(value, entity)
    if strategy == MaskStrategy.HASH:
        return _mask_hash(value, entity)
    if strategy == MaskStrategy.GENERALIZE:
        return _mask_generalize(value, entity)
    if strategy == MaskStrategy.PSEUDONYM:
        return _mask_pseudonym(value, entity)
    if strategy == MaskStrategy.ENCRYPT:
        if encrypt_key is None:
            encrypt_key = _generate_encrypt_key()
        return _mask_encrypt(value, entity, encrypt_key)
    # 理论不可达：策略枚举已穷举
    return value


def mask_text_by_strategy(
    text: str,
    hits: list[Hit],
    strategy: MaskStrategy,
    *,
    encrypt_key: bytes | None = None,
) -> str:
    """按策略对一段文本里的所有命中做脱敏（导出重建时用）。

    跳过敏感词表命中（KEYWORD_RULE_PREFIX），与现有 mask 行为一致。
    重叠命中按起始位置排序后顺序处理。
    """
    if not text or not hits:
        return text
    parts: list[str] = []
    cursor = 0
    for hit in sorted(hits, key=lambda item: item.start):
        if hit.rule.startswith(KEYWORD_RULE_PREFIX):
            continue
        if hit.start < cursor:
            continue
        parts.append(text[cursor:hit.start])
        parts.append(mask_by_strategy(hit.value, hit.type, strategy, encrypt_key=encrypt_key))
        cursor = hit.end
    parts.append(text[cursor:])
    return "".join(parts)
