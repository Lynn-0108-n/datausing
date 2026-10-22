"""统一插件注册表：引擎 / 算子 / 解析器。

三类扩展点的口径：

- **引擎（engine）**：识别链路上可替换的一级能力——L1 规则、L2 语义、L3 护栏、NER；
- **算子（operator）**：隐私增强与脱敏动作——Laplace 扰动、数值泛化、l-多样性校验、四级脱敏；
- **解析器（parser）**：输入格式——text/json/csv/sql/docx/pdf，可运行时追加新扩展名。

内置实现全部登记在册；第三方插件通过 ``FLOWMASK_PLUGINS=module:register``（逗号分隔）
指向一个 ``register(registry)`` 函数即可接入。加载失败只记警告、不影响主流程——
插件是"可扩展"，不是"可把服务拖垮"。
"""

from __future__ import annotations

import importlib
from dataclasses import dataclass, field
from typing import Any, Callable

from .core.config import Settings, get_settings

PLUGIN_KINDS = ("engine", "operator", "parser")

#: 工厂/处理函数契约：由使用方按 kind 调用，注册表只负责登记与描述。
PluginFactory = Callable[..., Any]

_HINT = (
    "第三方插件：设置 FLOWMASK_PLUGINS=module:register（逗号分隔）后重启，"
    "register(registry) 里调用 registry.register(...) 或 registry.register_parser(...) 登记。"
)


@dataclass(slots=True)
class PluginSpec:
    """一个可替换扩展点的登记信息。"""

    kind: str
    name: str
    description: str
    factory: PluginFactory | None = None
    # 运行时就绪判定（如 L2 是否启用）；异常一律视为不可用，不让插件拖垮 /plugins
    available: Callable[[], bool] | None = None
    builtin: bool = False
    source: str = "builtin"
    meta: dict[str, Any] = field(default_factory=dict)

    def is_available(self) -> bool:
        if self.available is None:
            return True
        try:
            return bool(self.available())
        except Exception:
            return False

    def to_dict(self) -> dict[str, Any]:
        item: dict[str, Any] = {
            "kind": self.kind,
            "name": self.name,
            "description": self.description,
            "builtin": self.builtin,
            "source": self.source,
            "available": self.is_available(),
        }
        if self.meta:
            item["meta"] = dict(self.meta)
        return item


class PluginRegistry:
    """按 kind 分组的插件登记表；注册是显式的，查询是只读的。"""

    def __init__(self) -> None:
        self._items: dict[str, dict[str, PluginSpec]] = {kind: {} for kind in PLUGIN_KINDS}
        self.warnings: list[str] = []
        self._loaded: set[str] = set()

    # ---- 登记 -----------------------------------------------------------
    def register(self, spec: PluginSpec, *, replace: bool = False) -> PluginSpec:
        """登记一个插件；同名重复登记需显式 ``replace=True``。"""
        if spec.kind not in PLUGIN_KINDS:
            raise ValueError(f"插件类型非法：{spec.kind!r}（可选 {', '.join(PLUGIN_KINDS)}）")
        if not spec.name:
            raise ValueError("插件名不能为空")
        table = self._items[spec.kind]
        if spec.name in table and not replace:
            raise ValueError(f"插件已注册：{spec.kind}/{spec.name}")
        table[spec.name] = spec
        return spec

    def register_parser(
        self,
        *,
        name: str,
        extensions: str | tuple[str, ...] | list[str],
        handler: Callable[[str, bytes], Any],
        description: str,
        binary: bool = False,
        replace: bool = False,
        source: str = "builtin",
        builtin: bool = False,
    ) -> PluginSpec:
        """登记解析器插件：同时把扩展名接进 ``parsers.registry`` 的分派表。"""
        from .parsers.registry import register_format

        suffixes = register_format(extensions, handler, content_type=name, binary=binary, replace=replace)
        spec = PluginSpec(
            kind="parser",
            name=name,
            description=description,
            factory=handler,
            builtin=builtin,
            source=source,
            meta={"extensions": list(suffixes), "binary": binary},
        )
        return self.register(spec, replace=replace)

    # ---- 查询 -----------------------------------------------------------
    def get(self, kind: str, name: str) -> PluginSpec | None:
        return self._items.get(kind, {}).get(name)

    def require(self, kind: str, name: str) -> PluginSpec:
        spec = self.get(kind, name)
        if spec is None:
            raise KeyError(f"插件未注册：{kind}/{name}")
        return spec

    def specs(self, kind: str | None = None) -> list[PluginSpec]:
        """按 kind（缺省全部）列出插件，顺序为登记顺序。"""
        if kind is not None:
            return list(self._items.get(kind, {}).values())
        return [spec for current in PLUGIN_KINDS for spec in self._items[current].values()]

    def names(self, kind: str | None = None) -> list[str]:
        return [spec.name for spec in self.specs(kind)]

    def summary(self) -> dict[str, Any]:
        """``GET /plugins`` 的响应体：数量、分组计数、明细与加载警告。"""
        items = [spec.to_dict() for spec in self.specs()]
        return {
            "total": len(items),
            "kinds": {kind: len(self._items[kind]) for kind in PLUGIN_KINDS},
            "items": items,
            "warnings": list(self.warnings),
            "hint": _HINT,
        }

    # ---- 第三方加载 -----------------------------------------------------
    def load_modules(self, modules: str) -> int:
        """按 ``module:register`` 列表加载第三方插件，返回成功加载的模块数。

        单个模块导入失败、入口函数缺失或抛异常都只记一条警告，继续加载其余模块；
        同一入口重复调用不会重复执行 register()。
        """
        loaded = 0
        for entry in (item.strip() for item in modules.split(",")):
            if not entry or entry in self._loaded:
                continue
            module_name, _, attr = entry.partition(":")
            attr = attr.strip() or "register"
            try:
                module = importlib.import_module(module_name)
            except Exception as exc:  # 模块不存在/导入期报错：记警告，不影响启动
                self.warnings.append(f"{entry}：导入失败（{type(exc).__name__}: {exc}）")
                self._loaded.add(entry)
                continue
            register = getattr(module, attr, None)
            if not callable(register):
                self.warnings.append(f"{entry}：模块里没有可调用的 {attr}()")
                self._loaded.add(entry)
                continue
            try:
                register(self)
            except Exception as exc:  # 插件自己的 bug 不能把服务带崩
                self.warnings.append(f"{entry}：register() 抛异常（{type(exc).__name__}: {exc}）")
                self._loaded.add(entry)
                continue
            self._loaded.add(entry)
            loaded += 1
        return loaded


_PARSER_DESCRIPTIONS = {
    "text": "纯文本/日志：整段检测，命中区间按偏移回写原文",
    "json": "JSON：递归展平成字段路径，键名作为语义上下文",
    "csv": "CSV/TSV：按单元格检测，表头作为上下文",
    "sql": "SQL：INSERT/UPDATE 字面量检测",
    "docx": "Word（zip+XML 自研解析，免 python-docx）",
    "pdf": "PDF（可选依赖 pdfplumber，缺依赖时 /formats 提示）",
}


def register_builtin_plugins(registry: PluginRegistry) -> None:
    """登记内置引擎 / 算子 / 解析器（幂等：重复调用会因重名抛错，调用方保证一次）。"""
    from .identify import l2, l3, ner, operators, scoring
    from .identify.masking import mask_value
    from .parsers import registry as parsers

    def engine(
        name: str,
        description: str,
        factory: PluginFactory | None,
        available: Callable[[], bool] | None = None,
    ) -> None:
        registry.register(
            PluginSpec(
                kind="engine",
                name=name,
                description=description,
                factory=factory,
                available=available,
                builtin=True,
            )
        )

    engine(
        "l1-rules",
        "L1 正则 + 敏感词表：离线、毫秒级，所有检测的必经层",
        lambda settings=None: scoring.analyze,
    )
    engine(
        "l2-semantic",
        "L2 语义分类：HTTP 契约调用独立推理服务（onnx/mock 后端），失败自动降级",
        l2.build_semantic_classifier,
        lambda: get_settings().enable_l2,
    )
    engine(
        "l3-guard",
        "L3 大模型护栏：只复核灰区样本，给出解释与可审计证据",
        l3.build_guard_classifier,
        lambda: get_settings().enable_l3,
    )
    engine(
        "ner",
        "L1.5 NER：补没有字段名的人名/地址，只补类别不抬级别",
        ner.build_ner_detector,
        lambda: get_settings().enable_ner,
    )

    def operator(name: str, description: str, factory: PluginFactory) -> None:
        registry.register(
            PluginSpec(kind="operator", name=name, description=description, factory=factory, builtin=True)
        )

    operator("laplace", "Laplace 差分隐私扰动：数值字段加噪（epsilon 越小噪声越大）", operators.perturb_numeric)
    operator("numeric-generalize", "数值泛化：把具体数值落到固定宽度的区间", operators.generalize_numeric)
    operator("l-diversity", "l-多样性校验：准标识符等价类里的敏感值多样性 ≥ l", operators.check_l_diversity)
    operator("mask", "四级脱敏：L3 整段屏蔽 / L2 首字符+星号 / L1 前缀警示", mask_value)

    for spec in parsers.format_specs():
        name = str(spec["name"])
        extensions = list(spec["extensions"])  # type: ignore[arg-type]
        registry.register(
            PluginSpec(
                kind="parser",
                name=name,
                description=_PARSER_DESCRIPTIONS.get(name, f"解析器：{name}"),
                factory=parsers.parse_bytes,
                builtin=True,
                meta={"extensions": extensions, "binary": bool(spec["binary"])},
            )
        )


_registry = PluginRegistry()
_builtin_loaded = False


def get_registry() -> PluginRegistry:
    """进程内单例（内置插件懒加载一次）。"""
    global _builtin_loaded
    if not _builtin_loaded:
        register_builtin_plugins(_registry)
        _builtin_loaded = True
    return _registry


def load_plugins(settings: Settings | None = None) -> PluginRegistry:
    """启动时调用：加载内置插件，再按 ``FLOWMASK_PLUGINS`` 加载第三方插件。"""
    registry = get_registry()
    registry.load_modules((settings or get_settings()).plugin_modules)
    return registry


def reset_plugins_for_tests() -> None:
    """测试用：清空单例，重新登记内置插件。"""
    global _registry, _builtin_loaded
    _registry = PluginRegistry()
    _builtin_loaded = False
