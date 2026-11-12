<#
.SYNOPSIS
  停止由「启动 FlowMask.cmd / full-start.ps1」启动的 FlowMask 主程序、L2 与 L3 服务。

.DESCRIPTION
  1) 读取 logs\*.pid，仅停止进程路径属于本包目录的进程（不会误杀其他 FlowMask 实例）；
  2) 兜底：按 logs\ports.json 记录的端口检查，占用进程属于本包目录时一并停止。
#>
param()

$Root = Split-Path -Parent $PSScriptRoot
$Logs = Join-Path $Root 'logs'
$PortsFile = Join-Path $Logs 'ports.json'

$ports = @()
if (Test-Path $PortsFile) {
    try {
        $cfg = Get-Content $PortsFile -Raw | ConvertFrom-Json
        $ports = @($cfg.main, $cfg.l2, $cfg.l3) | Where-Object { $_ }
    } catch { }
}

function Stop-FromPidFile {
    param([string]$PidFile, [string]$Label)
    if (-not (Test-Path $PidFile)) { return }
    $raw = Get-Content $PidFile -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($raw) {
        $procId = 0
        if ([int]::TryParse($raw, [ref]$procId)) {
            $p = Get-Process -Id $procId -ErrorAction SilentlyContinue
            if ($p -and $p.Path -and $p.Path.StartsWith($Root, [System.StringComparison]::OrdinalIgnoreCase)) {
                Stop-Process -Id $procId -Force -ErrorAction SilentlyContinue
                Write-Host ("[停止] " + $Label + "（PID " + $procId + "）")
            }
        }
    }
    Remove-Item $PidFile -Force -ErrorAction SilentlyContinue
}

# 先主程序，再 L3、L2
Stop-FromPidFile -PidFile (Join-Path $Logs 'main.pid') -Label '主程序'
Start-Sleep -Milliseconds 500
Stop-FromPidFile -PidFile (Join-Path $Logs 'l3.pid') -Label 'L3 判定服务'
Stop-FromPidFile -PidFile (Join-Path $Logs 'l2.pid') -Label 'L2 识别服务'

# 兜底：端口仍被占用且进程属于本包目录
foreach ($port in $ports) {
    $conns = Get-NetTCPConnection -State Listen -LocalPort ([int]$port) -ErrorAction SilentlyContinue
    foreach ($c in @($conns)) {
        if (-not $c) { continue }
        $p = Get-Process -Id $c.OwningProcess -ErrorAction SilentlyContinue
        if ($p -and $p.Path -and $p.Path.StartsWith($Root, [System.StringComparison]::OrdinalIgnoreCase)) {
            Stop-Process -Id $c.OwningProcess -Force -ErrorAction SilentlyContinue
            Write-Host ("[停止] 端口 " + $port + " 占用进程（PID " + $c.OwningProcess + "）")
        }
    }
}

Write-Host '[完成] FlowMask 已停止。' -ForegroundColor Green
exit 0
