"""图片 OCR 解析（第一次优化）：可选依赖探测、缩放、排序与 registry 分派。

测试用纯标准库构造 PNG 字节（zlib + struct），不依赖 Pillow 生成样本；
OCR 引擎一律用假实现替换，不加载 rapidocr、不访问网络。
"""

from __future__ import annotations

import struct
import zlib

import pytest

from app.parsers import image_parser, registry
from app.parsers.base import ParseError


def make_png(width: int, height: int, color: tuple[int, int, int] = (255, 255, 255)) -> bytes:
    """用标准库拼一个 RGB PNG（合法最小结构，Pillow 可正常解码）。"""

    def chunk(tag: bytes, data: bytes) -> bytes:
        crc = zlib.crc32(tag + data) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", crc)

    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    raw = b"".join(b"\x00" + bytes(color) * width for _ in range(height))
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


def fake_engine(received: list[bytes]):
    """构造一个假 OCR 引擎类：两行文本故意乱序，用于验证 top→bottom 排序。"""

    class _FakeOCR:
        def __call__(self, data: bytes):
            received.append(data)
            return (
                [
                    [[[0, 100], [50, 100], [50, 120], [0, 120]], "底部文字", 0.97],
                    [[[0, 20], [50, 20], [50, 40], [0, 40]], "第一行文字", 0.99],
                ],
                0.01,
            )

    return _FakeOCR


def empty_engine():
    class _EmptyOCR:
        def __call__(self, data: bytes):
            return (None, 0.0)

    return _EmptyOCR


@pytest.fixture()
def ocr_env(tmp_env, monkeypatch):
    """开启 OCR 配置并重置 settings 缓存（引擎由各用例自己 monkeypatch）。"""
    from app.core.config import get_settings

    monkeypatch.setenv("FLOWMASK_OCR_ENABLED", "1")
    get_settings.cache_clear()
    yield


# ---------------------------------------------------------------------------
# 依赖探测与错误文案
# ---------------------------------------------------------------------------


def test_parse_image_requires_ocr_enabled(tmp_env) -> None:
    """默认关闭 OCR：即使装了依赖也要抛固定中文提示。"""
    with pytest.raises(ParseError, match="未启用 OCR"):
        image_parser.parse_image(make_png(4, 2), name="scan.png")


def test_parse_image_requires_dependency_when_enabled(ocr_env, monkeypatch) -> None:
    """开启 OCR 但 rapidocr 不可导入：同样给出安装提示，不抛底层 ImportError。"""
    monkeypatch.setattr(image_parser, "_load_rapidocr", lambda: None)
    with pytest.raises(ParseError, match="未启用 OCR"):
        image_parser.parse_image(make_png(4, 2), name="scan.png")


def test_parse_image_invalid_bytes_reports_decode_error(ocr_env, monkeypatch) -> None:
    monkeypatch.setattr(image_parser, "_load_rapidocr", lambda: fake_engine([]))
    with pytest.raises(ParseError, match="图片解码"):
        image_parser.parse_image(b"not an image", name="broken.png")


# ---------------------------------------------------------------------------
# 正常解析：排序、hint、缩放
# ---------------------------------------------------------------------------


def test_parse_image_extracts_text_sorted_top_to_bottom(ocr_env, monkeypatch) -> None:
    received: list[bytes] = []
    monkeypatch.setattr(image_parser, "_load_rapidocr", lambda: fake_engine(received))
    document = image_parser.parse_image(make_png(40, 20), name="scan.png")

    assert document.content_type == "image"
    assert document.source == "scan.png"
    assert document.raw == "第一行文字\n底部文字"
    assert len(document.segments) == 1
    segment = document.segments[0]
    assert segment.path == "image.ocr"
    # 返回顺序是先底部后顶部，排序后应为从上到下
    assert segment.text == "第一行文字\n底部文字"
    assert "colorspace=RGB" in segment.hint
    assert "size=40x20" in segment.hint
    assert document.meta["engine"] == "rapidocr"
    assert document.meta["boxes"] == 2
    # 传给引擎的是重新编码后的 PNG 字节（不是原始上传内容）
    assert len(received) == 1
    assert received[0].startswith(b"\x89PNG")


def test_parse_image_downscales_by_max_side(ocr_env, monkeypatch) -> None:
    from app.core.config import get_settings

    monkeypatch.setenv("FLOWMASK_OCR_MAX_SIDE", "10")
    get_settings.cache_clear()
    received: list[bytes] = []
    monkeypatch.setattr(image_parser, "_load_rapidocr", lambda: fake_engine(received))
    document = image_parser.parse_image(make_png(40, 20), name="big.png")
    assert document.meta["size"] == [40, 20]  # meta 记录原尺寸，便于排查
    assert document.meta["scale"] == 0.25
    assert "scale=0.25" in document.segments[0].hint


def test_parse_image_without_text_returns_empty_document(ocr_env, monkeypatch) -> None:
    """识别不出文字不算错误：返回空文本，hint 里记录原因，供前端提示重拍。"""
    monkeypatch.setattr(image_parser, "_load_rapidocr", lambda: empty_engine())
    document = image_parser.parse_image(make_png(8, 8), name="blank.png")
    assert document.segments[0].text == ""
    assert document.raw == ""
    assert "未识别到文字" in document.segments[0].hint
    assert document.meta["boxes"] == 0


# ---------------------------------------------------------------------------
# registry：图片分派与 /formats 状态（原格式行为不变）
# ---------------------------------------------------------------------------


def test_supported_formats_keeps_ten_extensions_without_ocr(monkeypatch) -> None:
    """OCR 依赖不可用时：supported 必须包含全部文本/文档扩展名（现 10 个）。

    supported_formats() 不读 get_settings()，这里直接用假 zero 引擎探测结果验证。
    """
    monkeypatch.setattr(registry, "ocr_engine", lambda: None)
    formats = registry.supported_formats()
    assert set(formats["supported"]) == {
        ".txt",
        ".log",
        ".md",
        ".json",
        ".csv",
        ".tsv",
        ".sql",
        ".docx",
        ".xlsx",
        ".pdf",
    }
    assert formats["image"] is False
    assert formats["ocr_engine"] is None
    assert "requirements-ocr.txt" in formats["ocr_hint"]
    # 图片扩展名在 OCR 就绪前不出现在 /formats 的 supported 列表里
    for suffix in (".png", ".jpg", ".jpeg", ".bmp", ".webp"):
        assert suffix not in formats["supported"]


def test_supported_formats_lists_images_when_ocr_available(monkeypatch) -> None:
    monkeypatch.setenv("FLOWMASK_OCR_ENABLED", "1")
    monkeypatch.setattr(registry, "ocr_engine", lambda: "rapidocr")
    formats = registry.supported_formats()
    assert formats["image"] is True
    assert formats["ocr_engine"] == "rapidocr"
    assert formats["ocr_hint"] == ""
    assert formats["docx"] is True
    assert ".pdf" in formats["supported"]
    for suffix in (".png", ".jpg", ".jpeg", ".bmp", ".webp"):
        assert suffix in formats["supported"]


def test_supported_formats_hides_images_until_enabled(monkeypatch) -> None:
    """依赖已装但开关没开：仍按未启用处理，并提示去设置 FLOWMASK_OCR_ENABLED=1。"""
    monkeypatch.delenv("FLOWMASK_OCR_ENABLED", raising=False)
    monkeypatch.setattr(registry, "ocr_engine", lambda: "rapidocr")
    formats = registry.supported_formats()
    assert formats["image"] is False
    assert formats["ocr_engine"] == "rapidocr"
    assert "FLOWMASK_OCR_ENABLED" in formats["ocr_hint"]
    assert ".png" not in formats["supported"]


def test_image_formats_not_listed_as_text_parsers() -> None:
    """插件页的解析器清单保持内置解析器：图片是可选能力，不混进解析器列表。"""
    names = [str(spec["name"]) for spec in registry.format_specs()]
    assert names == ["csv", "docx", "json", "pdf", "sql", "text", "xlsx"]


def test_registry_dispatches_image_extensions_to_ocr(tmp_env) -> None:
    """图片扩展名已接进分派表：OCR 关闭时给出安装提示而不是"暂不支持"。"""
    for suffix in (".png", ".jpg", ".jpeg", ".bmp", ".webp"):
        with pytest.raises(ParseError, match="未启用 OCR"):
            registry.parse_bytes(f"scan{suffix}", make_png(2, 2))


def test_registry_parses_image_when_ocr_enabled(ocr_env, monkeypatch) -> None:
    monkeypatch.setattr(image_parser, "_load_rapidocr", lambda: fake_engine([]))
    document = registry.parse_bytes("scan.png", make_png(40, 20))
    assert document.content_type == "image"
    assert document.source == "scan.png"
    assert document.segments[0].text == "第一行文字\n底部文字"


def test_existing_text_format_behavior_unchanged() -> None:
    document = registry.parse_bytes("note.txt", "手机号 13800138000".encode("utf-8"))
    assert document.content_type == "text"
    assert "13800138000" in document.raw
    json_document = registry.parse_bytes("data.json", b'{"phone": "13800138000"}')
    assert json_document.content_type == "json"
