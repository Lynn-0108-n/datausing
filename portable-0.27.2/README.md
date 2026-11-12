# FlowMask 0.27.2 便携全功能版 · 源码归档

本目录从 `FlowMask-0.27.2-全功能版` 工作区整理而来，用于在 GitHub 上留存本次
「文档脱敏前后对比」功能，以及随后的应用图标、启动脚本修复的源码。

> 这是一个**独立分支上的源码快照**，未与 `main` 的 `web/` 合并：`main` 已有一套
> 0.27.2 前端实现（`web/src/components/DocumentComparison.vue`），两套前端谱系不同，
> 直接合并会造成互相覆盖，因此单独保留本分支便于对照与回溯。

## 包含内容

| 路径 | 说明 |
| --- | --- |
| `web/src/` | Vue3 + Element Plus 前端源码：新增 `components/DocumentCompareDialog.vue`、`api.js` 的对比接口、`views/DetectView.vue` 批量结果「对比」入口 |
| `web/public/` | 应用图标、品牌图、favicon、`site.webmanifest`（由 `流动盾牌隐私图标.png` 规范化为 squircle 图标） |
| `web/tests/comparison/` | API / UI / 长文档自动化脚本（`e2e_api.py`、`ui_smoke.py`、`ui_longdoc.py`、`make_samples.py`）；测试样本与截图可用 `make_samples.py` 重新生成，不随仓库提交 |
| `scripts/full-start.ps1`、`scripts/full-stop.ps1` | 修复后的便携启动脚本：启动前校验 `web/dist/index.html`、`/app/` 严格 200 校验、检测并自愈「主程序已运行但未挂载前端」的 404 状态 |
| `server/tests/` | 对比接口后端 pytest（单元 + 集成，共 26 例） |
| `docs/` | 《对比功能交付说明》与独立 QA《对比功能验收报告》 |

## 不包含

- `web/dist` 构建产物、`node_modules`
- `runtime/`、`models/`、`data/`、数据库与日志（便携包运行时内容）
- 大体积测试样本与截图（`web/tests/comparison/samples/`、`shots/`），运行 `make_samples.py` 可重新生成

## 构建与运行

```powershell
cd web
npm install
npm run build        # 产物写入 web/dist
cd ..
powershell -ExecutionPolicy Bypass -File scripts\full-start.ps1
```

默认主程序端口 8095，前端地址 `http://127.0.0.1:8095/app/`。

## 数据与安全要点

- 对比接口 `GET /api/v1/tasks/{id}/comparison` 与下载接口均由服务端按
  「浏览器会话 Cookie + export_secret」校验快照归属，未授权返回 403；
  快照只存内存、TTL 约 5 分钟，过期返回 410。
- 前端只渲染服务端返回的 `original` / `masked` / `changes`，不做字符串模糊匹配，
  不持久化原文到 localStorage / 日志。
- 对比视图的「下载脱敏文档」复用同一快照重建，内容与界面逐字一致。
