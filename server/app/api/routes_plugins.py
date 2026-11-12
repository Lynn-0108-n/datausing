"""插件注册表只读接口（需求 N4）。

返回进程内已登记的引擎 / 算子 / 解析器，以及第三方插件的加载警告。
改插件需要改 ``FLOWMASK_PLUGINS`` 并重启——这里不提供热安装，避免演示环境被远程注入代码。
"""

from __future__ import annotations

from fastapi import APIRouter

from ..plugins import get_registry

router = APIRouter(tags=["plugins"])


@router.get("/plugins", summary="已注册的引擎 / 算子 / 解析器插件")
def list_plugins() -> dict[str, object]:
    return get_registry().summary()
