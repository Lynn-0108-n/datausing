"""对比后端纯单元测试：偏移正确性、mappings 语义与安全边界。

不需要启动服务，也不需要 L2/L3/NER 模型：`DetectionPipeline()` 默认走 L1 规则，
毫秒级完成。运行：

    cd FlowMask\\server
    ..\\runtime\\python.exe -m pytest tests -m "not integration" -q
"""

from __future__ import annotations

import hashlib

import pytest

from app.identify import DetectionPipeline, DetectOptions, EntityType, Level
from app.identify.masking import mask_spans_with_strategy
from app.identify.pipeline import DocumentReport, SegmentFinding
from app.identify.snapshots import SnapshotStore
from app.parsers.base import Document, Segment
from app.parsers.json_parser import parse_json
from app.parsers.text import parse_text
from app.security import SecurityError, protected_text
from app.tasks.comparison import comparison_payload

# 全部为自造测试数据：占位姓名 + 公共示例手机号 + 公共示例身份证校验位示例。
TEXT = "患者姓名：张三，手机 13800138000，身份证 11010519491231002X。备注：😀 emoji 前后偏移。"
TEXT_MASKED = (
    "患者姓名：[已屏蔽:姓名]，手机 [已屏蔽:手机号]，身份证 [已屏蔽:身份证号]。"
    "备注：😀 emoji 前后偏移。"
)
TEXT_CHANGES = [
    # 升序（按原文 start），block 策略（整段命中且任务级别 L3）
    dict(start=5, end=7, masked_start=5, masked_end=13, type="name", strategy="block", level="L3", status="已脱敏"),
    dict(start=11, end=22, masked_start=17, masked_end=26, type="phone", strategy="block", level="L3", status="已脱敏"),
    dict(start=27, end=45, masked_start=31, masked_end=41, type="id_card", strategy="block", level="L3", status="已脱敏"),
]

CLEAN_TEXT = "This is a normal meeting note. Lorem ipsum dolor sit amet, consectetur adipiscing elit."


# ---------------------------------------------------------------------------
# 测试辅助：复刻 service._execute 的构造顺序（先按 protected_text 重算 masked 再存快照）
# ---------------------------------------------------------------------------


def _build_snapshot(document, *, options=None, store=None, result_id="task:unit",
                    owner="owner-unit", secret="secret-unit"):
    """跑识别 → 重算脱敏副本 → 存快照，返回 comparison_payload 可用的 Snapshot。"""
    options = options or DetectOptions()
    report = DetectionPipeline().analyze_document(document, options)
    by_path = {segment.path: segment for segment in document.segments}
    for finding in report.all_findings:
        segment = by_path[finding.path]
        assert finding.original_hash == hashlib.sha256(segment.text.encode("utf-8")).hexdigest()
        # service._execute 的真实顺序：先 protected_text 重算一致副本，再落盘/存快照
        finding.masked = protected_text(segment.text, finding, options.mask_level)
    store = store or SnapshotStore(ttl=300)
    return store.put(
        document,  # put 内部会 deepcopy，避免测试之间互相污染
        report,
        {"mask_level": options.mask_level.value if options.mask_level else None},
        hashlib.sha256(document.raw.encode("utf-8")).hexdigest(),
        "rules-rev-unit",
        owner,
        secret,
        result_id=result_id,
    )


def _assert_change_invariants(segment: dict) -> None:
    """逐条校验 original/masked 的 codepoint 切片自洽。"""
    original, masked = segment["original"], segment["masked"]
    prev_original_end = -1
    prev_masked_end = -1
    for change in segment["changes"]:
        start, end = change["start"], change["end"]
        masked_start, masked_end = change["masked_start"], change["masked_end"]

        # 1) 偏移是 Python 字符串（unicode codepoint）下标：切片不抛错、区间合法
        assert 0 <= start < end <= len(original)
        assert 0 <= masked_start < masked_end <= len(masked)
        # 2) 升序且互不重叠——前端可以放心顺序渲染
        assert start >= prev_original_end
        assert masked_start >= prev_masked_end
        prev_original_end, prev_masked_end = end, masked_end

        hit_text = original[start:end]
        replacement = masked[masked_start:masked_end]
        # 3) original 切片是被替换的敏感文本，masked 切片是替换后文本
        assert hit_text and replacement
        assert hit_text != replacement
        assert change["status"] == "已脱敏"
        assert change["level"] in {"L1", "L2", "L3"}
        assert change["strategy"] in {"block", "partial"}

    # 4) 用 changes 右→左回放原文，必须逐字符等于 masked（校验两套偏移配对一致）
    rebuilt = original
    for change in reversed(segment["changes"]):
        rebuilt = (
            rebuilt[: change["start"]]
            + masked[change["masked_start"]: change["masked_end"]]
            + rebuilt[change["end"]:]
        )
    assert rebuilt == masked

    # 5) 未命中区域不被误标：首条 change 之前、末条 change 之后必须原样保留
    if segment["changes"]:
        first, last = segment["changes"][0], segment["changes"][-1]
        assert original[: first["start"]] == masked[: first["masked_start"]]
        assert original[last["end"]:] == masked[last["masked_end"]:]
    else:
        assert masked == original


# ---------------------------------------------------------------------------
# 偏移正确性
# ---------------------------------------------------------------------------


def test_single_segment_offsets_are_unicode_codepoints():
    document = parse_text(TEXT, source="sample-a.txt")
    snapshot = _build_snapshot(document)
    payload = comparison_payload(snapshot)

    assert payload["filename"] == "sample-a.txt"
    assert payload["status"] == "done"
    assert payload["coverage"] == "complete"
    assert payload["representation"] == "parsed-segments"
    assert payload["offset_unit"] == "unicode-codepoint"
    assert payload["hits"] == 4 == sum(f.hits for f in snapshot.report.all_findings)
    assert len(payload["segments"]) == 1

    segment = payload["segments"][0]
    assert segment["path"] == "$"
    assert segment["original"] == TEXT
    assert segment["masked"] == TEXT_MASKED
    assert segment["changes"] == TEXT_CHANGES
    _assert_change_invariants(segment)

    # emoji 计 1 个 codepoint；中文命中值切片精确
    assert len("😀") == 1
    assert segment["original"][5:7] == "张三"
    assert segment["original"][11:22] == "13800138000"
    assert segment["original"][27:45] == "11010519491231002X"
    assert segment["masked"][5:13] == "[已屏蔽:姓名]"
    assert segment["masked"][17:26] == "[已屏蔽:手机号]"
    assert segment["masked"][31:41] == "[已屏蔽:身份证号]"

    # 命中之后的所有内容（含 astral 平面的 emoji）保持原样
    assert segment["masked"].endswith("备注：😀 emoji 前后偏移。")


def test_multi_segment_json_offsets_after_hint_rebase():
    raw = (
        '{"name": "张三", "phone": "13800138000", "safe": "hello world", '
        '"id": "11010519491231002X"}'
    )
    document = parse_json(raw, source="b.json")
    snapshot = _build_snapshot(document)
    payload = comparison_payload(snapshot)

    assert payload["filename"] == "b.json"
    assert payload["hits"] == 3
    assert [segment["path"] for segment in payload["segments"]] == [
        "$.name", "$.phone", "$.safe", "$.id",
    ]
    by_path = {segment["path"]: segment for segment in payload["segments"]}

    # 检测时 hint（"name: " / "phone: "）作为前缀参与匹配，随后必须 rebase 回原值偏移
    assert by_path["$.name"]["original"] == "张三"
    assert by_path["$.name"]["masked"] == "张*"
    assert by_path["$.name"]["changes"] == [
        dict(start=0, end=2, masked_start=0, masked_end=2, type="name",
             strategy="partial", level="L1", status="已脱敏")
    ]
    assert by_path["$.phone"]["masked"] == "1******"
    assert by_path["$.phone"]["changes"] == [
        dict(start=0, end=11, masked_start=0, masked_end=7, type="phone",
             strategy="partial", level="L2", status="已脱敏")
    ]
    # 无命中的 segment 照样返回，changes 为空、原文不动
    assert by_path["$.safe"] == {
        "path": "$.safe", "original": "hello world", "masked": "hello world", "changes": []
    }
    assert by_path["$.id"]["masked"] == "[已屏蔽:身份证号]"
    assert by_path["$.id"]["changes"] == [
        dict(start=0, end=18, masked_start=0, masked_end=10, type="id_card",
             strategy="block", level="L3", status="已脱敏")
    ]
    for segment in payload["segments"]:
        _assert_change_invariants(segment)


def test_mask_spans_mappings_are_left_to_right_after_emoji():
    """直接验证 masking 层：替换从右→左执行，但 mappings 按原文 start 升序返回。"""
    text = "前😀张三后13800138000尾"
    assert len(text) == 17
    mappings: list[dict] = []
    masked = mask_spans_with_strategy(
        text,
        [
            {"start": 2, "end": 4, "type": EntityType.NAME.value},
            {"start": 5, "end": 16, "type": EntityType.PHONE.value},
        ],
        "partial",
        mappings=mappings,
    )
    assert masked == "前😀张*后1******尾"
    assert [(m["start"], m["end"], m["masked_start"], m["masked_end"]) for m in mappings] == [
        (2, 4, 2, 4),
        (5, 16, 5, 12),
    ]
    assert [m["strategy"] for m in mappings] == ["partial", "partial"]
    assert [m["type"] for m in mappings] == ["name", "phone"]
    # start 升序就是"左→右"：不是右侧命中排在前面
    assert [m["start"] for m in mappings] == sorted(m["start"] for m in mappings)
    assert masked[mappings[0]["masked_start"]: mappings[0]["masked_end"]] == "张*"
    assert masked[mappings[1]["masked_start"]: mappings[1]["masked_end"]] == "1******"


def test_full_segment_mask_mapping_is_wholesale():
    """requires_full_mask 的 finding 会整段替换，mapping 覆盖全文并带 full_segment 标记。"""
    finding = SegmentFinding(
        path="$",
        original_hash=hashlib.sha256(b"secret").hexdigest(),
        masked="",
        level=Level.HIGH,
        score=1.0,
        types=["secret"],
        hits=1,
        rule_ids=["secret_kv"],
        confidence_vector=[],
        model_chain="l1",
        spans=[{"start": 0, "end": 6, "type": "secret", "label": "密钥", "rule": "secret_kv"}],
        requires_full_mask=True,
    )
    mappings: list[dict] = []
    masked = protected_text("secret", finding, mappings=mappings)
    assert masked == "[已屏蔽:敏感信息]"
    assert mappings == [{
        "start": 0, "end": 6,
        "masked_start": 0, "masked_end": len("[已屏蔽:敏感信息]"),
        "type": "secret", "strategy": "block", "full_segment": True,
    }]


def test_hits_include_keyword_hits_while_changes_do_not():
    """hits 计数含敏感词表（kw-）命中；changes 只含真正替换的区间，所以 hits ≥ len(changes)。"""
    snapshot = _build_snapshot(parse_text(TEXT, source="k.txt"))
    payload = comparison_payload(snapshot)
    total_changes = sum(len(segment["changes"]) for segment in payload["segments"])
    assert payload["hits"] == 4
    assert total_changes == 3
    assert payload["hits"] >= total_changes


# ---------------------------------------------------------------------------
# 安全边界：快照一致性 / 大小限制 / TTL / owner / 密钥版本
# ---------------------------------------------------------------------------


def test_comparison_rejects_tampered_document_422():
    snapshot = _build_snapshot(parse_text(TEXT, source="t.txt"))
    snapshot.document.segments[0].text = TEXT.replace("张三", "李四")
    with pytest.raises(SecurityError) as excinfo:
        comparison_payload(snapshot)
    assert (excinfo.value.code, excinfo.value.status_code) == ("MASK_LOCATION_INVALID", 422)


def test_comparison_rejects_tampered_masked_copy_409():
    snapshot = _build_snapshot(parse_text(TEXT, source="t.txt"))
    snapshot.report.all_findings[0].masked = "被人改过的脱敏副本"
    with pytest.raises(SecurityError) as excinfo:
        comparison_payload(snapshot)
    assert (excinfo.value.code, excinfo.value.status_code) == ("SNAPSHOT_POLICY_CHANGED", 409)


def test_comparison_rejects_oversized_document_413():
    big = "a" * 1_000_001
    document = Document(
        source="big.txt",
        content_type="text",
        segments=[Segment(path="$", text=big)],
        raw=big,
    )
    report = DocumentReport(source="big.txt", content_type="text", segments=1)
    store = SnapshotStore(ttl=300)
    snapshot = store.put(document, report, {"mask_level": None}, "hash", "rev", "o", "s",
                          result_id="task:big")
    with pytest.raises(SecurityError) as excinfo:
        comparison_payload(snapshot)
    assert (excinfo.value.code, excinfo.value.status_code) == ("COMPARISON_TOO_LARGE", 413)


class _FakeClock:
    def __init__(self, now: float = 1000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


def test_snapshot_ttl_is_300_seconds_and_expires_410():
    clock = _FakeClock()
    store = SnapshotStore(ttl=300, clock=clock)
    _build_snapshot(parse_text(TEXT, source="ttl.txt"), store=store, result_id="task:ttl")

    assert store.get("task:ttl", "owner-unit", "secret-unit") is not None  # 0s：有效
    clock.now += 299
    assert store.get("task:ttl", "owner-unit", "secret-unit") is not None  # 299s：仍有效
    clock.now += 2  # 301s > 300s
    with pytest.raises(SecurityError) as excinfo:
        store.get("task:ttl", "owner-unit", "secret-unit")
    assert (excinfo.value.code, excinfo.value.status_code) == ("SNAPSHOT_EXPIRED", 410)


def test_snapshot_owner_isolation_403():
    store = SnapshotStore(ttl=300)
    _build_snapshot(parse_text(TEXT, source="owner.txt"), store=store,
                    owner="owner-a", secret="secret-a", result_id="task:owner")

    with pytest.raises(SecurityError) as excinfo:
        store.get("task:owner", "owner-b", "secret-a")
    assert (excinfo.value.code, excinfo.value.status_code) == ("SNAPSHOT_FORBIDDEN", 403)

    with pytest.raises(SecurityError) as excinfo:
        store.get("task:owner", "", "secret-a")  # 不带会话 cookie 的请求
    assert (excinfo.value.code, excinfo.value.status_code) == ("SNAPSHOT_FORBIDDEN", 403)


def test_snapshot_key_revision_and_policy_version_409(monkeypatch):
    store = SnapshotStore(ttl=300)
    _build_snapshot(parse_text(TEXT, source="rev.txt"), store=store, result_id="task:rev")

    # 导出密钥变了（例如 FLOWMASK_EXPORT_SECRET 被改），旧快照不能再被解出/对齐
    with pytest.raises(SecurityError) as excinfo:
        store.get("task:rev", "owner-unit", "wrong-secret")
    assert (excinfo.value.code, excinfo.value.status_code) == ("SNAPSHOT_POLICY_CHANGED", 409)

    # 脱敏策略版本升级后，旧快照必须重新检测
    monkeypatch.setattr("app.security.POLICY_VERSION", "protected-v2")
    with pytest.raises(SecurityError) as excinfo:
        store.get("task:rev", "owner-unit", "secret-unit")
    assert (excinfo.value.code, excinfo.value.status_code) == ("SNAPSHOT_POLICY_CHANGED", 409)


def test_clean_segment_has_no_changes():
    document = parse_text(CLEAN_TEXT, source="clean.txt")
    snapshot = _build_snapshot(document)
    payload = comparison_payload(snapshot)
    assert payload["hits"] == 0
    assert payload["segments"][0]["changes"] == []
    assert payload["segments"][0]["masked"] == CLEAN_TEXT
