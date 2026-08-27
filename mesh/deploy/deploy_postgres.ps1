# 生产部署 Docker PostgreSQL + 迁移
# 在 mesh 目录：powershell -File deploy/deploy_postgres.ps1

$ErrorActionPreference = "Stop"
$MeshRoot = Split-Path -Parent $PSScriptRoot
Set-Location $MeshRoot

$DeployHost = if ($env:MESH_DEPLOY_HOST) { $env:MESH_DEPLOY_HOST } else { "104.250.53.182" }
$DeployPort = if ($env:MESH_DEPLOY_PORT) { $env:MESH_DEPLOY_PORT } else { "22341" }
$DeployUser = if ($env:MESH_DEPLOY_USER) { $env:MESH_DEPLOY_USER } else { "root" }
$RemoteBase = if ($env:MESH_DEPLOY_REMOTE_BASE) { $env:MESH_DEPLOY_REMOTE_BASE } else { "/opt/geekpark-mesh" }

$ssh = @("-p", $DeployPort, "-o", "StrictHostKeyChecking=no", "${DeployUser}@${DeployHost}")
$scp = @("-P", $DeployPort, "-o", "StrictHostKeyChecking=no")

Write-Host "==> 打包上传"
$tarLocal = Join-Path $env:TEMP "mesh-pg-deploy.tgz"
if (Test-Path $tarLocal) { Remove-Item $tarLocal -Force }
& tar -czf $tarLocal -C $MeshRoot app deploy/migrate_to_postgres.py deploy/docker-entry.sh deploy/POSTGRES.md Dockerfile docker-compose.yml requirements.txt
if ($LASTEXITCODE -ne 0) { throw "tar failed" }
& scp @scp $tarLocal "${DeployUser}@${DeployHost}:$RemoteBase/mesh-pg-deploy.tgz"
if ($LASTEXITCODE -ne 0) { throw "scp tar failed" }

$remoteSh = Join-Path $MeshRoot "deploy\mesh-pg-remote.sh"
& scp @scp $remoteSh "${DeployUser}@${DeployHost}:$RemoteBase/mesh-pg-remote.sh"
if ($LASTEXITCODE -ne 0) { throw "scp script failed" }

& ssh @ssh "sed -i 's/\r$//' $RemoteBase/mesh-pg-remote.sh && bash $RemoteBase/mesh-pg-remote.sh"
if ($LASTEXITCODE -ne 0) { throw "remote deploy failed" }
Write-Host "DEPLOY_POSTGRES_OK"
