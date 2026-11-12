<#
.SYNOPSIS
  FlowMask 0.27.2 全功能版一键启动：L2 识别服务 + L3 本地大模型 + NER 实体识别 + OCR 图片识别

.DESCRIPTION
  使用包内便携运行时（runtime\python.exe）启动：
    1) L2 识别服务（RoBERTa-wwm ONNX，10 类敏感信息）  端口 8191
    2) L3 判定服务（Qwen2.5-1.5B-Instruct，bf16）        端口 8192
    3) 主程序（uvicorn app.main:app）                    端口 8095
  若对应端口已有本包上次启动的服务则复用，否则用包内运行时自启（默认 8191/8192，避免与本机其他项目冲突）。
  启动成功后自动打开浏览器 http://127.0.0.1:8095/app/

.PARAMETER Port
  主程序端口，默认 8095，也可用环境变量 FLOWMASK_PORT 覆盖。

.PARAMETER NoBrowser
  不自动打开浏览器。

.PARAMETER Force
  先停止本包上一次启动、由 logs\*.pid 记录的进程再启动。

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\full-start.ps1
  powershell -ExecutionPolicy Bypass -File scripts\full-start.ps1 -Port 8096 -NoBrowser
#>
param(
    [int]$Port = 0,
    [switch]$NoBrowser,
    [switch]$Force
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

$Root = Split-Path -Parent $PSScriptRoot
$Py = Join-Path $Root 'runtime\python.exe'
$Server = Join-Path $Root 'server'
$Logs = Join-Path $Root 'logs'
$Data = Join-Path $Root 'data'
$FrontendDir = Join-Path $Root 'web\dist'

if (-not (Test-Path $Py)) {
    Write-Host "[错误] 缺少便携运行时：$Py" -ForegroundColor Red
    exit 1
}
if (-not (Test-Path (Join-Path $Server 'app\main.py'))) {
    Write-Host "[错误] 缺少服务端代码：$Server" -ForegroundColor Red
    exit 1
}
# 前端构建产物缺失时 FastAPI 不会挂载 /app，主程序看似启动成功、浏览器却打开 404。
# 这里提前拦截，避免"启动了但打不开界面"的误导。
if (-not (Test-Path (Join-Path $FrontendDir 'index.html'))) {
    Write-Host "[错误] 缺少前端构建产物：$(Join-Path $FrontendDir 'index.html')" -ForegroundColor Red
    Write-Host "       请使用完整解压包，或先构建前端（web\dist）后再启动。" -ForegroundColor Yellow
    exit 1
}
New-Item -ItemType Directory -Force -Path $Logs, $Data | Out-Null

# ---------------- 端口 ----------------
$mainPort = if ($Port -gt 0) { $Port } elseif ($env:FLOWMASK_PORT) { [int]$env:FLOWMASK_PORT } else { 8095 }
$l2Port = if ($env:FLOWMASK_L2_PORT) { [int]$env:FLOWMASK_L2_PORT } else { 8191 }
$l3Port = if ($env:FLOWMASK_L3_PORT) { [int]$env:FLOWMASK_L3_PORT } else { 8192 }

# ---------------- 功能开关：全部启用 ----------------
$env:FLOWMASK_HOST = '127.0.0.1'
# 收敛 OpenBLAS/OMP 线程数：线程缓冲在内存紧张时申请失败会导致 OCR/numpy 报错
$env:OPENBLAS_NUM_THREADS = '1'
$env:OMP_NUM_THREADS = '1'
$env:FLOWMASK_PORT = "$mainPort"
$env:FLOWMASK_DATA_DIR = $Data
$env:FLOWMASK_FRONTEND_DIR = $FrontendDir
$env:FLOWMASK_ENABLE_L2 = '1'
$env:FLOWMASK_ENABLE_L3 = '1'
$env:FLOWMASK_ENABLE_NER = '1'
$env:FLOWMASK_OCR_ENABLED = '1'
$env:FLOWMASK_NER_BACKEND = 'token'
$env:FLOWMASK_NER_MODEL_DIR = Join-Path $Server 'models\bert4ner'
$env:FLOWMASK_NER_DEVICE = 'cpu'
$env:FLOWMASK_L2_PORT = "$l2Port"
$env:FLOWMASK_L2_ENDPOINT = "http://127.0.0.1:$l2Port/v1/classify"
$env:FLOWMASK_L3_PORT = "$l3Port"
$env:FLOWMASK_L3_ENDPOINT = "http://127.0.0.1:$l3Port/v1/judge"
if (-not $env:FLOWMASK_L2_MODE) { $env:FLOWMASK_L2_MODE = 'onnx' }
$env:FLOWMASK_L2_MODEL_DIR = Join-Path $Server 'models\bert-sens-onnx'
if (-not $env:FLOWMASK_L3_MODE) { $env:FLOWMASK_L3_MODE = 'instruct' }
$env:FLOWMASK_L3_MODEL_DIR = Join-Path $Server 'models\qwen2.5-1.5b-instruct'
if (-not $env:FLOWMASK_L3_DEVICE) { $env:FLOWMASK_L3_DEVICE = 'auto' }
# CPU 上用 fp32 加载 1.5B 模型在 transformers 5.x 下会生成乱码，bf16 已验证正常且省一半内存
if (-not $env:FLOWMASK_L3_DTYPE) { $env:FLOWMASK_L3_DTYPE = 'bf16' }
# 本地 1.5B 模型在 CPU 上单次推理约 20-30 秒，默认 5 秒超时必然降级，这里放宽到 120 秒
if (-not $env:FLOWMASK_L3_TIMEOUT_S) { $env:FLOWMASK_L3_TIMEOUT_S = '120' }

# 记录端口，供停止脚本使用
@{ main = $mainPort; l2 = $l2Port; l3 = $l3Port; root = $Root } |
    ConvertTo-Json | Set-Content -Path (Join-Path $Logs 'ports.json') -Encoding UTF8

# ---------------- 工具函数 ----------------
function Test-Http {
    param([string]$Url, [int]$TimeoutSec = 3)
    try {
        $resp = Invoke-WebRequest -UseBasicParsing -Uri $Url -TimeoutSec $TimeoutSec
        # 健康检查必须严格要求 200：某些端口被别的程序占用时也会返回 4xx，
        # 旧逻辑把 <500 都当成"已就绪"，会误判为 FlowMask 正在运行。
        return ($resp.StatusCode -eq 200)
    } catch { return $false }
}

function Test-AppPage {
    param([string]$Url, [int]$TimeoutSec = 5)
    try {
        $resp = Invoke-WebRequest -UseBasicParsing -Uri $Url -TimeoutSec $TimeoutSec
        return ($resp.StatusCode -eq 200)
    } catch { return $false }
}

function Wait-AppPage {
    param([string]$Url, [int]$TimeoutSec, [string]$Name)
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    while ((Get-Date) -lt $deadline) {
        if (Test-AppPage -Url $Url) { return $true }
        Start-Sleep -Milliseconds 500
    }
    Write-Host "[警告] $Name 在 ${TimeoutSec} 秒内未返回页面：$Url" -ForegroundColor Yellow
    return $false
}

function Stop-MainOnPort {
    <# 停止端口上属于本包目录的主程序进程；返回端口是否已释放。 #>
    param([int]$Port)
    $procIds = @()
    $conns = Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue
    foreach ($c in @($conns)) {
        if ($c) { $procIds += [int]$c.OwningProcess }
    }
    $mainPidFile = Join-Path $Logs 'main.pid'
    if (Test-Path $mainPidFile) {
        $raw = Get-Content $mainPidFile -ErrorAction SilentlyContinue | Select-Object -First 1
        $procId = 0
        if ([int]::TryParse([string]$raw, [ref]$procId)) { $procIds += $procId }
    }
    foreach ($procId in ($procIds | Sort-Object -Unique)) {
        $p = Get-Process -Id $procId -ErrorAction SilentlyContinue
        if ($p -and $p.Path -and $p.Path.StartsWith($Root, [System.StringComparison]::OrdinalIgnoreCase)) {
            Stop-Process -Id $procId -Force -ErrorAction SilentlyContinue
            Write-Host ("[停止] 旧的 FlowMask 主程序（PID " + $procId + "）")
        }
    }
    Remove-Item $mainPidFile -Force -ErrorAction SilentlyContinue
    for ($i = 0; $i -lt 40; $i++) {
        $left = Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue
        if (-not $left) { return $true }
        Start-Sleep -Milliseconds 250
    }
    return $false
}

function Test-PortListening {
    param([int]$Port)
    $conns = Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue
    return ($null -ne $conns -and @($conns).Count -gt 0)
}

function Wait-Http {
    param([string]$Url, [int]$TimeoutSec, [string]$Name)
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    while ((Get-Date) -lt $deadline) {
        if (Test-Http -Url $Url) { return $true }
        Start-Sleep -Milliseconds 500
    }
    Write-Host "[警告] $Name 在 ${TimeoutSec} 秒内未就绪：$Url" -ForegroundColor Yellow
    return $false
}

function Stop-OwnService {
    param([string]$PidFile)
    if (-not (Test-Path $PidFile)) { return }
    $raw = Get-Content $PidFile -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($raw) {
        $procId = 0
        if ([int]::TryParse($raw, [ref]$procId)) {
            $p = Get-Process -Id $procId -ErrorAction SilentlyContinue
            if ($p -and $p.Path -and $p.Path.StartsWith($Root, [System.StringComparison]::OrdinalIgnoreCase)) {
                Stop-Process -Id $procId -Force -ErrorAction SilentlyContinue
            }
        }
    }
    Remove-Item $PidFile -Force -ErrorAction SilentlyContinue
}

function Start-OwnService {
    param([string]$Name, [string]$Module, [int]$PortForLog, [string]$PidFile, [string]$OutLog, [string]$ErrLog)
    Write-Host ("[启动] " + $Name + "（端口 " + $PortForLog + "）...")
    $p = Start-Process -FilePath $Py -ArgumentList @('-m', $Module) -WorkingDirectory $Server `
        -WindowStyle Hidden -RedirectStandardOutput $OutLog -RedirectStandardError $ErrLog -PassThru
    Set-Content -Path $PidFile -Value $p.Id -Encoding ASCII
    return $p
}

# ---------------- 主程序已在运行则直接打开 ----------------
if (Test-Http -Url "http://127.0.0.1:$mainPort/api/v1/health") {
    if (Test-AppPage -Url "http://127.0.0.1:$mainPort/app/") {
        Write-Host ("[信息] FlowMask 已在端口 " + $mainPort + " 运行。") -ForegroundColor Green
        if (-not $NoBrowser) { Start-Process ("http://127.0.0.1:" + $mainPort + "/app/") }
        exit 0
    }
    # 端口上有本包主程序，但它启动时 web\dist 可能还不存在/不完整，导致没有挂载 /app。
    # 旧逻辑此时直接打开浏览器 → 用户只看到 404。这里自动重启主程序修复。
    Write-Host ("[提示] 端口 " + $mainPort + " 上的 FlowMask 主程序没有加载前端页面（/app/ 返回 404）。") -ForegroundColor Yellow
    Write-Host "       通常是该进程启动时 web\dist 尚未就绪（例如前端正在重新构建），或旧版本残留。" -ForegroundColor Yellow
    Write-Host "[处理] 正在停止旧主程序并重新启动……" -ForegroundColor Yellow
    if (-not (Stop-MainOnPort -Port $mainPort)) {
        Write-Host ("[错误] 无法释放端口 " + $mainPort + "（可能被其他程序占用）。请手动结束该进程后重试。") -ForegroundColor Red
        exit 1
    }
}

# ---------------- L2 识别服务 ----------------
$l2Pid = Join-Path $Logs 'l2.pid'
if ($Force) { Stop-OwnService -PidFile $l2Pid }
if (Test-Http -Url "http://127.0.0.1:$l2Port/health") {
    Write-Host ("[复用] L2 识别服务已在端口 " + $l2Port + " 运行。") -ForegroundColor Green
} elseif (Test-PortListening -Port $l2Port) {
    Write-Host ("[提示] 端口 " + $l2Port + " 已被其他程序占用且健康检查失败，跳过自启。") -ForegroundColor Yellow
} else {
    Start-OwnService -Name 'L2 识别服务（ONNX RoBERTa）' -Module 'l2_service' -PortForLog $l2Port `
        -PidFile $l2Pid -OutLog (Join-Path $Logs 'l2.log') -ErrLog (Join-Path $Logs 'l2.err.log') | Out-Null
    if (-not (Wait-Http -Url "http://127.0.0.1:$l2Port/health" -TimeoutSec 120 -Name 'L2 识别服务')) {
        Write-Host "[提示] L2 服务未就绪，主程序将以规则模式继续，详见 logs\l2.err.log" -ForegroundColor Yellow
    }
}

# ---------------- L3 判定服务 ----------------
$l3Pid = Join-Path $Logs 'l3.pid'
if ($Force) { Stop-OwnService -PidFile $l3Pid }
if (Test-Http -Url "http://127.0.0.1:$l3Port/health") {
    Write-Host ("[复用] L3 判定服务已在端口 " + $l3Port + " 运行。") -ForegroundColor Green
} elseif (Test-PortListening -Port $l3Port) {
    Write-Host ("[提示] 端口 " + $l3Port + " 已被其他程序占用且健康检查失败，跳过自启。") -ForegroundColor Yellow
} else {
    Start-OwnService -Name 'L3 判定服务（Qwen2.5-1.5B-Instruct）' -Module 'l3_service' -PortForLog $l3Port `
        -PidFile $l3Pid -OutLog (Join-Path $Logs 'l3.log') -ErrLog (Join-Path $Logs 'l3.err.log') | Out-Null
    if (-not (Wait-Http -Url "http://127.0.0.1:$l3Port/health" -TimeoutSec 120 -Name 'L3 判定服务')) {
        Write-Host "[提示] L3 服务未就绪，详见 logs\l3.err.log" -ForegroundColor Yellow
    } else {
        Write-Host '[预热] 正在加载 L3 本地大模型，首次启动约 1-3 分钟，请稍候...'
        $warmBytes = [System.Text.Encoding]::UTF8.GetBytes('{"text":"邮箱 test@example.com，手机号 13800138000","purpose":"public-sharing"}')
        $warmOk = $false
        $deadline = (Get-Date).AddSeconds(300)
        while ((Get-Date) -lt $deadline) {
            try {
                $r = Invoke-RestMethod -Uri "http://127.0.0.1:$l3Port/v1/judge" -Method Post `
                    -ContentType 'application/json; charset=utf-8' -Body $warmBytes -TimeoutSec 120
                if ($r -and ($r.degraded -ne $true)) { $warmOk = $true; break }
            } catch { }
            Start-Sleep -Seconds 3
        }
        if ($warmOk) { Write-Host '[预热] L3 模型加载完成。' -ForegroundColor Green }
        else { Write-Host '[警告] L3 模型预热未成功，首次检测可能较慢或降级，详见 logs\l3.err.log' -ForegroundColor Yellow }
    }
}

# ---------------- 主程序 ----------------
$mainPid = Join-Path $Logs 'main.pid'
if ($Force) { Stop-OwnService -PidFile $mainPid }
Write-Host ("[启动] FlowMask 主程序（端口 " + $mainPort + "）...")
$mp = Start-Process -FilePath $Py `
    -ArgumentList @('-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', "$mainPort") `
    -WorkingDirectory $Server -WindowStyle Hidden `
    -RedirectStandardOutput (Join-Path $Logs 'server.log') `
    -RedirectStandardError (Join-Path $Logs 'server.err.log') -PassThru
Set-Content -Path $mainPid -Value $mp.Id -Encoding ASCII

if (-not (Wait-Http -Url "http://127.0.0.1:$mainPort/api/v1/health" -TimeoutSec 300 -Name 'FlowMask 主程序')) {
    Write-Host '[错误] 主程序启动失败，logs\server.err.log 末尾：' -ForegroundColor Red
    Get-Content (Join-Path $Logs 'server.err.log') -Tail 20 -ErrorAction SilentlyContinue
    exit 1
}

# 健康检查只能证明 API 起来了；再确认 /app/ 真的可打开，避免"显示启动成功但界面 404"。
if (-not (Wait-AppPage -Url "http://127.0.0.1:$mainPort/app/" -TimeoutSec 15 -Name 'FlowMask 前端页面')) {
    Write-Host '[错误] 主程序已运行，但前端页面 /app/ 无法访问。' -ForegroundColor Red
    Write-Host ('       请确认前端构建产物完整：' + (Join-Path $FrontendDir 'index.html')) -ForegroundColor Yellow
    Write-Host '       若刚更新过前端，请重新解压完整包或重新构建 web\dist。' -ForegroundColor Yellow
    Get-Content (Join-Path $Logs 'server.err.log') -Tail 20 -ErrorAction SilentlyContinue
    exit 1
}

Write-Host ''
Write-Host '==============================================' -ForegroundColor Cyan
Write-Host ' FlowMask 已启动（L2 / L3 / NER / OCR 全部启用）' -ForegroundColor Green
Write-Host (' 界面： http://127.0.0.1:' + $mainPort + '/app/')
Write-Host (' 接口： http://127.0.0.1:' + $mainPort + '/docs')
Write-Host (' 前端： ' + $FrontendDir)
Write-Host (' 数据： ' + $Data)
Write-Host (' 日志： ' + $Logs)
Write-Host ' 停止： 双击「停止 FlowMask.cmd」'
Write-Host '==============================================' -ForegroundColor Cyan

if (-not $NoBrowser) { Start-Process ("http://127.0.0.1:" + $mainPort + "/app/") }
exit 0
