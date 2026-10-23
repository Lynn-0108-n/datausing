# 构建 FlowMask Windows 安装包（Inno Setup 6）与便携 zip。
#
# 用法（任意目录）：
#   powershell -NoProfile -ExecutionPolicy Bypass -File scripts\build_installer.ps1
#   powershell ... -File scripts\build_installer.ps1 -SkipBuild      # 复用已构建的 exe
#
# 前置：
#   1. 已能正常跑 scripts\build_exe.ps1（node/npm + PyInstaller，见 README 第 12 章）；
#   2. 已安装 Inno Setup 6（winget install --id JRSoftware.InnoSetup -e）。
#
# 产物（dist_install/，已被 .gitignore 忽略，不要提交）：
#   FlowMask-Setup-<version>.exe   当前用户安装包（无需管理员权限）
#   FlowMask-Portable-<version>.zip 解压即用的便携版
#
# 退出码：0 成功；1 构建 exe 失败；2 未找到 ISCC；3 编译安装包失败。

[CmdletBinding()]
param(
    [switch]$SkipBuild,
    [string]$OutDir = "dist_install"
)

$ErrorActionPreference = "Stop"
$serverDir = Split-Path -Parent $PSScriptRoot
Set-Location $serverDir

Write-Host "==> FlowMask 安装包构建（server 目录：$serverDir）"

# 1) 版本号：从 app/__init__.py 读 __version__，安装包与 exe 文件属性保持一致
$initPy = Join-Path $serverDir "app\__init__.py"
$match = Select-String -Path $initPy -Pattern '__version__\s*=\s*"([0-9][0-9.]*)"' -Encoding utf8 | Select-Object -First 1
if (-not $match) { throw "读不到 app/__init__.py 里的 __version__" }
$version = $match.Matches[0].Groups[1].Value.TrimEnd('.')
Write-Host "版本号：$version"

# 2) exe（默认先重建；-SkipBuild 时复用 dist_exe\FlowMask.exe）
$exe = Join-Path $serverDir "dist_exe\FlowMask.exe"
if (-not $SkipBuild) {
    & (Join-Path $PSScriptRoot "build_exe.ps1")
    if ($LASTEXITCODE -ne 0) { throw "build_exe.ps1 构建失败（exit $LASTEXITCODE）" }
}
if (-not (Test-Path $exe)) { throw "找不到 $exe；先运行 scripts\build_exe.ps1，或去掉 -SkipBuild" }
$exeSizeMb = [math]::Round(((Get-Item $exe).Length / 1MB), 1)

# 3) 定位 Inno Setup 编译器：PATH → 当前用户安装 → 全局安装
$isccCandidates = @()
$cmd = Get-Command ISCC.exe -ErrorAction SilentlyContinue
if ($cmd) { $isccCandidates += $cmd.Source }
$isccCandidates += (Join-Path $env:LOCALAPPDATA "Programs\Inno Setup 6\ISCC.exe")
$isccCandidates += (Join-Path ${env:ProgramFiles(x86)} "Inno Setup 6\ISCC.exe")
$isccCandidates += (Join-Path $env:ProgramFiles "Inno Setup 6\ISCC.exe")
$iscc = $isccCandidates | Where-Object { $_ -and (Test-Path $_) } | Select-Object -First 1
if (-not $iscc) {
    Write-Host ""
    Write-Host "找不到 Inno Setup 的 ISCC.exe。安装方式（任选其一）：" -ForegroundColor Yellow
    Write-Host "  winget install --id JRSoftware.InnoSetup -e"
    Write-Host "  或从 https://jrsoftware.org/isdl.php 下载安装（仅当前用户即可）"
    Write-Host "装好后重新运行本脚本；exe 已构建完成（$exeSizeMb MB）。"
    exit 2
}

# 4) 编译安装包
Write-Host "==> 使用 ISCC：$iscc"
$iss = Join-Path $serverDir "installer\FlowMask.iss"
& $iscc $iss
if ($LASTEXITCODE -ne 0) { throw "ISCC 编译失败（exit $LASTEXITCODE）" }

$outDirPath = Join-Path $serverDir $OutDir
$setup = Join-Path $outDirPath "FlowMask-Setup-$version.exe"
if (-not (Test-Path $setup)) { throw "编译成功但没有预期产物 $setup" }
$setupSizeMb = [math]::Round(((Get-Item $setup).Length / 1MB), 1)

# 5) 便携 zip（免安装：解压即用，不写注册表）
$zip = Join-Path $outDirPath "FlowMask-Portable-$version.zip"
if (Test-Path $zip) { Remove-Item $zip -Force }
$readme = Join-Path $serverDir "installer\使用说明.txt"
Compress-Archive -Path $exe, $readme -DestinationPath $zip -Force
$zipSizeMb = [math]::Round(((Get-Item $zip).Length / 1MB), 1)

Write-Host ""
Write-Host "构建完成：" -ForegroundColor Green
Write-Host "  exe      ：$exe（$exeSizeMb MB）"
Write-Host "  安装包   ：$setup（$setupSizeMb MB）"
Write-Host "  便携 zip ：$zip（$zipSizeMb MB）"
Write-Host ""
Write-Host "说明：安装包按当前用户安装到 %LOCALAPPDATA%\Programs\FlowMask，无需管理员权限。"
Write-Host "产物目录 dist_install/ 已被 .gitignore 忽略，不要提交。"
exit 0
