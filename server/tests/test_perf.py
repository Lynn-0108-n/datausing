"""识别层长文本性能与优化回归测试（行为等价 + 线性伸缩）。

为什么单列一个文件：``test_identify.py`` 只覆盖语义，这里锁定两件事：

1. **行为不变**：email 规则、地名扫描、编码候选扫描在优化前后必须给出同一结果。
   地名与编码两条用「参考实现」逐项比对（参考实现就是优化前的朴素写法），
   email 规则用黄金样例 + 长数字串反例锁定。
2. **近似线性**：用 20k → 80k 的耗时比值判定——线性约 4 倍，历史上的二次方缺陷
   会到 16 倍附近。比值与机器绝对速度无关，比易抖的绝对耗时断言稳。
   绝对耗时抽查放在 ``FLOWMASK_RUN_PERF=1`` 门控之后，供本机/发布前复核。

背景（v2 性能修复）：长数字串曾让 email 正则在每个起点回溯到串尾（O(n²)）；
地名密集文本曾让「命中区间是否重叠」线性扫描已收区间（O(h²)）；
base64 密集文本曾对同一候选片段解码两次。
"""

from __future__ import annotations

import base64
import os
import re
import time
import urllib.parse

import pytest

from app.identify import (
    DetectionPipeline,
    DetectOptions,
    EntityType,
    decode as decode_mod,
    detect,
    gazetteer as gazetteer_mod,
)
from app.identify.decode import _candidate_spans, decode_layers, iter_decoded_spans
from app.identify.gazetteer import iter_place_mentions
from app.identify.keywords import _PureAhoCorasick

# 线性伸缩判据：20k → 80k（4 倍数据）耗时比值上限。
# 实测修复后 ~4.0；修复前 email/地名两条缺陷会到 12~16。
RATIO_LIMIT = 8.0

# 长文本按块重复到指定长度，模拟 DLP 里常见的长行/日志/CSV 单元格。
SAMPLES: dict[str, str] = {
    "phone_dense": "13800138000 ",
    "place_dense": "在湖南省长沙市 在广东省深圳市 在四川省成都市 ",
    "base64_dense": "eyJwaG9uZSI6IjEzODAwMTM4MDAwIn0= ",
    "digits": "1234567890",
}


def _repeat_to(unit: str, size: int) -> str:
    return (unit * (size // len(unit) + 1))[:size]


def _best_elapsed(func, reps: int = 2) -> float:
    """跑 ``reps`` 次取最小值，抵消调度抖动（只用来算比值）。"""
    best = float("inf")
    for _ in range(reps):
        started = time.perf_counter()
        func()
        best = min(best, time.perf_counter() - started)
    return best


@pytest.mark.parametrize("sample_name", sorted(SAMPLES))
def test_analyze_time_scales_linearly(sample_name: str) -> None:
    """20k → 80k 数据量涨 4 倍，耗时不应明显超过 4 倍。"""
    pipeline = DetectionPipeline()
    options = DetectOptions()
    unit = SAMPLES[sample_name]

    small = _repeat_to(unit, 20_000)
    large = _repeat_to(unit, 80_000)
    # 预热一次，避免首次导入/懒初始化的固定开销算进比值
    pipeline.analyze_text(small, options)

    small_elapsed = _best_elapsed(lambda: pipeline.analyze_text(small, options))
    large_elapsed = _best_elapsed(lambda: pipeline.analyze_text(large, options))
    assert small_elapsed > 0 and large_elapsed > 0
    ratio = large_elapsed / small_elapsed
    assert ratio < RATIO_LIMIT, (
        f"{sample_name}: 20k={small_elapsed * 1000:.1f}ms, 80k={large_elapsed * 1000:.1f}ms, "
        f"比值 {ratio:.1f} ≥ {RATIO_LIMIT}，疑似退化回二次方"
    )


@pytest.mark.skipif(
    os.environ.get("FLOWMASK_RUN_PERF") != "1",
    reason="绝对耗时抽查默认跳过；设置 FLOWMASK_RUN_PERF=1 后运行",
)
@pytest.mark.parametrize("sample_name", sorted(SAMPLES))
def test_dense_40k_absolute_budget(sample_name: str) -> None:
    """40k 密集文本的单次耗时预算（本机参考值 20~60ms，留足机器差异余量）。"""
    pipeline = DetectionPipeline()
    options = DetectOptions()
    text = _repeat_to(SAMPLES[sample_name], 40_000)
    pipeline.analyze_text(text, options)  # 预热
    elapsed = _best_elapsed(lambda: pipeline.analyze_text(text, options))
    assert elapsed < 0.5, f"{sample_name}: 40k 单次 {elapsed * 1000:.1f}ms，超过 500ms 预算"


# --- email 规则：优化只加前向断言，匹配集合必须保持不变 -------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("邮箱 zhang.san@example.com", ["zhang.san@example.com"]),
        # 前面是汉字/中文标点时也要能命中（老实现靠本地部分从中间起匹配）
        ("联系人a@b.co", ["a@b.co"]),
        ("ID:user+tag@sub.example.cn", ["user+tag@sub.example.cn"]),
        # 起点处就是超长本地部分（连续字母数字段开头）时，整段仍然是一个合法邮箱
        ("1" * 2000 + "@example.com", ["1" * 2000 + "@example.com"]),
        ("prefix=alice@example.com;next=x@y.io", ["alice@example.com", "x@y.io"]),
        (".alice@example.com", [".alice@example.com"]),
    ],
)
def test_email_rule_matches_golden_examples(text: str, expected: list[str]) -> None:
    values = [hit.value for hit in detect(text) if hit.type is EntityType.EMAIL]
    assert values == expected


@pytest.mark.parametrize(
    "text",
    [
        "13800138000 " * 200,  # 长数字串（归一化会把数字间空格删掉，拼成超长连续数字）
        "aB3" * 3000,  # 长字母数字串
        "313338303031333830303030",  # 纯 hex 串
    ],
)
def test_email_rule_has_no_hits_inside_long_tokens(text: str) -> None:
    assert [hit for hit in detect(text) if hit.type is EntityType.EMAIL] == []


# --- 地名扫描：与「优化前的朴素实现」逐项等价 -----------------------------------------


def _naive_place_mentions(text: str) -> list[tuple[int, int, str, str]]:
    """优化前 ``iter_place_mentions`` 的朴素写法（重叠判断线性扫描）。"""
    mentions: list[tuple[int, int, str, str]] = []
    taken: list[tuple[int, int]] = []

    def overlaps(start: int, end: int) -> bool:
        return any(start < take_end and take_start < end for take_start, take_end in taken)

    for match in gazetteer_mod._DIRECTIONAL_RE.finditer(text):
        start, end = match.span(1)
        if overlaps(start, end):
            continue
        mentions.append((start, end, match.group(1), "direction"))
        taken.append((start, end))

    for match in re.finditer(gazetteer_mod._PLACE_ALT, text):
        start, end = match.span()
        if overlaps(start, end):
            continue
        if not re.match(gazetteer_mod._SUFFIX, text[end:]) and not re.match(
            r"\s*" + gazetteer_mod._SUFFIX, text[end:]
        ):
            continue
        mentions.append((start, end, match.group(0), "suffix"))
        taken.append((start, end))

    mentions.sort(key=lambda item: item[0])
    return mentions


PLACE_CORPUS = [
    "",
    "今天天气不错，系统运行正常",
    "他昨天从湖南过来，住了一晚就走了",
    "在湖南省长沙市 在广东省深圳市 在四川省成都市",
    "他从湖南长沙出发，先到上海开会",
    "收货地址：福建省福州市鼓楼区五四路 100 号，从江苏南京转来",
    "在北京和上海之间往返，途经天津，最后到内蒙古呼和浩特",
    "客户在杭州开会，晚点回公司；仓库在东莞虎门镇",
]


@pytest.mark.parametrize("text", PLACE_CORPUS)
def test_place_mentions_match_naive_reference(text: str) -> None:
    fast = [(m.start, m.end, m.place, m.reason) for m in iter_place_mentions(text)]
    assert fast == _naive_place_mentions(text)


def test_place_mentions_match_naive_reference_on_dense_text() -> None:
    """密集地名 + 方位词混排，专门压 bisect 相邻区间判断的边界。"""
    text = ("在湖南省长沙市 他从四川过来 收货地址：福建省福州市 " * 60)[:3000]
    fast = [(m.start, m.end, m.place, m.reason) for m in iter_place_mentions(text)]
    assert fast == _naive_place_mentions(text)


# --- 编码候选扫描：与「优化前的朴素实现」逐项等价 -------------------------------------


def _naive_candidate_spans(text: str) -> list[tuple[int, int]]:
    """优化前 ``_candidate_spans`` 的朴素写法（重叠判断线性扫描、解码两次）。"""
    found: list[tuple[int, int]] = []

    def overlaps(start: int, end: int) -> bool:
        return any(start < found_end and found_start < end for found_start, found_end in found)

    for pattern, expected in (
        (decode_mod._HEX_ESC_RE, "hex"),
        (decode_mod._HEX_RE, "hex"),
        (decode_mod._PERCENT_CHUNK_RE, "percent"),
        (decode_mod._BASE64_RE, "base64"),
    ):
        for match in pattern.finditer(text):
            if overlaps(match.start(), match.end()):
                continue
            decoded = decode_layers(match.group(0))
            if decoded is None or decoded[1].split(">")[0] != expected:
                continue
            found.append((match.start(), match.end()))
    found.sort()
    return found


PHONE_JSON = '{"phone":"13800138000"}'
ID_CARD_TEXT = "身份证 11010519491231002X 请勿外传"
PHONE_B64 = base64.b64encode(PHONE_JSON.encode()).decode()
ID_CARD_B64 = base64.b64encode(ID_CARD_TEXT.encode()).decode()
DOUBLE_B64 = base64.b64encode(PHONE_B64.encode()).decode()
PERCENT = urllib.parse.quote(PHONE_JSON)
B64_PERCENT = base64.b64encode(PERCENT.encode()).decode()
HEX_TEXT = PHONE_JSON.encode().hex()
HEX_ESC = "".join("\\x%02x" % byte for byte in ID_CARD_TEXT.encode())

DECODE_CORPUS = [
    "",
    "nothing to decode here",
    PHONE_B64,
    f"前缀 {PHONE_B64} 后缀",
    DOUBLE_B64,
    B64_PERCENT,
    f"payload={PERCENT}",
    HEX_ESC,
    HEX_TEXT,
    f"{PHONE_B64} 中间 {DOUBLE_B64} 再来 {HEX_TEXT}",
    "\\x31\\x33\\x38\\x30\\x30\\x31\\x33\\x38\\x30\\x30\\x30",
    f"{PHONE_B64}%20{ID_CARD_B64}",
    "x" * 40 + PHONE_B64,
]


@pytest.mark.parametrize("text", DECODE_CORPUS)
def test_decoded_spans_match_naive_reference(text: str) -> None:
    fast = [(span.start, span.end, span.encoding) for span in iter_decoded_spans(text)]
    naive: list[tuple[int, int, str]] = []
    for start, end in _naive_candidate_spans(text):
        decoded = decode_layers(text[start:end])
        if decoded is None:  # 参考实现里候选已保证可解码，防御性分支
            continue
        naive.append((start, end, decoded[1]))
    assert fast == naive

    # 解码文本也必须一致（优化只是复用了解码结果，不能改变内容）
    fast_text = [span.text for span in iter_decoded_spans(text)]
    naive_text = [decode_layers(text[start:end])[0] for start, end, _ in naive]
    assert fast_text == naive_text


def test_candidate_spans_prefer_hex_over_base64() -> None:
    """hex 与 base64 字母表有交集：同一片段被 hex 先收走后，base64 不应重复收。"""
    # 60 个 hex 字符同时是合法 base64 字符
    text = "313338303031333830303030" * 3
    spans = _candidate_spans(text)
    starts = [span[0] for span in spans]
    assert starts == sorted(starts)
    for (_, prev_end, _, _), (next_start, _, _, _) in zip(spans, spans[1:]):
        assert prev_end <= next_start


# --- 关键词回退：没装 pyahocorasick 时纯 Python 实现也要正确、近似线性 ------------------


KEYWORD_TERMS = ["湖南", "湖南长沙", "南长", "长沙", "password", "pass", "13800138000"]


def _naive_word_occurrences(text: str, words: list[str]) -> set[tuple[int, int, str]]:
    occurrences: set[tuple[int, int, str]] = set()
    for word in words:
        cursor = text.find(word)
        while cursor != -1:
            occurrences.add((cursor, cursor + len(word), word))
            cursor = text.find(word, cursor + 1)  # +1 才能发现重叠出现
    return occurrences


@pytest.mark.parametrize(
    "text",
    [
        "",
        "他从湖南长沙出发，途经南长街，密码 password 泄漏",
        "湖南湖南长沙长沙passwordpass湖南长沙",
        "13800138000 13800138000 湖南省",
    ],
)
def test_pure_python_automaton_matches_naive_scan(text: str) -> None:
    automaton = _PureAhoCorasick()
    for word in KEYWORD_TERMS:
        automaton.add(word)
    automaton.build()
    assert set(automaton.find_all(text)) == _naive_word_occurrences(text, KEYWORD_TERMS)


def test_pure_python_automaton_scales_linearly() -> None:
    """回退实现是逐字符状态机，不应随文本变长退化成乘法级。"""
    automaton = _PureAhoCorasick()
    for word in KEYWORD_TERMS:
        automaton.add(word)
    automaton.build()
    unit = "姓名 张三 手机号 13800138000 密码 password 湖南长沙 "
    small = _repeat_to(unit, 20_000)
    large = _repeat_to(unit, 80_000)
    automaton.find_all(small)  # 预热
    small_elapsed = _best_elapsed(lambda: automaton.find_all(small))
    large_elapsed = _best_elapsed(lambda: automaton.find_all(large))
    assert large_elapsed / small_elapsed < RATIO_LIMIT
