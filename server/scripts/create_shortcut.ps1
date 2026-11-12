# 在桌面创建 FlowMask 快捷方式（启动 / 停止）
#
# 用法（在任意目录）：
#   powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\create_shortcut.ps1
#   powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\create_shortcut.ps1 -Mode stop
#   powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\create_shortcut.ps1 -Mode web
#   powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\create_shortcut.ps1 -ExePath D:\tools\FlowMask.exe -Name "FlowMask 演示"
#
# 说明：
# - 启动快捷方式的「起始位置」必须指向 exe 所在目录：exe 是 PyInstaller onefile，
#   解包目录固定在工作目录下的 .flowmask-tmp，指错目录时可能因没有写权限而闪退；
# - 停止快捷方式调用 scripts\stop_flowmask.cmd（内部是 FlowMask.exe --stop），
#   不依赖模型/服务状态，点了就退出后台实例；
# - 网页端快捷方式（-Mode web）调用 scripts\open_web.ps1：服务已在跑就直接开
#   http://127.0.0.1:8090/app/，没在跑则先在后台拉起再打开；
# - 桌面路径用系统「桌面」特殊文件夹解析，兼容桌面被重定向到其它盘的情况。

[CmdletBinding()]
param(
    [ValidateSet("start", "stop", "web")]
    [string]$Mode = "start",
    [string]$ExePath = "",
    [string]$Name = "",
    [string]$Description = ""
)

$ErrorActionPreference = "Stop"

$serverDir = Split-Path $PSScriptRoot -Parent

$desktop = (New-Object -ComObject WScript.Shell).SpecialFolders.Item("Desktop")
if (-not (Test-Path -LiteralPath $desktop)) {
    throw "找不到桌面目录：$desktop"
}

$shell = New-Object -ComObject WScript.Shell

if ($Mode -eq "web") {
    if (-not $Name) { $Name = "FlowMask 网页端" }
    if (-not $Description) { $Description = "打开 FlowMask 网页端（服务未运行时会自动启动）" }
    $openWeb = Join-Path $serverDir "scripts\open_web.ps1"
    if (-not (Test-Path -LiteralPath $openWeb)) {
        throw "找不到网页端脚本：$openWeb"
    }
    $powershellExe = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"
    $iconPath = Join-Path $serverDir "assets\flowmask.ico"
    if (-not (Test-Path -LiteralPath $iconPath)) {
        $iconPath = Join-Path $serverDir "dist_exe\FlowMask.exe"
    }
    $lnkPath = Join-Path $desktop "$Name.lnk"
    $shortcut = $shell.CreateShortcut($lnkPath)
    $shortcut.TargetPath = $powershellExe
    $shortcut.Arguments = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$openWeb`""
    $shortcut.WorkingDirectory = $serverDir
    if (Test-Path -LiteralPath $iconPath) {
        $shortcut.IconLocation = "$iconPath,0"
    }
    $shortcut.Description = $Description
    # 7 = 最小化：脚本隐藏窗口运行，打开浏览器后即退出
    $shortcut.WindowStyle = 7
    $shortcut.Save()
    Write-Output "已创建网页端快捷方式：$lnkPath"
    Write-Output "  目标：$openWeb"
    Write-Output "  行为：已启动则直接开网页；未启动会后台拉起后打开 http://127.0.0.1:8090/app/"
    exit 0
}

if (-not $ExePath) {
    $ExePath = Join-Path $serverDir "dist_exe\FlowMask.exe"
}
if (-not (Test-Path -LiteralPath $ExePath)) {
    throw "找不到 exe：$ExePath（先运行 scripts\build_exe.ps1 打包；只想打开网页端可用 -Mode web）"
}
$exeFull = (Resolve-Path -LiteralPath $ExePath).Path
$exeDir = Split-Path $exeFull -Parent

if ($Mode -eq "stop") {
    if (-not $Name) { $Name = "FlowMask 停止" }
    if (-not $Description) { $Description = "停止后台运行的 FlowMask 实例" }
    $stopCmd = Join-Path $serverDir "scripts\stop_flowmask.cmd"
    if (-not (Test-Path -LiteralPath $stopCmd)) {
        throw "找不到停止脚本：$stopCmd"
    }
    $lnkPath = Join-Path $desktop "$Name.lnk"
    $shortcut = $shell.CreateShortcut($lnkPath)
    $shortcut.TargetPath = Join-Path $env:SystemRoot "System32\cmd.exe"
    $shortcut.Arguments = "/c `"`"$stopCmd`"`""
    $shortcut.WorkingDirectory = $serverDir
    $shortcut.IconLocation = "$exeFull,0"
    $shortcut.Description = $Description
    # 1 = 正常窗口：点完后能看到一句「已停止」确认（3 秒后自动关）
    $shortcut.WindowStyle = 1
    $shortcut.Save()
    Write-Output "已创建停止快捷方式：$lnkPath"
    Write-Output "  命令：$stopCmd"
    exit 0
}

if (-not $Name) { $Name = "FlowMask" }
if (-not $Description) { $Description = "FlowMask · 分级脱敏与审计溯源系统（双击启动，自动打开界面）" }
# 启动通过 scripts\start_flowmask.cmd 中转：
# - 由它把工作目录切到 exe 目录（onefile 解包目录 .flowmask-tmp 相对工作目录，错了会闪退）；
# - 用 start 脱离控制台，黑框一闪即关，用户只看到应用窗口；
# - 快捷方式本身只依赖 cmd.exe，行为在任何 Windows 会话里都可复现/可验证。
$startCmd = Join-Path $serverDir "scripts\start_flowmask.cmd"
if (-not (Test-Path -LiteralPath $startCmd)) {
    throw "找不到启动脚本：$startCmd"
}
$lnkPath = Join-Path $desktop "$Name.lnk"
$shortcut = $shell.CreateShortcut($lnkPath)
$shortcut.TargetPath = Join-Path $env:SystemRoot "System32\cmd.exe"
$shortcut.Arguments = "/c `"`"$startCmd`"`""
$shortcut.WorkingDirectory = $exeDir
$shortcut.IconLocation = "$exeFull,0"
$shortcut.Description = $Description
# 7 = 最小化：中转脚本约 0.2 秒就退出，不让黑框抢焦点
$shortcut.WindowStyle = 7
$shortcut.Save()

Write-Output "已创建启动快捷方式：$lnkPath"
Write-Output "  目标：$startCmd"
Write-Output "  起始位置：$exeDir"
