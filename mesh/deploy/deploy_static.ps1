# 热更新前端静态资源到线上容器（不改镜像、不重启）。
# 在 mesh 目录执行：  powershell -File deploy/deploy_static.ps1
# 可选环境变量：MESH_DEPLOY_HOST / MESH_DEPLOY_PORT / MESH_DEPLOY_USER / MESH_BASE_URL

$ErrorActionPreference = "Stop"
$MeshRoot = Split-Path -Parent $PSScriptRoot
Set-Location $MeshRoot

$DeployHost = if ($env:MESH_DEPLOY_HOST) { $env:MESH_DEPLOY_HOST } else { "104.250.53.182" }
$DeployPort = if ($env:MESH_DEPLOY_PORT) { $env:MESH_DEPLOY_PORT } else { "22341" }
$DeployUser = if ($env:MESH_DEPLOY_USER) { $env:MESH_DEPLOY_USER } else { "root" }
$RemoteBase = if ($env:MESH_DEPLOY_REMOTE_BASE) { $env:MESH_DEPLOY_REMOTE_BASE } else { "/opt/geekpark-mesh" }
$Container = if ($env:MESH_CONTAINER) { $env:MESH_CONTAINER } else { "geekpark-mesh" }
$BaseUrl = if ($env:MESH_BASE_URL) { $env:MESH_BASE_URL } else { "https://mesh.geekpark.ai" }

$ssh = @("-p", $DeployPort, "-o", "StrictHostKeyChecking=no", "${DeployUser}@${DeployHost}")
$scp = @("-P", $DeployPort, "-o", "StrictHostKeyChecking=no")

$files = @(
    @{ Local = "app\static\app.js";        Remote = "$RemoteBase/app/static/app.js";        Container = "/srv/mesh/app/static/app.js" },
    @{ Local = "app\static\console.js";    Remote = "$RemoteBase/app/static/console.js";    Container = "/srv/mesh/app/static/console.js" },
    @{ Local = "app\static\dialog.js";     Remote = "$RemoteBase/app/static/dialog.js";     Container = "/srv/mesh/app/static/dialog.js" },
    @{ Local = "app\static\style.css";     Remote = "$RemoteBase/app/static/style.css";     Container = "/srv/mesh/app/static/style.css" },
    @{ Local = "app\static\console.css";   Remote = "$RemoteBase/app/static/console.css";   Container = "/srv/mesh/app/static/console.css" },
    @{ Local = "app\templates\base.html";  Remote = "$RemoteBase/app/templates/base.html";  Container = "/srv/mesh/app/templates/base.html" },
    @{ Local = "app\templates\issue.html"; Remote = "$RemoteBase/app/templates/issue.html"; Container = "/srv/mesh/app/templates/issue.html" },
    @{ Local = "app\templates\issue_console.html"; Remote = "$RemoteBase/app/templates/issue_console.html"; Container = "/srv/mesh/app/templates/issue_console.html" }
)

Write-Host "==> 发布前检查（本地）"
python scripts/check_frontend.py
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "==> 上传到服务器 $DeployUser@${DeployHost}:$DeployPort"
foreach ($f in $files) {
    $localPath = Join-Path $MeshRoot $f.Local
    if (-not (Test-Path $localPath)) {
        Write-Warning "跳过缺失文件 $($f.Local)"
        continue
    }
    & scp @scp $localPath "${DeployUser}@${DeployHost}:$($f.Remote)"
    if ($LASTEXITCODE -ne 0) { throw "scp 失败: $($f.Local)" }
    Write-Host "  uploaded $($f.Local)"
}

Write-Host "==> 复制进容器 $Container"
$dockerCmd = ($files | ForEach-Object {
    if (Test-Path (Join-Path $MeshRoot $_.Local)) {
        "docker cp $($_.Remote) ${Container}:$($_.Container)"
    }
}) -join " && "
& ssh @ssh "set -e; $dockerCmd; echo container_ok"
if ($LASTEXITCODE -ne 0) { throw "docker cp 失败" }

Write-Host "==> 发布后检查（线上）"
python scripts/check_frontend.py --remote $BaseUrl
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host ""
Write-Host "DEPLOY_STATIC_OK  已热更新前端；用户需强制刷新 (Ctrl+F5) 或等待缓存过期。"
