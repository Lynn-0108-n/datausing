<#
.SYNOPSIS
  从 Windows 一键上传并更新 Linux 上的 netmon 采集端。
.EXAMPLE
  .\upload-netmon.ps1 -Server 192.168.161.128 -User wuki
  .\upload-netmon.ps1 -Server 192.168.161.128 -DryRun
#>
param(
  [Parameter(Mandatory = $true)][string]$Server,
  [string]$User = "wuki",
  [string]$RemoteDir = "~",
  [string]$Binary = "",
  [string]$Config = "~/config.toml",
  [string]$Service = "netmon",
  [int]$Port = 8080,
  [switch]$DryRun
)

$ErrorActionPreference = "Stop"
# 兼容不同调用方式：$PSScriptRoot 为空时用脚本自身路径推导
$scriptDir = if ($PSScriptRoot) { $PSScriptRoot } else { Split-Path -Parent $MyInvocation.MyCommand.Path }
$script = Join-Path $scriptDir "update-netmon.sh"
if ([string]::IsNullOrWhiteSpace($Binary)) {
  $Binary = Join-Path (Split-Path -Parent $scriptDir) "netmon-linux"
}
if (-not (Test-Path -LiteralPath $Binary)) { throw "找不到本地二进制：$Binary" }
if (-not (Test-Path -LiteralPath $script)) { throw "找不到脚本：$script" }

# Windows 检出可能把 .sh 变成 CRLF，会导致 Linux 报 "bad interpreter"：
# 这里先转成 LF 的临时副本再上传。
$tmpDir = Join-Path $env:TEMP "metd-deploy"
New-Item -ItemType Directory -Force -Path $tmpDir | Out-Null
$lfScript = Join-Path $tmpDir "update-netmon.sh"
$text = [System.IO.File]::ReadAllText($script).Replace("`r`n", "`n").Replace("`r", "`n")
[System.IO.File]::WriteAllText($lfScript, $text, (New-Object System.Text.UTF8Encoding($false)))


$target = "$User@$Server"

# 参数校验：这些值会进入远端 shell 命令，禁止空格/引号/分号等可注入字符
$localChecks = @(
  @{ Name = "Server"; Value = $Server; Pattern = '^[A-Za-z0-9._-]+$' },
  @{ Name = "User"; Value = $User; Pattern = '^[A-Za-z0-9._-]+$' },
  @{ Name = "RemoteDir"; Value = $RemoteDir; Pattern = '^[A-Za-z0-9._~/-]+$' },
  @{ Name = "Config"; Value = $Config; Pattern = '^[A-Za-z0-9._~/-]+$' },
  @{ Name = "Service"; Value = $Service; Pattern = '^[A-Za-z0-9._-]+$' }
)
foreach ($c in $localChecks) {
  if ($c.Value -notmatch $c.Pattern) {
    throw "参数 $($c.Name) 含非法字符：$($c.Value)（只允许字母、数字、._~/-）"
  }
}

$remoteCmd = "cd $RemoteDir && mv -f netmon-linux netmon-linux.new && chmod +x update-netmon.sh && ./update-netmon.sh -b ./netmon-linux.new -c $Config -s $Service -p $Port"

Write-Host "目标服务器：$target" -ForegroundColor Cyan
Write-Host "本地二进制：$Binary"
if ($DryRun) {
  Write-Host "  scp `"$Binary`" `"$lfScript`" ${target}:$RemoteDir/" -ForegroundColor DarkGray
  Write-Host "  ssh -t $target `"$remoteCmd`"" -ForegroundColor DarkGray
  Write-Host "（演练模式：未实际执行）" -ForegroundColor Yellow
} else {
  # 参数以数组传参，不把命令字符串交给 PowerShell 二次解析（避免注入）
  & scp -- $Binary $lfScript "${target}:$RemoteDir/"
  if ($LASTEXITCODE -ne 0) { throw "scp 失败（退出码 $LASTEXITCODE）" }
  & ssh -t -- $target $remoteCmd
  if ($LASTEXITCODE -ne 0) { throw "ssh 失败（退出码 $LASTEXITCODE）" }
  Write-Host "上传并更新完成；如失败可运行：ssh $target '$RemoteDir/update-netmon.sh --rollback'" -ForegroundColor Green
}