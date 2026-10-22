"""插件注册表（N4）：内置引擎/算子/解析器登记、第三方加载与只读接口。"""

from __future__ import annotations

import textwrap

import pytest

from app.parsers import registry as parsers
from app.parsers.base import Document
from app.plugins import (
    PLUGIN_KINDS,
    PluginRegistry,
    PluginSpec,
    register_builtin_plugins,
)


def _builtin_registry() -> PluginRegistry:
    registry = PluginRegistry()
    register_builtin_plugins(registry)
    return registry


def _write_plugin(tmp_path, name: str, body: str):
    """在临时目录里写一个插件模块，并把它加入 sys.path。"""
    path = tmp_path / f"{name}.py"
    path.write_text(textwrap.dedent(body), encoding="utf-8")
    return path


def test_builtin_engines_registered() -> None:
    registry = _builtin_registry()
    names = registry.names("engine")
    assert names == ["l1-rules", "l2-semantic", "l3-guard", "ner"]
    l1 = registry.require("engine", "l1-rules")
    assert l1.builtin is True
    assert l1.is_available() is True
    # L1 引擎工厂不依赖配置，可直接取到评分函数
    assert callable(l1.factory())
    # L2/L3/NER 默认关闭，available 反映当前配置而不是硬编码 True
    assert registry.require("engine", "l2-semantic").available is not None


def test_builtin_operators_registered() -> None:
    registry = _builtin_registry()
    assert registry.names("operator") == ["laplace", "numeric-generalize", "l-diversity", "mask"]
    laplace = registry.require("operator", "laplace")
    assert callable(laplace.factory)
    assert laplace.to_dict()["builtin"] is True


def test_builtin_parsers_match_format_registry() -> None:
    registry = _builtin_registry()
    parser_names = registry.names("parser")
    assert parser_names == ["csv", "docx", "json", "pdf", "sql", "text"]
    supported = parsers.supported_extensions()
    for spec in registry.specs("parser"):
        extensions = list(spec.meta["extensions"])
        assert extensions, spec.name
        assert set(extensions) <= set(supported)
    # 扩展名与内容类型的映射仍与旧行为一致
    assert parsers.SUPPORTED_TEXT_EXTENSIONS[".txt"] == "text"
    assert parsers.DOCUMENT_EXTENSIONS[".pdf"] == "pdf"


def test_register_rejects_unknown_kind_and_duplicate() -> None:
    registry = PluginRegistry()
    with pytest.raises(ValueError, match="插件类型非法"):
        registry.register(PluginSpec(kind="widget", name="x", description="x"))
    registry.register(PluginSpec(kind="engine", name="demo", description="演示"))
    with pytest.raises(ValueError, match="插件已注册"):
        registry.register(PluginSpec(kind="engine", name="demo", description="重复"))
    replaced = registry.register(PluginSpec(kind="engine", name="demo", description="新描述"), replace=True)
    assert replaced.description == "新描述"
    assert registry.names("engine") == ["demo"]


def test_require_and_get() -> None:
    registry = _builtin_registry()
    assert registry.get("engine", "nope") is None
    with pytest.raises(KeyError, match="未注册"):
        registry.require("engine", "nope")
    assert registry.names() == registry.names("engine") + registry.names("operator") + registry.names("parser")


def test_unavailable_plugin_never_breaks_listing() -> None:
    def boom() -> bool:
        raise RuntimeError("模型没装")

    spec = PluginSpec(kind="engine", name="broken", description="x", available=boom)
    assert spec.is_available() is False
    assert spec.to_dict()["available"] is False


def test_successful_external_module_load(tmp_path, monkeypatch) -> None:
    _write_plugin(
        tmp_path,
        "flowmask_demo_plugin",
        """
        from app.plugins import PluginSpec


        def register(registry):
            registry.register(
                PluginSpec(kind="engine", name="demo-engine", description="演示引擎", source="flowmask_demo_plugin:register")
            )
        """,
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    registry = _builtin_registry()
    assert registry.load_modules("flowmask_demo_plugin:register") == 1
    spec = registry.require("engine", "demo-engine")
    assert spec.builtin is False
    assert spec.source == "flowmask_demo_plugin:register"
    # 同一入口重复加载不会重复执行
    assert registry.load_modules("flowmask_demo_plugin:register") == 0
    assert registry.names("engine").count("demo-engine") == 1


def test_failed_external_modules_become_warnings(tmp_path, monkeypatch) -> None:
    _write_plugin(
        tmp_path,
        "flowmask_plugin_crashing",
        """
        def register(registry):
            raise RuntimeError("插件自己有 bug")
        """,
    )
    _write_plugin(tmp_path, "flowmask_plugin_no_entry", "VALUE = 1\n")
    monkeypatch.syspath_prepend(str(tmp_path))
    registry = _builtin_registry()
    loaded = registry.load_modules(
        "flowmask_missing_module,flowmask_plugin_crashing:register,flowmask_plugin_no_entry:register"
    )
    assert loaded == 0
    assert len(registry.warnings) == 3
    assert "导入失败" in registry.warnings[0]
    assert "抛异常" in registry.warnings[1]
    assert "没有可调用的" in registry.warnings[2]
    # 警告只影响插件，内置插件照常可用
    assert registry.require("engine", "l1-rules").builtin is True


def test_parser_plugin_registers_new_extension(tmp_path) -> None:
    registry = PluginRegistry()
    spec = registry.register_parser(
        name="ini-demo",
        extensions=(".fm-ini",),
        handler=lambda filename, data: Document(source=filename, content_type="ini-demo", raw=data.decode()),
        description="演示：INI 风格配置",
        source="test",
    )
    assert spec.meta["extensions"] == [".fm-ini"]
    assert ".fm-ini" in parsers.supported_formats()["supported"]
    document = parsers.parse_bytes("demo.fm-ini", b"a=1")
    assert document.content_type == "ini-demo"
    # 已被占用的扩展名不能再登记，避免插件顶掉内置解析器
    with pytest.raises(ValueError, match="已被占用"):
        registry.register_parser(
            name="hijack-json",
            extensions=(".json",),
            handler=lambda filename, data: Document(source=filename, content_type="x"),
            description="试图顶掉 json",
        )


def test_plugins_endpoint_lists_every_kind(client) -> None:
    response = client.get("/api/v1/plugins")
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == sum(body["kinds"].values())
    assert set(body["kinds"]) == set(PLUGIN_KINDS)
    assert body["kinds"]["engine"] >= 4
    assert body["kinds"]["operator"] >= 4
    assert body["kinds"]["parser"] >= 6
    kinds = {item["kind"] for item in body["items"]}
    assert kinds == set(PLUGIN_KINDS)
    for item in body["items"]:
        assert item["name"] and item["description"]
        assert isinstance(item["available"], bool)
    assert "FLOWMASK_PLUGINS" in body["hint"]


def test_plugin_modules_setting_controls_loading(tmp_path, monkeypatch) -> None:
    from app.core.config import get_settings
    from app.plugins import load_plugins, reset_plugins_for_tests

    _write_plugin(
        tmp_path,
        "flowmask_env_plugin",
        """
        from app.plugins import PluginSpec


        def register(registry):
            registry.register(PluginSpec(kind="operator", name="env-op", description="环境变量加载"))
        """,
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setenv("FLOWMASK_PLUGINS", "flowmask_env_plugin:register")
    get_settings.cache_clear()
    reset_plugins_for_tests()
    try:
        registry = load_plugins()
        assert "env-op" in registry.names("operator")
        assert load_plugins().names("operator").count("env-op") == 1
    finally:
        reset_plugins_for_tests()
