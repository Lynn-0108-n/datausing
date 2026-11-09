"""多策略脱敏文件导出：按用户选择的策略重建完整文件。

与现有 ``tasks/export.py`` 的区别：

- ``export.py`` 的 ``masked_document`` 只拼**命中片段**，``export_findings`` 导出的是命中明细 JSONL；
- 本模块重建**完整文件**（含未命中的上下文），让用户下载后直接能用——
  「上传一份 CSV → 下载脱敏后的完整 CSV」闭环。

设计要点（与 ``instruction.md`` P1 一致）：

1. **原文不落库**：导出发生在 ``/detect`` 同一请求内，用内存里的 ``Document`` 原文重建；
2. **策略可选**：``mask_strategy`` 由前端传入（block/partial/hash/generalize/pseudonym/encrypt），
   内部固定 ``min_level=Level.SAFE`` 覆盖所有命中；
3. **路径对齐**：JSON 按 ``json_parser._walk`` 同规则、CSV 按 ``row{n}.{列名}``、SQL 按 ``offset`` 倒序回写；
4. **docx/pdf 转纯文本**：不保留原版式，文件名扩展名改为 ``.txt``，UI 明示。
"""

from __future__ import annotations

import csv
import io
import json
import re
from pathlib import PurePath
from typing import Any
from urllib.parse import quote

from ..identify import EntityType, MaskStrategy
from ..identify.masking import mask_text_by_strategy
from ..identify.pipeline import DocumentReport, SegmentFinding
from ..identify.types import Hit
from ..parsers.base import Document, Segment
from ..parsers.csv_parser import read_csv_rows

# 导出格式扩展名映射（content_type → (扩展名, media_type)）
_FORMAT_MAP: dict[str, tuple[str, str]] = {
    "text": (".txt", "text/plain; charset=utf-8"),
    "json": (".json", "application/json; charset=utf-8"),
    "csv": (".csv", "text/csv; charset=utf-8"),
    "sql": (".sql", "application/sql; charset=utf-8"),
    "docx": (".txt", "text/plain; charset=utf-8"),
    "pdf": (".txt", "text/plain; charset=utf-8"),
}

# 纯文本类格式允许保留的原扩展名（避免 .log/.md 被改成 .txt）
_TEXT_KEEP_SUFFIXES = {".log", ".md"}

# 文件名安全化：非 [字母数字._-] 替换为 _，截断到 80 字符
_SAFE_NAME_RE = re.compile(r"[^\w.\-]")


def _safe_stem(original_name: str) -> str:
    """从原文件名取 stem 并做安全化；空时返回 flowmask-masked。"""
    stem = PurePath(original_name).stem or ""
    cleaned = _SAFE_NAME_RE.sub("_", stem)
    cleaned = cleaned.strip("._-")
    return cleaned[:80] if cleaned else "flowmask-masked"


def _build_filename(original_name: str, ext: str) -> tuple[str, str, str]:
    """返回 (最终文件名, ASCII 回退名, RFC 5987 编码名)。"""
    stem = _safe_stem(original_name)
    filename = f"{stem}-masked{ext}"
    try:
        ascii_name = filename.encode("ascii", "ignore").decode() or f"flowmask-masked{ext}"
    except UnicodeEncodeError:
        ascii_name = f"flowmask-masked{ext}"
    quoted = quote(filename, safe="")
    return filename, ascii_name, quoted


def _content_disposition(filename: str, ascii_name: str, quoted: str) -> str:
    """同时给 ASCII 回退和 RFC 5987，兼容老浏览器与中文文件名。"""
    return f'attachment; filename="{ascii_name}"; filename*=UTF-8\'\'{quoted}'


def _spans_to_hits(segment: Segment, finding: SegmentFinding) -> list[Hit]:
    """从 SegmentFinding.spans 重建 Hit 列表（用于按策略重新脱敏）。

    spans 的 start/end 是 segment.text 的字符偏移（已 rebase 回原文），
    切片即可拿到敏感值；type/rule 从 span 字段读。
    """
    hits: list[Hit] = []
    for span in finding.spans:
        start = int(span["start"])
        end = int(span["end"])
        if start < 0 or end > len(segment.text) or start >= end:
            continue
        try:
            entity = EntityType(str(span["type"]))
        except ValueError:
            continue
        value = segment.text[start:end]
        hits.append(
            Hit(
                type=entity,
                start=start,
                end=end,
                value=value,
                weight=0.9,
                confidence=0.95,
                rule=str(span["rule"]),
            )
        )
    return hits


def _masked_by_path(report: DocumentReport) -> dict[str, str]:
    """建立 path → masked 副本的索引（仅命中片段）。"""
    return {f.path: f.masked for f in report.findings}


# ---------- 各 content_type 重建 ----------


def _build_text(document: Document, report: DocumentReport, strategy: MaskStrategy, encrypt_key: bytes | None) -> str:
    """纯文本：按 segment 顺序拼接，命中段按策略脱敏。"""
    if not document.segments:
        return document.raw
    # 纯文本通常只有一个 path="$" 的 segment；多段时按顺序拼
    findings_by_path = {f.path: f for f in report.findings}
    parts: list[str] = []
    for seg in document.segments:
        finding = findings_by_path.get(seg.path)
        if finding is None or not finding.spans:
            parts.append(seg.text)
            continue
        hits = _spans_to_hits(seg, finding)
        parts.append(mask_text_by_strategy(seg.text, hits, strategy, encrypt_key=encrypt_key))
    return "".join(parts)


def _build_json(document: Document, report: DocumentReport, strategy: MaskStrategy, encrypt_key: bytes | None) -> str:
    """JSON：按与 json_parser._walk 完全相同的路径规则递归替换字符串节点。"""
    data = json.loads(document.raw)
    findings_by_path = {f.path: f for f in report.findings}

    def walk(node: Any, path: str) -> Any:
        if isinstance(node, str):
            finding = findings_by_path.get(path)
            if finding is None or not finding.spans:
                return node
            # 构造一个临时 segment 用于偏移对齐
            seg = Segment(path=path, text=node)
            hits = _spans_to_hits(seg, finding)
            return mask_text_by_strategy(node, hits, strategy, encrypt_key=encrypt_key)
        if isinstance(node, dict):
            return {k: walk(v, path + "." + str(k)) for k, v in node.items()}
        if isinstance(node, list):
            return [walk(v, f"{path}[{i}]") for i, v in enumerate(node)]
        return node

    masked = walk(data, "$")
    return json.dumps(masked, ensure_ascii=False, indent=2)


def _build_csv(document: Document, report: DocumentReport, strategy: MaskStrategy, encrypt_key: bytes | None) -> str:
    """CSV：复用 read_csv_rows + parse_csv 的行过滤/列名规则，重建为 utf-8-sig。"""
    rows = [row for row in read_csv_rows(document.raw) if any(cell.strip() for cell in row)]
    if not rows:
        return document.raw
    header, data_rows = rows[0], rows[1:]
    columns = [
        (header[i].strip() if i < len(header) and header[i].strip() else f"col{i + 1}")
        for i in range(max(len(row) for row in rows))
    ]
    findings_by_path = {f.path: f for f in report.findings}

    out_rows: list[list[str]] = [header]
    for row_index, row in enumerate(data_rows, start=1):
        out_row: list[str] = []
        for col_index, cell in enumerate(row):
            column = columns[col_index] if col_index < len(columns) else f"col{col_index + 1}"
            path = f"row{row_index}.{column}"
            finding = findings_by_path.get(path)
            if finding is None or not finding.spans or not cell.strip():
                out_row.append(cell)
                continue
            seg = Segment(path=path, text=cell)
            hits = _spans_to_hits(seg, finding)
            out_row.append(mask_text_by_strategy(cell, hits, strategy, encrypt_key=encrypt_key))
        out_rows.append(out_row)

    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerows(out_rows)
    return buf.getvalue()


def _build_sql(document: Document, report: DocumentReport, strategy: MaskStrategy, encrypt_key: bytes | None) -> str:
    """SQL：按 segment.offset 从大到小倒序替换字面量（长度变化不影响前面的偏移）。"""
    text = document.raw
    # 只处理 offset >= 0 的 segment（DDL 已被解析层跳过）
    segs = [seg for seg in document.segments if seg.offset >= 0]
    findings_by_path = {f.path: f for f in report.findings}
    # 倒序：offset 从大到小
    segs.sort(key=lambda s: s.offset, reverse=True)
    for seg in segs:
        finding = findings_by_path.get(seg.path)
        if finding is None or not finding.spans:
            continue
        hits = _spans_to_hits(seg, finding)
        masked = mask_text_by_strategy(seg.text, hits, strategy, encrypt_key=encrypt_key)
        off = seg.offset
        text = text[:off] + masked + text[off + len(seg.text):]
    return text


def _build_docx_pdf(document: Document, report: DocumentReport, strategy: MaskStrategy, encrypt_key: bytes | None) -> str:
    """docx/pdf：按 segment 顺序拼接，命中段按策略脱敏，输出纯文本。"""
    findings_by_path = {f.path: f for f in report.findings}
    parts: list[str] = []
    for seg in document.segments:
        finding = findings_by_path.get(seg.path)
        if finding is None or not finding.spans:
            parts.append(seg.text)
            continue
        hits = _spans_to_hits(seg, finding)
        parts.append(mask_text_by_strategy(seg.text, hits, strategy, encrypt_key=encrypt_key))
    return "\n".join(parts)


# ---------- 主入口 ----------


def build_masked_file(
    document: Document,
    report: DocumentReport,
    original_name: str,
    strategy: MaskStrategy = MaskStrategy.PARTIAL,
    fmt: str | None = None,
    encrypt_key: bytes | None = None,
) -> tuple[bytes, str, str, str | None]:
    """按策略重建完整脱敏文件。

    Args:
        document: 原始 Document（含 segments 与 raw）
        report: 检测报告（含 findings 与 spans）
        original_name: 原文件名（用于生成下载文件名）
        strategy: 脱敏策略（block/partial/hash/generalize/pseudonym/encrypt）
        fmt: 强制输出格式；None 时按 document.content_type 决定
        encrypt_key: 加密密钥；encrypt 策略且为 None 时自动生成

    Returns:
        (文件内容bytes, media_type, Content-Disposition 头, 加密密钥base64或None)
    """
    # encrypt 策略需要密钥：为 None 时生成（整个文件共用一把）
    encrypt_key_b64: str | None = None
    if strategy == MaskStrategy.ENCRYPT and encrypt_key is None:
        from ..identify.masking import _generate_encrypt_key
        encrypt_key = _generate_encrypt_key()
    if encrypt_key is not None:
        import base64
        encrypt_key_b64 = encrypt_key.decode("ascii") if isinstance(encrypt_key, bytes) else str(encrypt_key)

    # 决定 content_type（fmt 优先，否则用 document.content_type）
    content_type = fmt or document.content_type
    if content_type not in _FORMAT_MAP:
        raise ValueError(f"不支持的导出格式: {content_type}")

    ext, media_type = _FORMAT_MAP[content_type]

    # 纯文本类：原文件是 .log/.md 时保留原扩展名
    if content_type == "text":
        orig_suffix = PurePath(original_name).suffix.lower()
        if orig_suffix in _TEXT_KEEP_SUFFIXES:
            ext = orig_suffix

    # 按策略重建
    if content_type == "text":
        text = _build_text(document, report, strategy, encrypt_key)
    elif content_type == "json":
        text = _build_json(document, report, strategy, encrypt_key)
    elif content_type == "csv":
        text = _build_csv(document, report, strategy, encrypt_key)
    elif content_type == "sql":
        text = _build_sql(document, report, strategy, encrypt_key)
    elif content_type in ("docx", "pdf"):
        text = _build_docx_pdf(document, report, strategy, encrypt_key)
    else:
        # 理论不可达：上面已校验 _FORMAT_MAP
        raise ValueError(f"未实现的导出格式: {content_type}")

    # 编码：CSV 用 utf-8-sig（Excel 打开中文不乱码），其余 utf-8
    if content_type == "csv":
        data = text.encode("utf-8-sig")
    else:
        data = text.encode("utf-8")

    filename, ascii_name, quoted = _build_filename(original_name, ext)
    disposition = _content_disposition(filename, ascii_name, quoted)

    return data, media_type, disposition, encrypt_key_b64


def build_masked_preview(document: Document, report: DocumentReport, strategy) -> str:
    """按策略生成脱敏文本预览（不下载文件），用于左栏实时展示导出策略效果。

    与 build_masked_file 的区别：只返回纯文本字符串，不打包文件、不做加密、不返回 headers。
    """
    strategy = MaskStrategy.parse(strategy) if isinstance(strategy, str) else strategy
    content_type = document.content_type
    if content_type not in _FORMAT_MAP:
        # 未知格式回退纯文本
        return _build_text(document, report, strategy, None)
    if content_type == "text":
        return _build_text(document, report, strategy, None)
    if content_type == "json":
        return _build_json(document, report, strategy, None)
    if content_type == "csv":
        return _build_csv(document, report, strategy, None)
    if content_type == "sql":
        return _build_sql(document, report, strategy, None)
    if content_type in ("docx", "pdf"):
        return _build_docx_pdf(document, report, strategy, None)
    return _build_text(document, report, strategy, None)
