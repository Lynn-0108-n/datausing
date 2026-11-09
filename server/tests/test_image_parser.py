"""图片 OCR 解析测试（Day 3.5）。

覆盖：
- OCR 未启用 → ParseUnsupported
- OCR 启用 + rapidocr 不可用 → ParseUnsupported
- OCR 启用 + rapidocr 可用 → 成功解析（mock 引擎）
- 注册表识别 .png/.jpg/.jpeg 并分派给 parse_image
- supported_formats 含图片扩展名与 ocr 字段
"""

from __future__ import annotations

import pytest

from app.parsers.base import Document, ParseError, ParseUnsupported
from app.parsers import image_parser
from app.parsers.registry import (
    IMAGE_EXTENSIONS,
    parse_bytes,
    supported_formats,
)


def test_parse_image_disabled_raises_unsupported(monkeypatch):
    """OCR 未启用 → ParseUnsupported。"""
    from app.core.config import get_settings

    monkeypatch.setenv("FLOWMASK_OCR_ENABLED", "0")
    get_settings.cache_clear()
    try:
        with pytest.raises(ParseUnsupported) as exc_info:
            image_parser.parse_image(b"\x89PNG\r\n\x1a\n", source="x.png")
        assert "OCR 未启用" in str(exc_info.value)
    finally:
        get_settings.cache_clear()


def test_parse_image_enabled_but_no_engine_raises_unsupported(monkeypatch):
    """OCR 启用但 rapidocr 未安装 → ParseUnsupported。"""
    from app.core.config import get_settings

    monkeypatch.setenv("FLOWMASK_OCR_ENABLED", "1")
    get_settings.cache_clear()
    # 模拟 rapidocr 未安装：_get_engine 返回 None
    monkeypatch.setattr(image_parser, "_engine", None)
    monkeypatch.setattr(image_parser, "_engine_loaded", True)
    try:
        with pytest.raises(ParseUnsupported) as exc_info:
            image_parser.parse_image(b"\x89PNG\r\n\x1a\n", source="x.png")
        assert "rapidocr-onnxruntime" in str(exc_info.value)
    finally:
        get_settings.cache_clear()
        # 复位懒加载状态
        monkeypatch.setattr(image_parser, "_engine_loaded", False)


def test_parse_image_success_mock_engine(monkeypatch):
    """OCR 启用 + 引擎可用（mock） → 成功解析为 text Document。"""
    from app.core.config import get_settings

    monkeypatch.setenv("FLOWMASK_OCR_ENABLED", "1")
    get_settings.cache_clear()

    # mock 一个假引擎：返回两行文字
    class FakeEngine:
        def __call__(self, data):
            return (
                [
                    [[[0, 0], [100, 0], [100, 30], [0, 30]], "身份证号 110101199003078888", 0.95],
                    [[[0, 40], [100, 40], [100, 70], [0, 70]], "手机 13800138000", 0.92],
                ],
                0.5,
            )

    monkeypatch.setattr(image_parser, "_engine", FakeEngine())
    monkeypatch.setattr(image_parser, "_engine_loaded", True)

    try:
        document = image_parser.parse_image(b"\x89PNG\r\n\x1a\nfake-image-bytes", source="x.png")
        assert isinstance(document, Document)
        assert document.content_type == "text"
        assert "110101199003078888" in document.segments[0].text
        assert "13800138000" in document.segments[0].text
        # OCR 标记
        assert document.meta.get("ocr") is True
    finally:
        get_settings.cache_clear()
        monkeypatch.setattr(image_parser, "_engine", None)
        monkeypatch.setattr(image_parser, "_engine_loaded", False)


def test_parse_image_empty_result_raises(monkeypatch):
    """OCR 引擎返回空 → ParseError。"""
    from app.core.config import get_settings

    monkeypatch.setenv("FLOWMASK_OCR_ENABLED", "1")
    get_settings.cache_clear()

    class FakeEmptyEngine:
        def __call__(self, data):
            return ([], 0.5)

    monkeypatch.setattr(image_parser, "_engine", FakeEmptyEngine())
    monkeypatch.setattr(image_parser, "_engine_loaded", True)

    try:
        with pytest.raises(ParseError) as exc_info:
            image_parser.parse_image(b"empty-image", source="x.png")
        assert "OCR" in str(exc_info.value)
    finally:
        get_settings.cache_clear()
        monkeypatch.setattr(image_parser, "_engine", None)
        monkeypatch.setattr(image_parser, "_engine_loaded", False)


def test_registry_routes_image_extensions():
    """注册表 IMAGE_EXTENSIONS 含 png/jpg/jpeg。"""
    assert ".png" in IMAGE_EXTENSIONS
    assert ".jpg" in IMAGE_EXTENSIONS
    assert ".jpeg" in IMAGE_EXTENSIONS


def test_parse_bytes_png_unsupported_when_ocr_disabled(client, monkeypatch):
    """OCR 未启用时 .png 上传 → 415。"""
    # client fixture 自带 tmp_env，OCR 默认关闭
    response = client.post(
        "/api/v1/detect/file",
        files={"file": ("x.png", b"\x89PNG\r\n\x1a\nfake", "image/png")},
    )
    assert response.status_code == 415
    assert "OCR" in response.json()["detail"] or "未启用" in response.json()["detail"]


def test_supported_formats_includes_images():
    """supported_formats() 含 .png/.jpg/.jpeg 与 ocr 字段。"""
    formats = supported_formats()
    supported = formats["supported"]
    assert ".png" in supported
    assert ".jpg" in supported
    assert ".jpeg" in supported
    assert "ocr" in formats
    assert "ocr_engine" in formats


def test_parse_bytes_unsupported_extension_lists_images():
    """不支持扩展名的提示信息里应包含 .png（让用户知道能传图片）。"""
    with pytest.raises(ParseUnsupported) as exc_info:
        parse_bytes("a.bin", b"\x00\x01")
    message = str(exc_info.value)
    # 提示文案应包含至少一种图片扩展名
    assert ".png" in message or ".jpg" in message
