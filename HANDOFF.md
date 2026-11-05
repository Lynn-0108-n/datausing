# FlowMask 前端重设计交接单 · 2026-11-05

## 当前状态

已完成正式前端 `web/` 的 Precision Dark Workspace 改造，包括共享设计系统、可折叠三组导航、服务状态工具栏、实时检测编辑器与全部八页迁移。Python/Go 后端、旧 MeTD `client/`、桌面入口与 API 请求契约未改。

已实现：输入空态、Ctrl/⌘ Enter 检测、文件上传、场景模板、高级参数、真实 loading、错误输入保护、快照过期提示、语义风险标记、Unicode 正确高亮、按路径对齐的结构化对照、同步滚动、命中详情、分页、完整副本复制、策略导出、AI 方案与加密还原。其他页面保留原有字段与能力，使用统一 Token/风险标签/页头，任务、流量和设置重组为业务页签。规则增加搜索，批量清空同步文件组件，关闭授权立即隐藏原文。

## 已定决策 —— 不要重新讨论这些

1. 目标为 `datausing/web`，不是邻近 `FlowMask/client`。真实桌面容器是 `server/desktop.py` 的 Edge/Chrome app 模式，本次无需引入 Tauri/React。
2. Vue + Element Plus 保持；新增 Reka UI 只用于无障碍渐进披露，Phosphor Vue 用于统一图标；Playwright 仅开发依赖。具体 Token 与界面契约见 `docs/前端重设计.md`。
3. `api.js` 所有请求实现保持原样，仅调整共享风险色。实时检测不落库，任务/动态授权访问有审计。绝不能把实时检测标成已审计。
4. health 的 L2/L3 只表示配置，不代表模型在线；结果的 `degraded` 与 `model_chain` 必须如实显示。NER 未就绪禁用。SAFE 与命中计数不得伪造。
5. Python spans 使用 Unicode code point；JSON/CSV/SQL 按后端片段路径定位。文件不回传原文时显示边界，不把输入框的文本套到文件结果上。
6. 输入不存浏览器持久存储、不写日志，主题/操作人/侧栏状态可以缓存。主题切换不能卸载当前业务页。API 校验错误不回显敏感 input。
7. 动态原文权限由服务端白名单判定，开关只表达请求；不得通过前端开关绕过服务端拒绝。测试白名单 `auditor-qa` 只允许存在于隔离测试服务。
8. 图表与页面按需分包，避免实时检测首屏加载完整 ECharts；不通过删除业务能力缩小界面。

## 验证与边界

- Node 单元测试：10 项，覆盖 Unicode/转义/区间/路径/计数/错误保护。
- 真实 Edge + FastAPI 的生产 `/app/` 回归：13 项，覆盖文本、JSON、文件、上传失败保留有效快照、下载、高级选项、清空、主题、8 页与 980/1280/1440/1920 宽度、批量失败隔离、规则 CRUD、任务/隐私导出、审计验签、动态拒绝/允许、流量示例、120 段分页/剪贴板、助手聚合信息与加密导出还原。
- 异常/loading 测试单独拦截一个 422；其余上述业务链路使用真实后端与隔离数据库。没有将产品功能替换成 Mock。
- 现有 Python 测试：625 passed、5 skipped。警告为现有 Starlette 422 常量弃用、旧 pytest 缓存目录权限；不涉及业务失败。
- 项目为 JavaScript，无独立 TypeScript 检查脚本。生产构建完成 Vue 编译与分包。
- 未实测：原生 exe 重打包/托盘/多屏 DPI，真实网卡持续旁路，可选 L2/L3/NER/OCR 权重联调，所有文档格式/全部导出组合。对应后端测试保留。本机 browser 自动化使用系统 Edge，缓存 Chromium 可执行文件因 Windows 加密属性无法启动。
- Windows 受限环境需将命令进程 TEMP/TMP 指向项目 `.qa/temp`；曾出现一次 Node 24/libuv 退出断言，完整重跑构建退出码为 0。

## 下一步

本轮重设计已交付。后续建议在新线程：先读本文件，再做 exe 打包、真实模型与流量联调、Windows 125%/150% DPI 人工验收。测试启动方式见 `web/test/README.md`。不要沿用测试服务授权配置部署生产。

## 关键文件 / 本轮修改清单

- 外壳与主题：`web/src/App.vue`、`web/src/main.js`、`web/src/style.css`、`web/src/theme.js`
- 数据显示与安全：`web/src/highlight.js`、`web/src/workspace.js`、`web/src/api.js`（仅共享风险色）
- 共享组件：`web/src/components/AppIcon.vue`、`PageHeader.vue`、`AdvancedOptions.vue`、`EditorPanel.vue`、`RiskIndicator.vue`、`EmptyState.vue`
- 八页：`web/src/views/DetectView.vue`、`BatchView.vue`、`TrafficView.vue`、`TasksView.vue`、`StatsView.vue`、`RulesView.vue`、`DynamicView.vue`、`SettingsView.vue`
- 依赖/构建：`web/package.json`、`web/package-lock.json`、`web/vite.config.js`
- 测试：`web/playwright.config.js`、`web/test/workspace.test.mjs`、`web/test/e2e/workspace.spec.js`、`web/test/README.md`
- 文档/忽略项：`docs/前端重设计.md`、`HANDOFF.md`、`.gitignore`

所有相对路径均以本文件所在项目目录 `D:/AAAdsh-allproject/flow work/datausing` 为基准。构建/测试输出不提交。
