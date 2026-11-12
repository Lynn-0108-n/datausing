# 脱敏前后文档对比后端 — 自动化测试

本目录是「脱敏前后文档对比」后端的可重复验证测试，分三层：

| 文件 | 类型 | 说明 |
| --- | --- | --- |
| `conftest.py` | 配置 | 用临时 `FLOWMASK_DATA_DIR`/`FLOWMASK_DB_PATH` 隔离数据；注册 `integration` marker；未设 `FLOWMASK_TEST_BASE_URL` 时自动 skip 集成用例 |
| `test_unit_comparison.py` | 纯单元（无模型/无网络） | 12 个用例：`comparison_payload` 偏移/切片自洽、`mask_spans_with_strategy` mappings、`protected_text`、快照 TTL/owner/secret/策略版本、413/409/422/410 分支 |
| `test_integration_comparison.py` | 集成（真实 HTTP） | 13 个用例：批量上传→轮询→findings→comparison→download，404/409/410/403、413、响应头、日志无原文 |
| `test_integration_ttl.py` | 集成（真实 HTTP，短 TTL 服务） | 1 个用例：快照过期后 comparison/download 均 410 |
| `ttl_harness.py` | 测试专用启动器 | `import app.main` 之前把 `SnapshotStore` 换成 `ttl=2s` 的实例，用于验证生产固定 300s TTL 的同一条过期代码路径（不改业务代码） |

测试数据全部自造（占位姓名、公共示例号段/身份证号/邮箱），不含真实个人信息。

---

## 1. 单元测试（无需启动服务，秒级）

在 `FlowMask\server` 目录下：

```powershell
..\runtime\python.exe -m pytest tests -m "not integration" -p no:cacheprovider
```

预期结果（本机实测）：`12 passed`，exit 0。

> 注意：本机 `FlowMask\server` 目录对 `.pytest_cache` 写入会被拒绝（`WinError 5`），
> 必须加 `-p no:cacheprovider`，否则 pytest 会在收集阶段报权限错误。

---

## 2. 集成测试

### 2.1 启动隔离的验证服务（端口 8395）

前置：确认 8395 空闲；**不要**占用外部 datausing 的 8090/8091/8092/8099、也不要占用 8095/8191/8192。
所有配置都通过 `FLOWMASK_*` 环境变量注入，数据目录指向临时目录，不会写 `FlowMask\data`：

```powershell
$root = 'D:\31387\Desktop\FlowMask-0.27.2-全功能版-20261112-2250'
$verify = "$root\_tmp_backend_verify"
$data = "$verify\data"
New-Item -ItemType Directory -Force -Path $data | Out-Null

$env:FLOWMASK_DATA_DIR       = $data
$env:FLOWMASK_DB_PATH        = "$data\flowmask.db"
$env:FLOWMASK_API_TOKEN      = ''                              # 验证服务不启用 Bearer 鉴权
$env:FLOWMASK_ENABLE_L2      = '0'                             # 关闭对外部 8091 的依赖
$env:FLOWMASK_ENABLE_L3      = '0'                             # 关闭对外部 8092 的依赖
$env:FLOWMASK_ENABLE_NER     = '0'
$env:FLOWMASK_OCR_ENABLED    = '0'
$env:FLOWMASK_MAX_UPLOAD_MB  = '2'                             # 便于验证 413
$env:FLOWMASK_HOST           = '127.0.0.1'
$env:FLOWMASK_PORT           = '8395'
$env:FLOWMASK_EXPORT_SECRET  = 'flowmask-verify-export-secret' # 隔离的导出密钥
$env:FLOWMASK_AUDIT_SECRET   = 'flowmask-verify-audit-secret'

Set-Location "$root\FlowMask\server"
..\runtime\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8395
```

服务就绪自检：`GET http://127.0.0.1:8395/api/v1/health` 返回 `status=ok`、`version=0.27.2`、
`enable_l2=false`、`enable_l3=false`。

> 该命令必须作为**常驻前台进程**运行（终端会话保持）。用 `Start-Process` 从一次性的
> PowerShell 调用里拉起 uvicorn 时，父进程退出后子进程会被一并回收，集成测试会看到
> `WinError 10061 连接被拒绝`。

### 2.2 跑集成测试

```powershell
$env:FLOWMASK_TEST_BASE_URL      = 'http://127.0.0.1:8395'
$env:FLOWMASK_TEST_MAX_UPLOAD_MB = '2'                              # 与服务端 FLOWMASK_MAX_UPLOAD_MB 一致
$env:FLOWMASK_TEST_LOG_FILE      = "$verify\server-8395.log"        # 可选：服务 stdout 重定向文件

Set-Location "$root\FlowMask\server"
..\runtime\python.exe -m pytest tests -m integration -p no:cacheprovider
```

预期结果（本机实测）：`13 passed, 1 skipped`（skipped 为未启动短 TTL 服务的过期用例），
补上 2.3 后整包 `26 passed`。

### 2.3 快照过期（410）端到端验证

生产 `SnapshotStore.ttl` 固定 300 秒且没有对应环境变量；用测试启动器在 `import app.main`
之前替换模块级 `_store`，跑同一条 `_get` 过期分支：

```powershell
# 另一个终端（或后台任务），先复用 2.1 的环境，再改端口/TTL：
$env:FLOWMASK_DATA_DIR      = "$verify\ttl-data"
$env:FLOWMASK_DB_PATH       = "$verify\ttl-data\flowmask.db"
$env:FLOWMASK_PORT          = '8396'
$env:FLOWMASK_TEST_SNAPSHOT_TTL = '2'

Set-Location "$root\FlowMask\server"
..\runtime\python.exe -u tests\ttl_harness.py
```

```powershell
$env:FLOWMASK_TEST_BASE_URL      = 'http://127.0.0.1:8395'   # conftest 需要
$env:FLOWMASK_TEST_TTL_BASE_URL  = 'http://127.0.0.1:8396'
$env:FLOWMASK_TEST_TTL_WAIT      = '3'                       # > 2s TTL

..\runtime\python.exe -m pytest tests -m integration -p no:cacheprovider
```

预期：`14 passed`。

---

## 3. 覆盖矩阵 ↔ 需求

| 验证点 | 用例 |
| --- | --- |
| comparison 返回 filename/status/hits/segments 结构 | `test_comparison_structure_and_offsets` |
| `segments[].original/masked/changes` 与逐 change 字段 | 同上 + `assert_change_invariants` |
| `original[start:end]` 确为被替换文本、`masked[masked_start:masked_end]` 确为替换后文本 | 同上（含 emoji 的 codepoint 切片断言） |
| 未命中区域不被错误标记 | 同上（首条前/末条后原样比对）+ `test_clean_document_has_empty_changes` |
| masked 与任务 findings 的 masked 一致 | `test_comparison_masked_equals_findings_masked` |
| download == `'\n'.join(segments[].masked)`、Content-Disposition | `test_download_equals_masked_segments` |
| 任务不存在 404 | `test_missing_task_404` |
| 未完成/失败 409 | `test_unfinished_task_409`（慢任务复现竞态；服务过快时 skip） |
| 无权限/无 cookie 403 | `test_ownership_isolation_403`（匿名 + 另一会话两个方向） |
| 过期 410 | `test_snapshot_expires_after_ttl_410` + 单元 `test_snapshot_ttl_is_300_seconds_and_expires_410` |
| 超大文档 413 | `test_oversize_upload_413`（单文件 + 批量逐文件失败） |
| `Cache-Control: no-store` / `Pragma: no-cache` | 各 comparison/download 用例统一 `assert_compare_headers` |
| 错误响应不含原文 | 每个错误用例 + `test_server_log_has_no_original_text` |
| 日志无原文 | `test_server_log_has_no_original_text`（需 `FLOWMASK_TEST_LOG_FILE`） |

---

## 4. 验证结论 / 接口语义（给前端用）

1. **偏移单位**：`offset_unit: "unicode-codepoint"`，`start/end/masked_start/masked_end` 都是
   **Python 字符串（Unicode codepoint）下标**，且 `masked_start/masked_end` 是 `masked` 串内的下标。
   - 纯 BMP 文本（中文、数字、常用符号）：与前端 `String.prototype.slice` 一致，可直接切片。
   - 含 astral 字符（emoji 等，JavaScript 里 length=2）：JS 索引比 codepoint 索引大，
     **不能直接 slice**。实测样本中 emoji 之后的一条 change：codepoint `start=64`，JS UTF-16 对应 65。
     前端需先按 codepoint 折叠（如 `Array.from(s)` / `[...s]` 重建映射）或让后端补 UTF-16 偏移。
2. **returns 顺序**：`changes`（即 `mappings`）按原文 `start` **升序（左→右）**返回，互不重叠；
   内部替换实际是右→左执行。
3. **脱敏策略粒度**：`strategy` 由该 segment finding 的级别决定 —— L3（高危，如身份证）→ 整段
   finding 的所有 span 都用 `block`（`[已屏蔽:类别]`）；L1/L2 → 按 span 用 `partial`（首字符 + 最多 6 个 `*`）。
   `level` 为发现级别，`status` 恒为 `"已脱敏"`。
4. **hits ≥ 脱敏条目数**：`hits` 含敏感词表 `kw-` 命中（只参与评分/告警、不脱敏），因此
   `hits` 可以大于 `changes` 总数（实测样本 hits=5、changes=4）。
5. **无快照的情况**：单文件 `POST /tasks` 与 `POST /tasks/text` 不传 `comparison_owner`，
   任务 done 后 `GET /comparison` / `/comparison/download` 均为 **410 `SNAPSHOT_EXPIRED`**
   （detail=`检测快照已过期或重启失效，请重新检测`）。只有 `POST /tasks/batch` 会建立快照，
   且必须用**创建任务时的同一 cookie 会话**（`flowmask_detection_session`，HttpOnly + SameSite=strict + Path=/api/v1）。
6. **TTL / 容量限制**：快照存在进程内存（`SnapshotStore(max_items=32, max_bytes=32MB, ttl=300s)`），
   服务重启即失效；TTL 固定 300s、无环境变量可调；生产者端 `_execute` 写快照失败只记
   `SNAPSHOT_WRITE_FAILED ... error_id=...` 警告，不影响任务状态——此时 comparison 会 410。
7. **错误响应**：只含 `{detail, code, error_id}`（404 任务不存在只含 `detail`），
   不返回原文；服务日志只记 code 与 error_id，不记原文。实测 145 行日志中无任何样本原文/敏感值、无 5xx。
8. **上传上限**：单文件超限返回 413 `{"detail": "文件超过上限 N MB"}`（错误体很小、不夹带文件内容）；
   批量接口对超限文件是「逐文件失败」（该 item.error 提示，其余文件正常建任务）。

### 已知限制

- 集成测试依赖本机以 8395 启动验证服务；未启动或未设 `FLOWMASK_TEST_BASE_URL` 时自动 skip，单元测试不受影响。
- `test_unfinished_task_409` 依赖「任务在 GET comparison 之前还没跑完」的竞态：用大 JSON（多 segment）
  放大检测耗时；若机器极快、两轮都已完成则 skip（不算失败）。
- 验证服务未启用 `FLOWMASK_API_TOKEN`；owner 指纹 = `sha256(session + '|' + Authorization)`，
  生产启用 token 时，对比必须带同一个 Bearer 头，否则同为 403。
- `413` 上传错误由 `read_upload_limited` 直接抛 `HTTPException`，响应**没有** `Cache-Control: no-store`
  （无数据泄露，仅为响应头不一致；如需强制统一可给 413 加同样的安全头）。
