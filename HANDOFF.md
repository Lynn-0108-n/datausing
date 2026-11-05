# FlowMask 后端业务审计交接单 · 2026-11-05

## 当前状态（最新）

已按用户要求完成先审查、后方案这一阶段：真实调用链审查、功能矩阵、32项问题与能力缺口、场景复现、四批计划和前端接口建议。**未修复业务代码，F01～F32均未关闭**。审查基线`73aaf10`。

- 报告：`docs/后端业务全面审计-2026-11-05.md`
- 计划：`docs/superpowers/plans/2026-11-05-flowmask-backend.md`
- Python：625 passed、5 skipped、1现有弃用警告；Node：10 passed。
- Go全套`go test ./...`单次通过，build/vet退出码0；`TestOnlineDLPCountsDroppedWhenQueueFull -count=20`失败，已复现时序假设，不能称验证全部稳定通过，更不能据此断言产品额外丢包。后续计划有独立测试修正项。
- 新增合成探针已复现结构化导出残留、动态SAFE/L1覆盖、任务L1落原值、归一化/重叠漏检、规则漂移导出、流量URL元数据泄露、重组与live停止/失败等缺陷。真实模型/网卡/exe未验证。

## 已定决策 —— 不要重新讨论这些

1. 当前产品是本目录`datausing/`，不是旁边的旧版`FlowMask/`。正式前端`web/`，Python`server/`主服务，桌面入口`server/desktop.py`；旧Go/MeTD功能保留。
2. 本次任务先全面审查输出方案，再分批完善。当前阶段完成的是审计交付；不要把报告中的方案写成已实现，不要一开始重做整个架构。
3. 保留FastAPI/SQLite/Document/Segment与既有API；不引入分布式队列，不用Mock替代真实业务结果，不删除旧能力。
4. 风险等级与掩码策略分离；原文不持久化；任务异常恢复不能靠暗存原文。旧库可能含历史L1原值，未经明确授权不清理/迁移真实用户内容。
5. 第一批统一检测/风险/脱敏，同时补直接消费者的最小防泄露边界；第二批规则/任务；第三批审计/可信主体/统计；第四批批量最终联调/流量/其他能力。
6. 不重启已完成的前端重设计。只在后端新契约必要时最小接入前端。
7. 文档与AGENTS里的旧绝对路径已过时，本项目实际根目录为`D:/AAAdsh-allproject/flow work/datausing`。当前主干main，文档提交不升级产品版本。
8. 多项问题已通过隔离合成数据复现；新线程先读报告的对应F项和计划，再把复现转为失败回归测试，不重走文件名猜能力的流程。

## 下一步

建议新线程从第一批任务1开始：结构化唯一定位、JSON数字值、CSV重名列、SQL混合值列上下文、安全格式回写。接着修归一化/重叠/PEM、引擎状态、最低策略和检测快照。每项先失败测试，再最小修改、回归、独立提交。

注意：仅规则/选项指纹不能保证模型重推理一致；最终导出要消费不可变命中集。结果句柄的随机性、主体绑定、每次授权、TTL和条数/字节预算必须随首次实现一起完成。

当前只有共享可选Bearer与自报operator，不能把白名单称为多角色强身份隔离；先补不可降级掩码，再按第三批完善本机能力模型。跨格式不支持必须明确拒绝，不能200返回原始内容。

## 关键文件

- 检测：`server/app/identify/{pipeline,rules,normalize,scoring,masking,l2,l3,ner}.py`
- 解析/输出：`server/app/parsers/`、`server/app/tasks/{export,document_export}.py`
- 任务/权限：`server/app/tasks/service.py`、`server/app/api/{deps,routes_tasks,routes_detect,routes_dynamic}.py`
- 持久化/审计：`server/app/core/db.py`、`server/app/audit/chain.py`、`server/app/privacy.py`
- 流量：`server/app/traffic/`、`server/app/api/routes_traffic.py`；Go `cmd/flowmask/`、`internal/dlp/`、`internal/app/`
- 既有验证：`server/tests/`、`web/test/`、`internal/app/dlp_test.go`

---

# 历史前端重设计交接（保留） · 2026-11-05

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
