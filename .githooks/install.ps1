# 把仓库内 hook 安装到本机 .git/hooks（不改 git config）
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$HooksDir = Join-Path $Root ".git\hooks"
New-Item -ItemType Directory -Force -Path $HooksDir | Out-Null
Copy-Item -Force (Join-Path $PSScriptRoot "pre-commit") (Join-Path $HooksDir "pre-commit")
Copy-Item -Force (Join-Path $PSScriptRoot "pre-commit.ps1") (Join-Path $HooksDir "pre-commit.ps1")
Write-Host "installed: .git/hooks/pre-commit (+ pre-commit.ps1)"

# 冒烟：无暂存前端文件时应直接放行
& (Join-Path $PSScriptRoot "pre-commit.ps1")
if ($LASTEXITCODE -ne 0) { throw "pre-commit.ps1 smoke failed" }
Write-Host "smoke: ok (no staged frontend -> skip)"
