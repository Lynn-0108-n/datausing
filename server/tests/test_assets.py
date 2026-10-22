"""前端与运维脚本的静态回归测试。

这些文件没有 JS/PowerShell 测试基建，但本轮修复的问题都有明确的「坏形状」，
用断言把它们钉住，避免以后重构时又写回去：
- 内置单页不能再使用不存在的 span.level 字段；
- 文件检测的选项必须走查询参数（后端只读 query）；
- 前端偏好保存必须是 PUT（后端没有 POST）；
- 配了 API Token 时前端要能自动带 Authorization 头；
- demo.sh 不能读 DetectOut 顶层不存在的 level/types；
- 停止脚本必须先核对进程命令行，不能只信 pid 文件；
- 启动脚本要先做端口试绑预检；
- 上传脚本不能把变量拼进 Invoke-Expression；
- 任务导出必须走 Blob 下载（能带 Authorization，也不被弹窗拦截）；
- 批量结果的高危/中危要按 /stats 全量统计，不能只数前 100 条 findings；
- 列表/轮询/详情请求要有序号保护，晚到的旧响应必须丢弃。
"""

from pathlib import Path

SERVER_DIR = Path(__file__).resolve().parents[1]
REPO_DIR = SERVER_DIR.parent
WEBUI = SERVER_DIR / "app" / "webui" / "index.html"
WEB_SRC = REPO_DIR / "web" / "src"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_webui_uses_finding_spans_without_undefined_level_field():
    html = _read(WEBUI)
    assert "span.level" not in html
    assert "flatMap" in html


def test_webui_detect_file_passes_options_as_query():
    html = _read(WEBUI)
    assert "/detect/file?" in html
    assert 'form.append("min_level"' not in html


def test_webui_task_export_encodes_task_id():
    html = _read(WEBUI)
    assert "encodeURIComponent(taskId)" in html


def test_web_frontend_saves_preference_with_put():
    api = _read(WEB_SRC / "api.js")
    assert 'jsonBody({ theme }, "PUT")' in api


def test_web_frontend_injects_bearer_token():
    api = _read(WEB_SRC / "api.js")
    assert "flowmask_token" in api
    assert "Authorization" in api
    # 内置单页同样要能带 token
    assert "flowmask_token" in _read(WEBUI)


def test_detect_view_keeps_source_snapshot_for_highlight():
    view = _read(WEB_SRC / "views" / "DetectView.vue")
    assert "detectSource" in view
    assert "fileLabel" in view


def test_demo_script_reads_findings_not_missing_top_level_fields():
    script = _read(SERVER_DIR / "scripts" / "demo.sh")
    assert 'd["level"]' not in script
    assert 'd["types"]' not in script
    assert 'd.get("findings")' in script


def test_stop_scripts_verify_process_ownership_before_kill():
    ps1 = _read(SERVER_DIR / "scripts" / "stop_local.ps1")
    sh = _read(SERVER_DIR / "scripts" / "stop_local.sh")
    assert "CommandLine" in ps1 and "uvicorn" in ps1
    assert "ps -o command=" in sh and "uvicorn" in sh


def test_start_script_prechecks_ports_before_spawn():
    ps1 = _read(SERVER_DIR / "scripts" / "start_local.ps1")
    assert "TcpListener" in ps1


def test_upload_script_does_not_use_invoke_expression():
    deploy = REPO_DIR / "deploy" / "upload-netmon.ps1"
    assert "Invoke-Expression" not in _read(deploy)


def test_tasks_view_export_uses_blob_download_with_auth():
    view = _read(WEB_SRC / "views" / "TasksView.vue")
    # Blob + <a download>，不再先整份 fetch 再 window.open
    assert "createObjectURL" in view
    assert "revokeObjectURL" in view
    assert 'document.createElement("a")' in view
    assert "window.open(api.taskExportUrl" not in view
    # 有 FLOWMASK_API_TOKEN 时导出也要带 Authorization，文件名优先取响应头
    assert "authHeaders" in view
    assert "Content-Disposition" in view


def test_tasks_view_discards_stale_responses():
    view = _read(WEB_SRC / "views" / "TasksView.vue")
    assert "loadTasksSeq" in view
    assert "loadAuditSeq" in view
    assert "openTaskSeq" in view
    assert "seq !== loadTasksSeq" in view
    assert "seq !== openTaskSeq" in view


def test_batch_view_summary_uses_full_task_stats():
    view = _read(WEB_SRC / "views" / "BatchView.vue")
    assert "api.stats(" in view
    assert "by_level" in view
    # 不能再从"前 100 条 findings"里数高危/中危
    assert 'item.level === "L3"' not in view
    assert 'item.level === "L2"' not in view


def test_batch_view_discards_stale_poll_and_submit_responses():
    view = _read(WEB_SRC / "views" / "BatchView.vue")
    assert "refreshSeq" in view
    assert "submitSeq" in view
    assert "openTaskSeq" in view
    assert "seq !== refreshSeq" in view
    assert "seq !== submitSeq" in view
    assert "seq !== openTaskSeq" in view
    # 汇总被丢弃的已完成任务要能在下一轮补统计，不能永远停在 0
    assert "statsLoaded" in view
