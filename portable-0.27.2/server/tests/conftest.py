"""对比后端测试的公共装置。

两类测试：

* ``test_unit_*.py``：不需要启动服务、不需要任何模型（L2/L3/NER 全关时纯规则），
  毫秒级；直接 import ``app.*`` 验证偏移与安全边界。
* ``test_integration_*.py``：真实 HTTP，需要另行启动隔离服务（见 ``tests/README.md``）。
  未设置 ``FLOWMASK_TEST_BASE_URL`` 时自动 skip，不会误伤没起服务的环境。

本文件在收集测试前设置 ``FLOWMASK_DATA_DIR`` 等环境变量，确保即使被测代码意外
调用 ``get_settings()``，也不会写入 ``FlowMask\\server\\data`` 或打包数据目录。
"""

from __future__ import annotations

import atexit
import os
import shutil
import tempfile

# 必须在任何 app.* import 之前执行（pytest 会先导入 conftest）。
_TMP_DATA_DIR = tempfile.mkdtemp(prefix="flowmask-tests-")
atexit.register(shutil.rmtree, _TMP_DATA_DIR, ignore_errors=True)

os.environ.setdefault("FLOWMASK_DATA_DIR", _TMP_DATA_DIR)
os.environ.setdefault("FLOWMASK_DB_PATH", os.path.join(_TMP_DATA_DIR, "flowmask.db"))
os.environ.setdefault("FLOWMASK_API_TOKEN", "")
os.environ.setdefault("FLOWMASK_ENABLE_L2", "0")
os.environ.setdefault("FLOWMASK_ENABLE_L3", "0")
os.environ.setdefault("FLOWMASK_ENABLE_NER", "0")
os.environ.setdefault("FLOWMASK_OCR_ENABLED", "0")

import pytest  # noqa: E402  （放在环境变量之后）


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "integration: 需要真实 HTTP 服务的集成测试（FLOWMASK_TEST_BASE_URL）",
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if os.environ.get("FLOWMASK_TEST_BASE_URL"):
        return
    skip_integration = pytest.mark.skip(
        reason="未设置 FLOWMASK_TEST_BASE_URL，跳过真实 HTTP 集成测试（见 tests/README.md）"
    )
    for item in items:
        if item.get_closest_marker("integration"):
            item.add_marker(skip_integration)
