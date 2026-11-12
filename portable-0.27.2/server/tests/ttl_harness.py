"""测试专用启动器：把内存检测快照的 TTL 缩短，用来端到端验证过期 410。

生产代码里 ``SnapshotStore`` 的 TTL 固定为 300 秒（``snapshots.py`` 默认参数），
没有环境变量可以改；为了几秒内验证同一条 ``_get`` 过期代码路径，本脚本在
``import app.main`` 之前把模块级 ``_store`` 换成 ``ttl=2s`` 的实例（仅测试用，
不修改 app 业务代码）。

用法（在 ``FlowMask\\server`` 目录下执行）：

    $env:FLOWMASK_DATA_DIR = 'D:\\...\\_tmp_backend_verify\\ttl-data'
    $env:FLOWMASK_DB_PATH  = 'D:\\...\\_tmp_backend_verify\\ttl-data\\flowmask.db'
    $env:FLOWMASK_ENABLE_L2 = '0'; $env:FLOWMASK_ENABLE_L3 = '0'
    $env:FLOWMASK_ENABLE_NER = '0'; $env:FLOWMASK_OCR_ENABLED = '0'
    $env:FLOWMASK_MAX_UPLOAD_MB = '2'
    $env:FLOWMASK_PORT = '8396'
    $env:FLOWMASK_TEST_SNAPSHOT_TTL = '2'
    ..\\runtime\\python.exe tests\\ttl_harness.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

SERVER_DIR = Path(__file__).resolve().parents[1]
if str(SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(SERVER_DIR))

import app.identify.snapshots as snapshots  # noqa: E402

_TTL = int(os.environ.get("FLOWMASK_TEST_SNAPSHOT_TTL", "2"))
snapshots._store = snapshots.SnapshotStore(ttl=_TTL)  # noqa: SLF001  测试专用替换

from app.main import app  # noqa: E402  （必须在替换 _store 之后导入）

if __name__ == "__main__":
    import uvicorn

    host = os.environ.get("FLOWMASK_HOST", "127.0.0.1")
    port = int(os.environ.get("FLOWMASK_PORT", "8396"))
    uvicorn.run(app, host=host, port=port, log_level="info", access_log=True)
