# 将 eval 脚本同步到生产并运行（PowerShell，在 mesh/ 目录）
$ErrorActionPreference = "Stop"
$DeployHost = if ($env:MESH_DEPLOY_HOST) { $env:MESH_DEPLOY_HOST } else { "104.250.53.182" }
$DeployPort = if ($env:MESH_DEPLOY_PORT) { $env:MESH_DEPLOY_PORT } else { "22341" }
$DeployUser = if ($env:MESH_DEPLOY_USER) { $env:MESH_DEPLOY_USER } else { "root" }
$RemoteBase = if ($env:MESH_DEPLOY_REMOTE_BASE) { $env:MESH_DEPLOY_REMOTE_BASE } else { "/opt/geekpark-mesh" }
$Container = if ($env:MESH_CONTAINER) { $env:MESH_CONTAINER } else { "geekpark-mesh" }

$ssh = @("-p", $DeployPort, "-o", "StrictHostKeyChecking=no", "${DeployUser}@${DeployHost}")

Write-Host "==> 上传 eval/"
scp @("-P", $DeployPort, "-o", "StrictHostKeyChecking=no", "-r", "eval", "${DeployUser}@${DeployHost}:${RemoteBase}/")

Write-Host "==> 容器内运行 prod 验收"
& ssh @ssh "set -e; docker cp ${RemoteBase}/eval ${Container}:/srv/mesh/eval; docker exec $Container python eval/run_final_eval.py --corpus prod --e2e --followup --sse"

Write-Host "==> 拉回报告"
scp @("-P", $DeployPort, "-o", "StrictHostKeyChecking=no", "${DeployUser}@${DeployHost}:${RemoteBase}/mesh/eval/reports/FINAL_ACCEPTANCE_REPORT.md", "eval/reports/FINAL_ACCEPTANCE_REPORT.prod.md") 2>$null
Write-Host "DONE"
