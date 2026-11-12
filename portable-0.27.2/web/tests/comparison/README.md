# 文档脱敏前后对比 — 前端验收测试

本目录是「FlowMask 文档脱敏前后对比」功能的独立自动化测试脚本，覆盖后端对比 API 与前端对比工作台 UI。测试数据全部为虚构内容。

## 目录

- `make_samples.py` — 生成测试样本（写到 `samples/`）：
  - `evidence-a.txt`：含姓名/手机/身份证/邮箱/地址，含 emoji 与缩进行、长行；
  - `evidence-b.txt`：无敏感信息（用于无差异状态）；
  - `evidence-large.txt`：约 115 万字符（> `MAX_COMPARE_CHARS`，用于 413 状态）。
- `e2e_api.py` — 纯 Python（urllib + cookie）API 端到端测试，共 28 项断言。
- `ui_smoke.py` — Playwright 浏览器 UI 冒烟测试，共 30 项断言（含下载文件与接口内容逐字一致性），截图写入 `shots/`。
- `ui_longdoc.py` — 长文档（约 40 万字符 / 8000 行）虚拟滚动、滚动、差异导航与自动换行禁用验证，共 7 项断言，截图 `shots/11-longdoc.png`。
- `_run/`、`shots/`、`samples/` 为运行产物，可随时删除重建（脚本不落盘原文/脱敏正文）。

## 前置条件

1. 已用源码构建前端：`cd FlowMask/web && node node_modules/vite/bin/vite.js build --outDir dist-next` 后改名 `dist`（详见交付说明）。
2. 一个隔离端口启动的后端服务，且 `FLOWMASK_FRONTEND_DIR` 指向本仓库 `FlowMask/web/dist`，例如：

```powershell
$env:FLOWMASK_HOST='127.0.0.1'
$env:FLOWMASK_PORT='8395'
$env:FLOWMASK_DATA_DIR='D:\flowmask-compare-test\data'
$env:FLOWMASK_DB_PATH='D:\flowmask-compare-test\flowmask.db'
$env:API_TOKEN=''
$env:FLOWMASK_ENABLE_L2='0'; $env:FLOWMASK_ENABLE_L3='0'
$env:FLOWMASK_ENABLE_NER='0'; $env:FLOWMASK_ENABLE_OCR='0'
$env:FLOWMASK_MAX_UPLOAD_MB='4'
$env:FLOWMASK_FRONTEND_DIR='D:\...\FlowMask\web\dist'
& 'D:\...\FlowMask\runtime\python.exe' -m uvicorn app.main:app --host 127.0.0.1 --port 8395
```

（`FLOWMASK_MAX_UPLOAD_MB=4` 仅用于让测试上传 3 MB 大样本；生产环境保持默认 20 MB 即可。）

3. UI 测试需要 Playwright 与本机 Chrome：

```powershell
python -m venv .venv
.venv\Scripts\pip install playwright
# 无需下载浏览器，使用 channel="chrome" 调用本机已安装的 Chrome
```

## 运行

```powershell
# 默认连接 http://127.0.0.1:8395，可用 FLOWMASK_BASE 覆盖
python make_samples.py
python e2e_api.py        # 28 passed
python ui_smoke.py       # 30 passed, 截图在 shots/
python ui_longdoc.py     # 7 passed, 长文档虚拟滚动
```

注意：`ui_smoke.py` 会在启动浏览器前把 `TMP/TEMP/TMPDIR` 指向 `_run/`，避免 Windows 权限策略导致的 `mkdtemp EPERM`。

## 预期与已知边界

- 28/28、30/30、7/7 全绿为通过标准。
- 对比功能只对「批量检测」任务开放（`POST /tasks/batch` 才会创建服务端临时快照）；单文件 `POST /tasks`、`POST /tasks/text` 的对比接口返回 `410 SNAPSHOT_EXPIRED` 属于预期行为，前端显示「对比快照已过期」引导重新检测。
- 快照 TTL 固定 300 秒且进程重启失效，`/comparison` 与 `/comparison/download` 都不允许缓存（`Cache-Control: no-store`），原始正文只存在服务端内存，测试脚本不会将原文写入磁盘或日志。



