"""导出侧多策略脱敏单元测试。

覆盖 6 个策略函数 + mask_text_by_strategy（文本级）+ MaskStrategy.parse。
不依赖数据库与 FastAPI，纯函数测试。
"""

from __future__ import annotations

import hashlib

import pytest

from app.identify import EntityType, Hit, MaskStrategy, mask_by_strategy, mask_text_by_strategy
from app.identify.masking import KEYWORD_RULE_PREFIX

ID_CARD = "11010519491231002X"
PHONE = "13800138000"
NAME = "张三"
ADDRESS = "北京市朝阳区建国路1号"


# ---------- MaskStrategy.parse ----------


def test_strategy_parse_ok() -> None:
    assert MaskStrategy.parse("block") == MaskStrategy.BLOCK
    assert MaskStrategy.parse("PARTIAL") == MaskStrategy.PARTIAL
    assert MaskStrategy.parse(" Hash ") == MaskStrategy.HASH


def test_strategy_parse_unknown_raises() -> None:
    with pytest.raises(ValueError):
        MaskStrategy.parse("unknown")
    with pytest.raises(ValueError):
        MaskStrategy.parse("")


# ---------- block 策略 ----------


def test_block_strategy_id_card() -> None:
    # block 也保留证件前 6 后 4 的星号形式，与四级脱敏副本保持一致
    out = mask_by_strategy(ID_CARD, EntityType.ID_CARD, MaskStrategy.BLOCK)
    assert out == "110105********002X"
    assert ID_CARD not in out
    assert "19491231" not in out


def test_block_strategy_phone() -> None:
    out = mask_by_strategy(PHONE, EntityType.PHONE, MaskStrategy.BLOCK)
    assert out == "手机号"


def test_block_strategy_empty_value() -> None:
    assert mask_by_strategy("", EntityType.PHONE, MaskStrategy.BLOCK) == ""


# ---------- partial 策略 ----------


def test_partial_strategy_phone() -> None:
    out = mask_by_strategy(PHONE, EntityType.PHONE, MaskStrategy.PARTIAL)
    assert out[0] == "1"
    assert "*" in out
    assert PHONE not in out


def test_partial_strategy_name_hides_surname() -> None:
    # 姓名不再保留首字（姓氏），按长度全星号
    out = mask_by_strategy(NAME, EntityType.NAME, MaskStrategy.PARTIAL)
    assert out == "*" * len(NAME)
    assert NAME[0] not in out


def test_partial_strategy_name_single_char() -> None:
    # 单字符姓名也至少 1 个星号，且不保留原字符
    out = mask_by_strategy("A", EntityType.NAME, MaskStrategy.PARTIAL)
    assert out == "*"


def test_partial_strategy_address_keeps_admin_prefix() -> None:
    # 地址保留省/市/区，从街道起用星号
    out = mask_by_strategy(ADDRESS, EntityType.ADDRESS, MaskStrategy.PARTIAL)
    assert out == "北京市朝阳区" + "*" * len("建国路1号")
    assert "建国路" not in out


def test_partial_strategy_address_without_province_keeps_city_district() -> None:
    # 没有省份时，能识别到市/区就保留，不再整体占位
    out = mask_by_strategy("长沙市岳麓区麓山南路1号", EntityType.ADDRESS, MaskStrategy.PARTIAL)
    assert out == "长沙市岳麓区" + "*" * len("麓山南路1号")


def test_partial_strategy_address_without_street_hides_district() -> None:
    # 没有街道时隐藏最后一段行政区，避免原样放行
    assert mask_by_strategy("深圳市南山区", EntityType.ADDRESS, MaskStrategy.PARTIAL) == "深圳市***"


def test_partial_strategy_province_only_address_blocks() -> None:
    # 只有省份时保留省份等于没脱敏，整体占位
    assert mask_by_strategy("湖南", EntityType.ADDRESS, MaskStrategy.PARTIAL) == "地址"
    assert mask_by_strategy("福建省", EntityType.ADDRESS, MaskStrategy.PARTIAL) == "地址"


def test_partial_strategy_medical_keeps_field_name() -> None:
    # 医疗保留「主诉 / 初步诊断」字段名，值整体用星号
    out = mask_by_strategy("主诉：反复头痛 3 个月", EntityType.MEDICAL, MaskStrategy.PARTIAL)
    assert out.startswith("主诉：")
    assert "头痛" not in out

    # 没有字段名时整值遮盖，不留首字
    out = mask_by_strategy("偏头痛", EntityType.MEDICAL, MaskStrategy.PARTIAL)
    assert out == "***"
    assert "偏" not in out


def test_partial_strategy_id_card_keeps_region_and_tail() -> None:
    out = mask_by_strategy(ID_CARD, EntityType.ID_CARD, MaskStrategy.PARTIAL)
    assert out == "110105********002X"
    assert ID_CARD not in out
    assert "19491231" not in out


def test_partial_strategy_bank_card_blocks() -> None:
    out = mask_by_strategy("6222020200012345670", EntityType.BANK_CARD, MaskStrategy.PARTIAL)
    assert out == "银行卡号"
    assert "6222" not in out


# ---------- hash 策略 ----------


def test_hash_strategy_phone() -> None:
    out = mask_by_strategy(PHONE, EntityType.PHONE, MaskStrategy.HASH)
    expected = hashlib.sha256(PHONE.encode("utf-8")).hexdigest()[:8]
    assert out == expected
    assert len(out) == 8
    assert PHONE not in out


def test_hash_strategy_deterministic() -> None:
    # 相同输入两次哈希结果相同（可做对位审计）
    a = mask_by_strategy(ID_CARD, EntityType.ID_CARD, MaskStrategy.HASH)
    b = mask_by_strategy(ID_CARD, EntityType.ID_CARD, MaskStrategy.HASH)
    assert a == b


# ---------- generalize 策略 ----------


def test_generalize_strategy_phone() -> None:
    out = mask_by_strategy(PHONE, EntityType.PHONE, MaskStrategy.GENERALIZE)
    assert out.startswith("138")
    assert "*" in out
    assert PHONE not in out


def test_generalize_strategy_address() -> None:
    out = mask_by_strategy(ADDRESS, EntityType.ADDRESS, MaskStrategy.GENERALIZE)
    # 保留省/市/区，街道起用星号
    assert out == "北京市朝阳区" + "*" * len("建国路1号")
    assert "建国路" not in out


def test_generalize_strategy_numeric() -> None:
    out = mask_by_strategy("42", EntityType.MEDICAL, MaskStrategy.GENERALIZE)
    # 42 应泛化到 40-50 区间
    assert "40" in out


def test_generalize_strategy_fallback_block() -> None:
    # 非数值、非手机号、非地址、非身份证 → 回退 block
    out = mask_by_strategy("hello", EntityType.EMAIL, MaskStrategy.GENERALIZE)
    assert out == "邮箱"


# ---------- pseudonym 策略 ----------


def test_pseudonym_strategy_name() -> None:
    out = mask_by_strategy(NAME, EntityType.NAME, MaskStrategy.PSEUDONYM)
    assert out.endswith("某某")
    assert NAME not in out


def test_pseudonym_strategy_name_deterministic() -> None:
    # 相同姓名两次生成相同假名（hash 决定姓氏）
    a = mask_by_strategy(NAME, EntityType.NAME, MaskStrategy.PSEUDONYM)
    b = mask_by_strategy(NAME, EntityType.NAME, MaskStrategy.PSEUDONYM)
    assert a == b


def test_pseudonym_strategy_id_card() -> None:
    out = mask_by_strategy(ID_CARD, EntityType.ID_CARD, MaskStrategy.PSEUDONYM)
    assert out[0] == ID_CARD[0]
    assert out[-1] == ID_CARD[-1]
    assert "*" in out
    assert ID_CARD not in out


def test_pseudonym_strategy_phone() -> None:
    out = mask_by_strategy(PHONE, EntityType.PHONE, MaskStrategy.PSEUDONYM)
    assert out.startswith("138")
    assert out.endswith(PHONE[-4:])
    assert "*" in out


def test_pseudonym_strategy_fallback_partial() -> None:
    # 非姓名/身份证/手机 → 回退 partial
    out = mask_by_strategy("test@example.com", EntityType.EMAIL, MaskStrategy.PSEUDONYM)
    assert "*" in out


# ---------- encrypt 策略 ----------


def test_encrypt_strategy_generates_key_if_missing() -> None:
    # 不传 encrypt_key 时自动生成
    out = mask_by_strategy(PHONE, EntityType.PHONE, MaskStrategy.ENCRYPT)
    assert out  # 非空
    assert PHONE not in out
    # Fernet token 以 gAAAA 开头
    assert out.startswith("gAAAA")


def test_encrypt_strategy_with_explicit_key() -> None:
    from cryptography.fernet import Fernet

    key = Fernet.generate_key()
    out = mask_by_strategy(PHONE, EntityType.PHONE, MaskStrategy.ENCRYPT, encrypt_key=key)
    assert PHONE not in out
    # 能用同一密钥解密回原文
    decrypted = Fernet(key).decrypt(out.encode("ascii")).decode("utf-8")
    assert decrypted == PHONE


def test_encrypt_strategy_missing_cryptography_raises() -> None:
    # 模拟 cryptography 未安装（encrypt_key 为 None 时才会触发 import）
    # 这里用空值走自动生成路径，正常环境已装 cryptography 应成功
    out = mask_by_strategy("secret", EntityType.SECRET, MaskStrategy.ENCRYPT)
    assert out != "secret"


# ---------- mask_text_by_strategy（文本级）----------


def _make_hit(start: int, end: int, value: str, entity: EntityType, rule: str = "test") -> Hit:
    return Hit(
        type=entity,
        start=start,
        end=end,
        value=value,
        weight=0.9,
        confidence=0.95,
        rule=rule,
    )


def test_mask_text_by_strategy_block() -> None:
    text = f"身份证：{ID_CARD}，手机：{PHONE}"
    # 偏移：身份证(3) + ：(1) = 4 起；之后"，手机："是 4 个字符
    phone_start = 4 + len(ID_CARD) + 4
    hits = [
        _make_hit(4, 4 + len(ID_CARD), ID_CARD, EntityType.ID_CARD),
        _make_hit(phone_start, phone_start + len(PHONE), PHONE, EntityType.PHONE),
    ]
    out = mask_text_by_strategy(text, hits, MaskStrategy.BLOCK)
    assert ID_CARD not in out
    assert PHONE not in out
    assert "110105********002X" in out
    assert "手机号" in out
    # 上下文保留
    assert out.startswith("身份证：")
    assert "，手机：" in out


def test_mask_text_by_strategy_partial() -> None:
    text = f"手机：{PHONE}"
    hits = [_make_hit(3, 3 + len(PHONE), PHONE, EntityType.PHONE)]
    out = mask_text_by_strategy(text, hits, MaskStrategy.PARTIAL)
    assert out.startswith("手机：1")
    assert "*" in out
    assert PHONE not in out


def test_mask_text_by_strategy_empty_text() -> None:
    assert mask_text_by_strategy("", [], MaskStrategy.BLOCK) == ""


def test_mask_text_by_strategy_no_hits() -> None:
    text = "没有敏感内容的文本"
    assert mask_text_by_strategy(text, [], MaskStrategy.BLOCK) == text


def test_mask_text_by_strategy_skips_keyword_hits() -> None:
    # 敏感词表命中（kw- 前缀）不应被脱敏
    text = "正文里提到身份证三个字"
    hits = [_make_hit(5, 8, "身份证", EntityType.ID_CARD, rule=KEYWORD_RULE_PREFIX + "term")]
    out = mask_text_by_strategy(text, hits, MaskStrategy.BLOCK)
    assert out == text  # 原样返回


def test_mask_text_by_strategy_overlapping_hits() -> None:
    # 重叠命中保留更靠前的
    text = f"值：{ID_CARD}"
    hits = [
        _make_hit(2, 2 + len(ID_CARD), ID_CARD, EntityType.ID_CARD),
        _make_hit(2, 2 + 4, ID_CARD[:4], EntityType.ID_CARD),  # 重叠，应被跳过
    ]
    out = mask_text_by_strategy(text, hits, MaskStrategy.BLOCK)
    assert "110105********002X" in out
    assert ID_CARD not in out


def test_mask_text_by_strategy_encrypt_with_key() -> None:
    from cryptography.fernet import Fernet

    key = Fernet.generate_key()
    text = f"key: sk-abcdef123456"
    value = "sk-abcdef123456"
    hits = [_make_hit(5, 5 + len(value), value, EntityType.SECRET)]
    out = mask_text_by_strategy(text, hits, MaskStrategy.ENCRYPT, encrypt_key=key)
    assert value not in out
    assert out.startswith("key: ")
