<#
.SYNOPSIS
    打开 FlowMask 网页端（服务未运行时会自动在后台拉起）。

.DESCRIPTION
    桌面「FlowMask 网页端」快捷方式调用的就是本脚本：
    1. 先探测本机是否已有 FlowMask 服务在跑：优先 8090（源码一键启动默认端口），
       再扫描 8765-8799（桌面 exe 版自动挑端口的范围）；
    2. 找到就直接用默认浏览器打开 http://127.0.0.1:<端口>/app/；
    3. 没找到且未指定 -NoStart 时，调用 scripts\start_local.cmd 在后台启动（不打开浏览器），
       最多等 90 秒健康检查通过，再打开网页；
    4. 失败时弹窗提示，并把过程写进 server\logs\open_web.log。

    这个脚本只负责「打开网页」，启动失败/端口被占用等细节都交给 start_local 脚本处理。

.PARAMETER NoStart
    服务没在跑时只弹窗提示，不自动启动。

.PARAMETER DryRun
    只打印将要执行的动作，不启动服务、不打开浏览器（自动化测试/排障用）。

.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File scripts\open_web.ps1 -DryRun
#>
[CmdletBinding()]
param(
    [switch]$NoStart,
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"

$serverDir = Split-Path $PSScriptRoot -Parent
$defaultPort = 8090
$ports = @($defaultPort) + (8765..8799)
$logDir = Join-Path $serverDir "logs"
$logFile = Join-Path $logDir "open_web.log"

function Write-Log([string]$Message) {
    try {
        New-Item -ItemType Directory -Force -Path $logDir | Out-Null
        $line = "[{0}] {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $Message
        Add-Content -LiteralPath $logFile -Value $line -Encoding UTF8
    }
    catch {
        # 日志写不进去不影响主流程
    }
}

function Show-Tip([string]$Message, [int]$Icon = 48) {
    if ($DryRun) {
        Write-Output "[DryRun] 提示：$Message"
        return
    }
    try {
        (New-Object -ComObject WScript.Shell).Popup($Message, 25, "FlowMask", $Icon) | Out-Null
    }
    catch {
        Write-Output "提示：$Message"
    }
}

function Test-FlowMask([int]$Port) {
    try {
        $uri = "http://127.0.0.1:{0}/api/v1/health" -f $Port
        $r = Invoke-WebRequest -UseBasicParsing -Uri $uri -TimeoutSec 1
        if ($r.StatusCode -ne 200) { return $false }
        $json = $r.Content | ConvertFrom-Json
        if ($json.PSObject.Properties.Name -contains "service") {
            return ($json.service -eq "FlowMask")
        }
        # 兼容早期没有 service 字段的版本：能返回 version/keywords 就当作 FlowMask
        return (($json.PSObject.Properties.Name -contains "version") -and
            ($json.PSObject.Properties.Name -contains "keywords"))
    }
    catch {
        return $false
    }
}

function Find-FlowMaskPort {
    foreach ($p in $ports) {
        if (Test-FlowMask -Port $p) { return $p }
    }
    return $null
}

function Open-Web([int]$Port) {
    $url = "http://127.0.0.1:{0}/app/" -f $Port
    Write-Log "打开网页：$url"
    if ($DryRun) {
        Write-Output "[DryRun] 将用默认浏览器打开：$url"
        return
    }
    try {
        Start-Process $url
        Write-Output "已打开：$url"
    }
    catch {
        # 浏览器启动失败时不影响服务本身，给出可复制的手动地址
        Write-Log "浏览器自动打开失败：$($_.Exception.Message)"
        Show-Tip "服务已就绪，但浏览器没能自动打开。`n请手动访问：$url" 48
    }
}

if ($DryRun) {
    Write-Output "[DryRun] serverDir=$serverDir"
}

$runningPort = Find-FlowMaskPort
if ($null -ne $runningPort) {
    Write-Log "检测到运行中的 FlowMask：端口 $runningPort"
    Open-Web -Port $runningPort
    exit 0
}

$startCmd = Join-Path $serverDir "scripts\start_local.cmd"
if ($NoStart) {
    $msg = "FlowMask 服务没有在运行。请先双击「FlowMask」启动，或运行 start_local.cmd。"
    Write-Log "未检测到服务，-NoStart 已设置，不自动启动"
    Show-Tip $msg 48
    exit 1
}

if (-not (Test-Path -LiteralPath $startCmd)) {
    $msg = "找不到启动脚本：$startCmd`n请在仓库的 server\scripts 目录下运行本脚本。"
    Write-Log $msg
    Show-Tip $msg 16
    exit 1
}

Write-Log "未检测到运行中的服务，开始后台启动（端口 $defaultPort）"
if ($DryRun) {
    Write-Output "[DryRun] 将执行：start_local.cmd -Port $defaultPort -NoBrowser"
    Write-Output "[DryRun] 完成（未真正启动服务）"
    exit 0
}
# start_local.cmd 自己会等健康检查；这里只负责把它拉起来，然后我们轮询确认
Start-Process -FilePath $startCmd -ArgumentList @("-Port", "$defaultPort", "-NoBrowser") `
    -WorkingDirectory $serverDir -WindowStyle Hidden | Out-Null

$deadline = (Get-Date).AddSeconds(90)
while ((Get-Date) -lt $deadline) {
    Start-Sleep -Milliseconds 800
    if (Test-FlowMask -Port $defaultPort) {
        Write-Log "启动成功，端口 $defaultPort"
        Open-Web -Port $defaultPort
        exit 0
    }
}

$tail = ""
if (Test-Path -LiteralPath (Join-Path $logDir "local.out.log")) {
    $tail = Get-Content -LiteralPath (Join-Path $logDir "local.out.log") -Tail 8 -ErrorAction SilentlyContinue | Out-String
}
$msg = "FlowMask 网页端启动超时（90 秒）。`n可手动运行 scripts\start_local.cmd 查看报错；日志：$logDir"
Write-Log "启动超时。$tail"
Show-Tip $msg 16
exit 1
