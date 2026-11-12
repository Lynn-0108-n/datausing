# FlowMask「文档脱敏前后对比」功能验收报告

- **验收性质**：独立 QA 验收（不参入开发、不修改产品代码）
- **验收对象**：task-3「文档脱敏前后对比」前端功能（FlowMask 0.27.2 全功能版）
- **验收时间**：2026-11-12 23:55 ～ 2026-11-13 00:26（Asia/Shanghai）
- **QA 执行**：qa-verifier（Agent Teams 独立成员）
- **工作区**：`D:\31387\Desktop\FlowMask-0.27.2-全功能版-20261112-2250`
- **被测源码**：`FlowMask\web\src\components\DocumentCompareDialog.vue`、`FlowMask\web\src\views\DetectView.vue`
- **被测构建**：`FlowMask\web\dist\assets\index-BTahdEZB.js`（2026-11-12 23:55:42 构建，已确认包含 `.dc-workbench` 与「对比快照已过期」）
- **交付物**：本报告 `_qa_compare\QA对比功能验收报告.md`、截图 `_qa_compare\shots\`、脚本 `_qa_compare\scripts\`、原始断言结果 `_qa_compare\tmp\*.json`

## 0. 总结论

**PASS —— 功能满足验收目标，可交付。**

| 指标 | 结果 |
|---|---|
| 自动断言通过 / 总数 | **135 / 135 PASS**（0 FAIL） |
| 阻断（BLOCKER）问题 | **0** |
| 高危 / 中危问题 | **0 / 0** |
| 低危问题 | 3（均为文案/边界表现，不影响主流程） |
| 观察项 | 2（非缺陷） |
| 内容真实性 | 原文/脱敏文本、下载文件均与对比接口**逐字一致**（含 emoji/ZWJ） |
| 安全 | 未授权/跨会话 403、缓存头、下载头、DOM/storage/console 无原文残留，全部 PASS |
| 长文档 | 40 万字符 / 7307 行虚拟滚动正常，DOM 仅 47 行；>100 万字符 413 有友好错误态 |
| 控制台 | 无新增 error（仅 favicon 404；错误态测试期间仅有预期状态码资源日志） |

## 1. 测试环境与端口

| 项 | 值 |
|---|---|
| 前端入口 | `http://127.0.0.1:8395/app/`（Hash 路由，默认 `#detect`） |
| API 前缀 | `http://127.0.0.1:8395/api/v1` |
| 服务来源 | 由 Lead 启动并保持运行（QA 全程未停止/重启服务） |
| 数据目录 | `FLOWMASK_DATA_DIR=...\_tmp_frontend_verify\data`（隔离目录） |
| 引擎开关 | `FLOWMASK_ENABLE_L2=0`、`L3=0`、`NER=0`、`OCR=0`；assistant=rules |
| 无 API Token | 未设置（按默认部署形态测试） |
| 版本 | health 200，`version=0.27.2` |
| 浏览器 | Playwright 1.65.1 + 本机 Chrome（`channel="chrome"`，Chromium 内核） |
| 视口 | 常规 1440×900；窄屏 900×800 |
| 运行时 | Python 3.12.10（`_tmp_frontend_verify\venv`） |
| TMP/TEMP/TMPDIR | 指向 `_qa_compare\tmp`（规避系统 Temp EPERM） |
| 端口/文件约束 | 未占用 8090-8099、8191、8192；未修改 `FlowMask\web`、`FlowMask\server`、`FlowMask\data`；仅写入 `_qa_compare\` |

## 2. 测试方法

采用「真实 UI 点击 + 真实接口」的端到端方式，独立设计 4 套 Playwright 脚本（未复用 Lead 冒烟脚本）：

| 套件 | 脚本 | 断言数 | 覆盖 |
|---|---|---|---|
| S1 核心 UI + 安全 | `_qa_compare\scripts\s1_ui_core.py` | 74 | 入口/表格、弹窗尺寸与双栏、内容真实性、高亮（含 emoji）、浮层、导航、同步滚动、视图、窄屏、焦点/Escape、关闭清理、下载、loading、无差异、API 安全头与 403 |
| S2 长文档 + 异常态 | `_qa_compare\scripts\s2_long_error_states.py` | 44 | 解析失败行、运行中 409、>100 万字符 413、40 万字符虚拟滚动/性能/同步滚动、404/403/409/410、快照驱逐真实 410、console |
| S4 原有流程回归 | `_qa_compare\scripts\s4_regression.py` | 9 | 文本检测、上传文件按钮、清空、原有导出控件、console |
| S5 多片段文档 | `_qa_compare\scripts\s5_docx_multi_segment.py` | 8 | docx 多 segment（段落 + 表格单元格）对比文本/高亮/片段数 |

> 全部 135 条断言结果 PASS，原始数据见 `_qa_compare\tmp\s1_results.json`、`s2_results.json`、`s4_results.json`、`s5_results.json`（含每条用例的机器可读证据）。

## 3. 逐项用例结果

### 3.1 入口与原有功能回归（验收目标 1、11）

| 编号 | 用例 | 结果 | 证据 |
|---|---|---|---|
| 1.1 | 批量结果表列顺序 | PASS | 表头依次为：文件 / 状态 / 片段 / 命中 / 高危/中危 / 任务 / 操作 / 错误 |
| 1.2 | 操作列按钮顺序为「查看｜对比｜明细｜脱敏文件」 | PASS | 实测 DOM 顺序：`查看 → 对比 → 明细(a.export-link) → 脱敏文件` |
| 1.3 | 对比按钮仅在任务完成且 taskId 存在时可用 | PASS | done 行 `disabled=false`，title=「查看原文与脱敏结果的逐字符对比」；running 行 disabled=true；解析失败行 disabled=true 且无 taskId |
| 1.4 | 「查看」原有行为不变 | PASS | 点击后 `GET /tasks/{id}/findings` 返回 200 |
| 1.5 | 「明细」打开 JSONL | PASS | 链接 `href=/api/v1/tasks/{id}/export?format=jsonl`、`target=_blank`；实测下载 200 / `application/x-ndjson`，首行为合法 JSON |
| 1.6 | 「脱敏文件」可下载 | PASS | 点击后下载成功，文件内容为脱敏副本（不含原文敏感值） |
| 1.7 | 文本「检测」入口无回归 | PASS | 输入 3 行敏感文本 → 左右对照 2 栏、右侧含 `[已屏蔽:姓名]` 等、legend 3 级计数、导出控件齐全 |
| 1.8 | 「上传文件」入口无回归 | PASS | Playwright `expect_file_chooser` 捕获到系统文件选择器 |
| 1.9 | 「清空」无回归 | PASS | 点击后批量结果行=0、待检测文件标签=0，可继续下一次检测 |
| 1.10 | 无新增 console error | PASS | 见 §6；仅 favicon 404 |

### 3.2 弹窗外观、布局与可访问性（验收目标 2、10）

| 编号 | 用例 | 结果 | 证据 |
|---|---|---|---|
| 2.1 | 弹窗宽度约视口 85%-92% | PASS | 1440×900：宽 1267.19px = **87.99%** |
| 2.2 | 弹窗高度约视口 80%-90% | PASS | 高 808px = **89.78%**（窄屏数据见 §5 L-1） |
| 2.3 | 顶部信息：文档名 / 脱敏状态 / 命中数 / 片段字符数 | PASS | `sample_a.txt`、「已脱敏」、`命中 20 处 · 1 个片段 · 746 字符` |
| 2.4 | 双栏（原始文档 / 脱敏文档） | PASS | `.dc-pane-orig`、`.dc-pane-masked` 两栏，栏头带「浅红=敏感命中 / 浅蓝=脱敏替换」图例 |
| 2.5 | 视图切换 + 关闭按钮 | PASS | 三项：左右对比 / 仅原文 / 仅脱敏文档；关闭按钮 aria-label=「关闭对比」 |
| 2.6 | 弹窗语义/ARIA | PASS | `.dc-dialog` aria-label=「文档脱敏前后对比」；两栏滚动区 role=region，名称分别为「原文文档内容」「脱敏文档内容」 |
| 2.7 | 弹窗内独立滚动、主页面无布局位移 | PASS | 打开前后 `body` scrollHeight 均为 900，textarea 位置未变；`.dc-scroll` 独立滚动 |
| 2.8 | 关闭后主页面可用 | PASS | 关闭后 body 尺寸恢复，文本输入/按钮可继续操作 |
| 10.1 | Escape 关闭 | PASS | 按 Esc 后 `.dc-dialog` 数=0 |
| 10.2 | Tab 焦点不逃出弹窗 | PASS | 连续 Tab 18 次，焦点元素始终在弹窗内（`.dc-dialog` 后代） |
| 10.3 | 关闭按钮可读名称 | PASS | aria-label=「关闭对比」；浮层关闭按钮 aria-label=「关闭字段信息」 |
| 10.4 | 高亮点击区域有 title | PASS | 所有 `mark.dc-mark` title=「点击查看该字段信息」 |

### 3.3 内容真实性与高亮准确性（验收目标 3、4）

接口基准（按当轮真实任务 `sample_a.txt`）：status 200、hits=20、`representation=parsed-segments`、`offset_unit=unicode-codepoint`、segments=1、original=728 codepoints、masked=674、changes=14。

| 编号 | 用例 | 结果 | 证据 |
|---|---|---|---|
| 3.1 | 对比接口 200 且数据完整 | PASS | `payload_stats {hits:20, changes:14, orig_len:728, masked_len:674}` |
| 3.2 | 左侧是真实原文（与接口逐字一致） | PASS | DOM `.dc-text` 合并文本长度 728，与 `segments[0].original` 完全相等 |
| 3.3 | 右侧是真实脱敏结果（与接口逐字一致） | PASS | DOM 长度 674，与 `segments[0].masked` 完全相等 |
| 4.1 | 两侧高亮数量一致且等于 changes | PASS | orig marks=14、masked marks=14、changes=14 |
| 4.2 | 原文侧只高亮被替换字符区间 | PASS | 逐 change 比对：mark 文本 == `original[start:end]`，全部匹配；data-i 顺序 0..13 |
| 4.3 | 脱敏侧同样精确 | PASS | mark 文本 == `masked[masked_start:masked_end]`（如 `[已屏蔽:姓名]`） |
| 4.4 | 不整行高亮 | PASS | 「张三」mark 宽 27px，而整行远宽于该值；高亮仅覆盖字段片段 |
| 4.5 | 未敏感文本不误标 | PASS | 干扰行中裸姓名「张三」只出现 1 处高亮（带字段前缀的 1 处）；普通编号/金额/短姓名未误标（注：`12.34.56.78` 本身是合法 IPv4，被命中属正确行为） |
| 4.6 | 两侧高亮一一对应 | PASS | 相同 data-i 在两侧同时存在、同时按「上一处/下一处」切换 |
| 4.7 | emoji 文档高亮位置不偏移 | PASS | `sample_emoji.txt`：原文 9 处高亮 UTF-16 偏移实测 = 期望（17,37,57,82,100,133,157,186,212）；脱敏侧 9 处 = 期望（17,35,61,78,94,118,136,170,194） |
| 3.4 | 「下载脱敏文档」与右侧逐字一致 | PASS | 下载文件 `flowmask-masked.txt`，191 字符；与 API masked 一致 且 与弹窗右侧 DOM 文本一致；Content-Type `text/plain; charset=utf-8`、`Content-Disposition: attachment; filename="flowmask-masked.txt"` |
| 3.5 | 多片段文档（docx） | PASS | 2 段落 + 1 表格 → 4 个 segment（`p1,p2,table1.1.1,table1.1.2`）；左右 DOM 文本 = 各 segment 以 `\n` 连接（原文 75 字符 / 脱敏 60 字符逐字一致）；高亮 3/3；头部 `4 个片段` |

### 3.4 字段浮层与差异导航（验收目标 5）

| 编号 | 用例 | 结果 | 证据 |
|---|---|---|---|
| 5.1 | 点原文敏感字段弹出浮层 | PASS | 浮层 role=dialog、aria-label=「敏感字段信息」，显示：敏感类别 姓名、检测级别 高危、替换策略 全屏蔽、处理状态 已脱敏、文档片段 `$` |
| 5.2 | 点击后对侧对应字段同步高亮 | PASS | 两侧同一 data-i 同时带 `is-active`（实测 ["0"]/["0"]） |
| 5.3 | 反向点击脱敏字段同样成立 | PASS | 点脱敏侧第 5 处（银行卡号）：浮层类别=银行卡号，两侧 active 均为 ["4"] |
| 5.4 | 浮层可关闭 | PASS | 点外部区域、点关闭按钮均移除浮层 |
| 5.5 | 上一处/下一处计数正确 | PASS | 序列：第 1/14 → 第 2/14 → 第 3/14 →（上一处）第 2/14 → 第 1/14 →（上一处循环）第 14/14 |
| 5.6 | 导航使高亮定位到可视区域 | PASS | 短文档：目标 mark 进入原文档滚动可视区（visible=true）；长文档见 3.5 |
| 5.7 | 同步滚动默认开且可关闭 | PASS | 打开时「同步滚动」switch 默认 checked；关闭后滚动不再联动，重开立即对齐 |

### 3.5 视图与窄屏（验收目标 6）

| 编号 | 用例 | 结果 | 证据 |
|---|---|---|---|
| 6.1 | 左右对比 / 仅原文 / 仅脱敏文档 | PASS | 仅原文：1 个 `.dc-pane-orig` 且同步滚动禁用；仅脱敏文档：1 个 `.dc-pane-masked`；左右对比：2 栏、同步滚动可开 |
| 6.2 | 窄视口（900×800）默认单栏 | PASS | 打开即单栏（orig），视图选项只剩「原文 / 脱敏文档」，无「左右对比」 |
| 6.3 | 窄屏不再提供双栏选项 | PASS | radios = [原文, 脱敏文档]；同步滚动 switch disabled |

### 3.6 长文档与虚拟滚动（验收目标 7）

样本 `sample_large.txt`：400,009 字符 / 7307 行；接口 200，hits=69，changes=46。

| 编号 | 用例 | 结果 | 证据 |
|---|---|---|---|
| 7.1 | 40 万字符文档可正常对比 | PASS | payload 200；header `命中 69 处 · 1 个片段 · 400,023 字符` |
| 7.2 | 虚拟滚动：DOM 行数远小于总行数 | PASS | 初始 DOM 仅 **47 行**（总 7307 行，约 0.64%）；`.dc-content` 高度 160,754px = 7307×22 |
| 7.3 | 滚动到中部仍窗口化且定位正确 | PASS | scrollTop=80,377 时 DOM 47 行、文本 2506 字符；行号连续、无重复渲染 |
| 7.4 | 滚动流畅无卡顿 | PASS | 30 步 rAF 滚动：总耗时 221.2ms、平均帧间隔 **7.37ms**、最大 22ms，渲染行数 47（无卡死/掉帧） |
| 7.5 | 同步滚动（长文档双向） | PASS | 原文滚到 30,000 → 脱敏 30,000；反向 12,000 → 12,000；关闭后 60,000 vs 12,000；重开立即对齐 60,000 |
| 7.6 | 初始差异计数 | PASS | 「共 46 处差异」 |
| 7.7 | 下一处/上一处长文定位与循环 | PASS | 第 1 处 scrollTop=10,846（期望 10,846，首处位于第 499 行）；第 46 处 153,846；再下一处循环回第 1 处 10,846 |
| 7.8 | 长文档自动换行禁用提示 | PASS | >20 万字符时「自动换行」disabled，title=「文档超过 20 万字符，自动换行可能影响性能，已禁用」 |
| 7.9 | >100 万字符对比返回 413 且有友好错误态 | PASS | 105 万字符任务：检测本身 done（上传 20MB 限制内）；对比 UI 显示「文档过大 / 文档超过 100 万字符，无法生成对比视图。请拆分文件后重新检测。/ 错误编号 … / 关闭 / 重试」（截图 s2_01） |
| 7.10 | 关闭长文档弹窗后布局恢复 | PASS | bodyH=900、bodyW=1440、textarea rect 恢复；`.dc-dialog` 从 DOM 移除 |

### 3.7 异常态（验收目标 8）

| 编号 | 用例 | 结果 | 证据 |
|---|---|---|---|
| 8.1 | 404 任务不存在（真实接口） | PASS | HTTP 404（HTTPException，body 仅 `{"detail":"任务不存在"}`，无 code/error_id）；UI 标题「任务不存在」、说明「该检测记录已被删除，请刷新批量结果后重试。」、有关闭/重试（截图 s2_05） |
| 8.2 | 403 无权限（真实第二浏览器会话） | PASS | 另一 owner 的上下文把请求路由到本会话任务：HTTP 403，body `{"detail":"当前会话不能访问该检测快照","code":"SNAPSHOT_FORBIDDEN","error_id":…}`；UI「无权限查看原文」+ 错误编号 + 重新检测说明（截图 s2_06） |
| 8.3 | 409 任务未完成/脱敏失败 | PASS | 真实接口（1M 字符任务运行中立即请求）返回 409 `{"detail":"任务未完成或脱敏失败"}`；UI 错误态「脱敏副本已失效」用路由 fulfill 模拟（真实 409 时序窗口过短，见 §8 限制）（截图 s2_07） |
| 8.4 | 410 快照过期（真实接口） | PASS | 通过创建 40 个新任务使快照存储淘汰旧快照（max_items=32）：旧任务 comparison 返回 410 `{"detail":"检测快照已过期或重启失效，请重新检测","code":"SNAPSHOT_EXPIRED","error_id":…}`；UI「对比快照已过期 / 原文对比只在检测完成后短时间内可用（服务端临时快照，约 5 分钟）。请重新检测该文件后再对比。/ 错误编号 …」（截图 s2_08） |
| 8.5 | 410/413/403 错误响应头 | PASS | 均含 `cache-control: no-store`、`pragma: no-cache` |
| 8.6 | 413 错误编号与重试 | PASS | UI 显示的错误编号 = UI 本次请求响应体 `error_id`；提供「关闭 / 重试」；重试后正常文档可恢复双栏 |
| 8.7 | loading 态 | PASS | 挂起请求后稳定显示 spinner + 「正在读取原文与脱敏结果…」+「原文仅从服务端临时快照读取，不写入浏览器存储」（截图 s1_09） |
| 8.8 | 无差异文档 | PASS | `sample_clean.txt`：.dc-notice=「未发现敏感信息，两侧内容一致（无差异）」、计数标签「无差异」、marks=0、仍双栏展示（截图 s1_10） |
| 8.9 | 解析失败行 | PASS | 伪造非 docx 字节：状态「解析失败」、错误列给出原因；查看/对比均 disabled，无 taskId（对比 title=「任务完成检测后可对比」） |

### 3.8 安全（验收目标 9）

| 编号 | 用例 | 结果 | 证据 |
|---|---|---|---|
| 9.1 | 未带 cookie 请求 comparison | PASS | HTTP 403，body code=SNAPSHOT_FORBIDDEN |
| 9.2 | 另一浏览器会话请求 comparison / download | PASS | 均 403；原会话仍可访问（非全局失效） |
| 9.3 | 不存在任务 | PASS | HTTP 404 + no-store |
| 9.4 | 对比成功响应头 | PASS | `cache-control: no-store`、`pragma: no-cache` |
| 9.5 | 下载接口响应头 | PASS | `no-store`、`no-cache`、`Content-Disposition: attachment; filename="flowmask-masked.txt"` |
| 9.6 | 关闭后 localStorage/sessionStorage 无原文/敏感值 | PASS | 仅 `guide-tip-closed=true`；搜索「张三 / 13800138000 / 110101199003071233」均为 0 |
| 9.7 | 控制台不打印原文字符串 | PASS | console 全量文本中无原文/敏感值 |
| 9.8 | 正文 DOM 无隐藏原文副本 | PASS | 关闭后 `.dc-dialog` 数=0、`.dc-field-card` 数=0；body 内搜索敏感值 0 命中（含隐藏元素） |

## 4. 截图清单

截图目录：`_qa_compare\shots\`（绝对路径 `D:\31387\Desktop\FlowMask-0.27.2-全功能版-20261112-2250\_qa_compare\shots\`）。

| 截图（相对工作区路径） | 说明 | 覆盖验收项 |
|---|---|---|
| `_qa_compare/shots/s1_01_entry_table.png` | 批量结果表操作列 | 1 |
| `_qa_compare/shots/s1_02_dialog_split.png` | 弹窗双栏（原文/脱敏） | 2、3 |
| `_qa_compare/shots/s1_03_field_card.png` | 点原文敏感字段浮层 | 5 |
| `_qa_compare/shots/s1_04_field_card_reverse.png` | 点脱敏字段反向浮层 | 5 |
| `_qa_compare/shots/s1_05_nav.png` | 上一处/下一处导航计数 | 5 |
| `_qa_compare/shots/s1_06_view_original_only.png` | 仅原文视图 | 6 |
| `_qa_compare/shots/s1_07_view_masked_only.png` | 仅脱敏文档视图 | 6 |
| `_qa_compare/shots/s1_08_narrow.png` | 窄屏 900×800 默认单栏、无左右对比 | 6 |
| `_qa_compare/shots/s1_09_loading.png` | loading 态（spinner） | 8 |
| `_qa_compare/shots/s1_10_no_diff.png` | 无差异文档提示 | 8 |
| `_qa_compare/shots/s1_11_after_close.png` | 关闭后主页面 | 2、9 |
| `_qa_compare/shots/s2_01_413_too_large.png` | 413 文档过大错误态（真实 UI） | 7、8 |
| `_qa_compare/shots/s2_02_longdoc_split.png` | 40 万字符长文档双栏 | 7 |
| `_qa_compare/shots/s2_03_nav_last.png` | 长文档跳转到第 46 处 | 5、7 |
| `_qa_compare/shots/s2_04_longdoc_nav.png` | 长文档循环导航回第 1 处 | 5、7 |
| `_qa_compare/shots/s2_05_404_not_found.png` | 404 任务不存在 | 8 |
| `_qa_compare/shots/s2_06_403_forbidden.png` | 403 跨会话无权限 | 8、9 |
| `_qa_compare/shots/s2_07_409_policy_changed.png` | 409 脱敏副本已失效 | 8 |
| `_qa_compare/shots/s2_08_410_expired.png` | 410 快照已过期（真实） | 8 |
| `_qa_compare/shots/s4_01_text_detect.png` | 文本检测入口回归 | 11 |
| `_qa_compare/shots/s4_02_batch_upload.png` | 批量上传回归 | 11 |
| `_qa_compare/shots/s4_03_after_clear.png` | 清空后回归 | 11 |
| `_qa_compare/shots/s5_01_docx_multi_segment.png` | docx 多片段对比 | 3、4 |

> 另有 `explore_01..08*.png` 为探索阶段的辅助截图，最终结论以上表正式用例截图为准。

## 5. 发现的问题（分级 / 复现 / 定位）

**无阻断（BLOCKER）、无高危、无中危。** 以下为低危文案/边界偏差与观察项：

### L-1（低）窄视口下弹窗高度 92.25%，略超「80%-90%」字面范围

- **定位**：`FlowMask\web\src\components\DocumentCompareDialog.vue:747-748`（`el-dialog :width="isNarrow ? '96%' : '88%'" top="4vh"`）+ `:954-957`（`.dc-workbench { height: 86vh }`）
- **复现**：视口 900×800 打开批量结果「对比」→ 弹窗实测 864×738，宽 96%、高 92.25%；1440×900 下为 87.99%/89.78%（PASS）。
- **影响**：仅窄屏/低高度窗口下高度略超出字面区间；内容完整可用、无遮挡。
- **建议**：窄屏时降低 `top` 或 `.dc-workbench` 高度（如 82vh），使高度落回 90% 内。

### L-2（低）「对比」按钮 title 未随任务状态变化

- **定位**：`FlowMask\web\src\views\DetectView.vue:822-824`
  `:disabled="!row.taskId || row.status !== 'done'"`，但 `:title="row.taskId ? '查看原文与脱敏结果的逐字符对比' : '任务完成检测后可对比'"`
- **复现**：上传 105 万字符大文件，任务 running 时悬停「对比」：按钮 disabled，但 tooltip 仍是「查看原文与脱敏结果的逐字符对比」。
- **影响**：提示语与禁用状态不一致（极小）；不影响禁用逻辑本身。
- **建议**：title 条件改为 `row.taskId && row.status === 'done'`。

### L-3（低）「命中 N 处」与「共 M 处差异」口径不同，易误解

- **定位**：`FlowMask\web\src\components\DocumentCompareDialog.vue:766`（`命中 {{ formatCount(model.payload.hits) }} 处`）与差异计数（`共 N 处差异`）
- **复现**：`sample_a.txt` 头部「命中 20 处」，导航区「共 14 处差异」；长文档「命中 69 处」vs「共 46 处差异」。
- **原因**：`hits` 为规则命中数（同一字段可能多规则/多次命中），`changes` 为去重后的敏感区间数；两者语义不同但文案都带「处」。
- **影响**：用户可能误以为差异数量统计错误。
- **建议**：文案区分（如「规则命中 20 次 · 差异 14 处」）或统一口径（产品决策）。

### L-4（低）头部「字符数」按 UTF-16 计算，emoji 文档略偏大

- **定位**：`FlowMask\web\src\components\DocumentCompareDialog.vue:85-88`（`formatCount(current.original.length)`，JS 字符串长度为 UTF-16 码元数）
- **复现**：`sample_a.txt` 后端 codepoint 728，弹窗显示 746；40 万字符样本显示 400,023（后端 400,009）；emoji 越多偏差越大。
- **影响**：仅计数展示差异，不影响内容/高亮。
- **建议**：若需与后端一致，改用 `[...text].length`（codepoint）或注明统计口径。

### O-1（观察）404 错误态无「错误编号」

- 后端 `FlowMask\server\app\api\routes_tasks.py:262-289` 的 404 为 `HTTPException(404, "任务不存在")`，响应体只有 `detail`，UI 因此不显示错误编号；而 410/413/403 走 security 错误格式（含 code/error_id）。属后端既有格式差异，UI 已正确降级，非对比功能缺陷。

### O-2（观察）解析失败行「脱敏文件」按钮仍可点击

- 解析失败行没有 taskId（查看/对比均正确禁用），但文件对象仍在，按既有 `v-if="row.fileObj"` 逻辑显示「脱敏文件」。属原有行为，不属本次对比功能回归；如需彻底禁用可另行评估。

## 6. 控制台错误

- 正式用例运行期间，页面 console 仅出现 **favicon 404**：Chromium 输出文本为 `Failed to load resource: the server responded with a status of 404 (Not Found)`（该文本不含 URL），通过网络响应日志核实唯一 404 资源请求为 `GET /favicon.ico`，故列入允许项。
- 故意构造的错误态测试期间，出现对应的预期资源错误日志（404/403/409/410/413，如 `Failed to load resource: the server responded with a status of 413 (Request Entity Too Large)`），均属被测错误路径的预期表现。
- 无 `pageerror`、无未捕获异常、无原文/敏感值打印、无 Vue 警告或 Element Plus 告警。
- 结论：**无新增 console error**（favicon 404 与故意拦截状态码除外）。

## 7. 安全结论

**通过。** 逐项核对：

1. **快照隔离**：comparison 与 download 均按 `_owner(request)`（cookie `flowmask_detection_session` + Authorization 指纹）鉴权；无 cookie 直接请求 → 403；另一浏览器会话请求 → 403；原会话不受影响。
2. **缓存控制**：成功、下载、错误（403/404/409/410/413）响应均带 `Cache-Control: no-store`、`Pragma: no-cache`。
3. **下载安全**：`text/plain; charset=utf-8` + attachment `flowmask-masked.txt`；内容只含脱敏文本，且与对比接口/右侧 DOM 逐字一致。
4. **前端残留**：弹窗关闭后 `.dc-dialog`、字段浮层从 DOM 移除（destroy-on-close），body 全文搜索敏感值 0 命中（含隐藏元素）；localStorage/sessionStorage 仅 `guide-tip-closed=true`。
5. **控制台**：无原文输出。
6. **DOM 隐藏副本**：未发现隐藏的原文副本节点。

## 8. 限制与未覆盖项

1. **引擎配置**：本环境 NER/OCR/L2/L3 均关闭，对比内容验证基于 rules 引擎；开启 NER/OCR/大模型后的对比表现未覆盖（超出本次前端验收范围，且环境由 Lead 指定）。
2. **410 触发方式**：未等待真实 5 分钟 TTL，而是通过快照驱逐（创建 40 个新任务，SnapshotStore max_items=32）触发真实 `SNAPSHOT_EXPIRED`；与 TTL 过期走同一代码路径，UI 表现一致。
3. **409 UI 态**：真实 409 由「任务运行中立即请求」在接口层验证（时序竞争无法稳定在 UI 点击时捕获）；UI 错误态展示用 `page.route` fulfill 模拟。
4. **403 构造**：使用真实第二浏览器会话（独立 owner cookie）经路由重写请求本会话任务，服务端真实返回 403，非 mock。
5. **长文档区间**：验收要求 30 万-80 万字符，实测 40 万字符（7307 行）；80 万端点未单独构造（同一虚拟滚动路径，风险低）。
6. **浏览器覆盖**：仅 Chromium（本机 Chrome），未覆盖 Firefox/Safari 与移动真机；窄屏以桌面浏览器 900×800 模拟。
7. **docx 复杂结构**：已覆盖 2 段落 + 1 表格多 segment；合并单元格、嵌套表格、页眉页脚/图片等复杂 OOXML 未覆盖。
8. **性能指标**：以 DOM 行数 + rAF 帧间隔衡量，未测内存峰值、未在低配机/高 DPI 下复测。
9. **409 策略变更真实路径**：`SNAPSHOT_POLICY_CHANGED` 需并发改变任务策略/密钥才能真实构造，本次以 route 模拟 UI 态。

## 9. 运行方式与证据文件

```powershell
$env:TMP="D:\31387\Desktop\FlowMask-0.27.2-全功能版-20261112-2250\_qa_compare\tmp"
$env:TEMP=$env:TMP; $env:TMPDIR=$env:TMP; $env:PYTHONIOENCODING="utf-8"
& "...\_tmp_frontend_verify\venv\Scripts\python.exe" "...\_qa_compare\scripts\s1_ui_core.py"
# 依次 s2_long_error_states.py / s4_regression.py / s5_docx_multi_segment.py
```

| 文件 | 内容 |
|---|---|
| `_qa_compare\QA对比功能验收报告.md` | 本报告 |
| `_qa_compare\tmp\s1_results.json` | S1 全部 74 条断言 + 原始证据 |
| `_qa_compare\tmp\s2_results.json` | S2 全部 44 条断言 + 原始证据 |
| `_qa_compare\tmp\s4_results.json` | S4 全部 9 条断言 |
| `_qa_compare\tmp\s5_results.json` | S5 全部 8 条断言 |
| `_qa_compare\samples\` | 测试样本（sample_a / sample_emoji / sample_clean / sample_large / sample_huge / sample_multi.docx / sample_bad.docx） |
| `_qa_compare\scripts\` | 全部 QA 脚本（common.py、gen_samples.py、s1/s2/s4/s5、探针脚本） |

### 附录：关键任务 ID（当轮真实任务）

| 样本 | taskId | 说明 |
|---|---|---|
| sample_a.txt | `9d26cbd7fe61425e` | 14 changes / 728 codepoints |
| sample_emoji.txt | `cd26b3268bb04b11` | 9 changes / 211 codepoints（emoji 偏移专项） |
| sample_clean.txt | `36f31978c8634ea6` | 0 changes（无差异） |
| sample_large.txt | `e7fd61347706413a` | 400,009 字符 / 7307 行 / 46 changes |
| sample_huge.txt | `5ce36fb048cd4fb1` | 1,050,039 字符 → comparison 413 |
| sample_multi.docx | `265b4feb9e6e4c3b` | 4 segments（p1/p2/table1.1.1/table1.1.2） |

### 附录：断言统计

| 套件 | PASS / 总数 |
|---|---|
| S1 核心 UI + 安全 | 74 / 74 |
| S2 长文档 + 异常态 | 44 / 44 |
| S4 原有流程回归 | 9 / 9 |
| S5 多片段 docx | 8 / 8 |
| **合计** | **135 / 135** |
