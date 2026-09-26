# Mesh local → tmesh → prod ship helper (Windows PowerShell)
#
# ⚠️  DEPRECATED as the release path for app/runtime code.
#     Use:  .\deploy\ship_image.ps1 -Target tmesh|prod
#     See:  docs/ENVIRONMENT_REPRODUCIBILITY.md
#
# docker cp into a running container is NOT reproducible:
#   recreate / compose up 会丢热补文件，且无法对齐 git commit ↔ image digest。
#
# This script remains only for emergency hotfixes or non-image host files.
# After any app hotfix via this script, you MUST follow with ship_image.ps1.
#
# Usage (legacy):
#   .\deploy\ship_changed_files.ps1 -Target tmesh -Files @("app\foo.py")
#   .\deploy\ship_changed_files.ps1 -Target tmesh -SyncApp
#
# Does NOT run Preview / Ask / Publish.
#
# 防漏同步：若 Files 含 app/main.py，会自动附带 app/aggregator.py + app/ingest.py。

param(
  [ValidateSet("tmesh", "prod")]
  [Parameter(Mandatory = $true)]
  [string]$Target,

  [Parameter(Mandatory = $false)]
  [string[]]$Files = @(),

  [switch]$SyncApp,

  [string]$DeployHost = $(if ($env:MESH_DEPLOY_HOST) { $env:MESH_DEPLOY_HOST } else { "104.250.53.182" }),
  [string]$DeployPort = $(if ($env:MESH_DEPLOY_PORT) { $env:MESH_DEPLOY_PORT } else { "22341" })
)

$ErrorActionPreference = "Stop"
$MeshRoot = Split-Path -Parent $PSScriptRoot
if (-not (Test-Path (Join-Path $MeshRoot "app"))) {
  throw "Expected mesh/ root at $MeshRoot"
}

if (-not $SyncApp -and ($Files.Count -eq 0)) {
  throw "Specify -Files and/or -SyncApp"
}

if ($Target -eq "tmesh") {
  $RemoteBase = "/opt/geekpark-tmesh"
  $Containers = @("geekpark-tmesh", "geekpark-tmesh-worker")
  $SmokeUrl = "http://127.0.0.1:8091/"
} else {
  $RemoteBase = "/opt/geekpark-mesh"
  $Containers = @("geekpark-mesh", "geekpark-mesh-worker")
  $SmokeUrl = "http://127.0.0.1:8090/"
}

$sshBase = @("-p", $DeployPort, "-o", "StrictHostKeyChecking=no", "root@$DeployHost")

function Normalize-Rel([string]$rel) {
  return ($rel -replace "\\", "/").TrimStart("./")
}

# --- whole app/ tree ---
if ($SyncApp) {
  $tarLocal = Join-Path $env:TEMP "mesh_app_sync.tgz"
  if (Test-Path $tarLocal) { Remove-Item $tarLocal -Force }
  Push-Location $MeshRoot
  try {
    & tar -czf $tarLocal --exclude=__pycache__ --exclude=*.pyc app
  } finally {
    Pop-Location
  }
  Write-Host "==> SyncApp tar -> $Target ($([math]::Round((Get-Item $tarLocal).Length/1KB)) KB)"
  & scp -P $DeployPort -o StrictHostKeyChecking=no $tarLocal "root@${DeployHost}:/tmp/mesh_app_sync.tgz"
  $containerList = ($Containers -join " ")
  $webCtr = $Containers[0]
  $installApp = @"
set -euo pipefail
BASE='$RemoteBase'
cd "`$BASE"
TS=`$(date +%Y%m%d%H%M%S)
if [ -d app ]; then cp -a app "app.bak.`$TS"; fi
tar -xzf /tmp/mesh_app_sync.tgz
for c in $containerList; do
  docker cp "`$BASE/app/." "`$c:/srv/mesh/app/"
done
docker restart $containerList
sleep 7
curl -sS -o /dev/null -w 'smoke=%{http_code}\n' '$SmokeUrl' || true
docker exec $webCtr python -c "from app.aggregator import should_pre_explode; print('aggregator_ok')"
echo SHIP_$($Target.ToUpper())_APP_OK
"@
  $localSh = Join-Path $env:TEMP "mesh_ship_app.sh"
  [System.IO.File]::WriteAllText($localSh, ($installApp -replace "`r`n", "`n" -replace "`r", "`n"))
  & scp -P $DeployPort -o StrictHostKeyChecking=no $localSh "root@${DeployHost}:/tmp/mesh_ship_app.sh"
  & ssh @sshBase "bash /tmp/mesh_ship_app.sh"
  Write-Host "DONE $Target SyncApp"
}

if ($Files.Count -eq 0) {
  return
}

# Auto-include upload/paste deps when shipping main.py
$normFiles = @($Files | ForEach-Object { Normalize-Rel $_ })
if ($normFiles -contains "app/main.py") {
  foreach ($dep in @("app/aggregator.py", "app/ingest.py")) {
    if ($normFiles -notcontains $dep) {
      Write-Host "  [auto] include $dep (required with main.py)"
      $normFiles += $dep
    }
  }
}

$RemoteTmp = "/tmp/mesh_ship_$(Get-Random)"
Write-Host "==> prepare $RemoteTmp on $DeployHost ($Target)"
& ssh @sshBase "rm -rf $RemoteTmp; mkdir -p $RemoteTmp"

$unixFiles = @()
foreach ($relUnix in $normFiles) {
  $local = Join-Path $MeshRoot ($relUnix -replace "/", [IO.Path]::DirectorySeparatorChar)
  if (-not (Test-Path $local)) { throw "missing local file: $local" }
  $remoteDest = ("$RemoteTmp/$relUnix") -replace '\\', '/'
  $remoteParent = $remoteDest -replace '/[^/]+$', ''
  & ssh @sshBase "mkdir -p '$remoteParent'"
  & scp -P $DeployPort -o StrictHostKeyChecking=no $local "root@${DeployHost}:$remoteDest"
  $unixFiles += $relUnix
  Write-Host "  uploaded $relUnix"
}

$containerList = ($Containers -join " ")
$fileLines = ($unixFiles | ForEach-Object { "  '$_'" }) -join "`n"
$install = @"
set -euo pipefail
REMOTE='$RemoteTmp'
BASE='$RemoteBase'
FILES=(
$fileLines
)
for f in "`${FILES[@]}"; do
  mkdir -p "`$BASE/`$(dirname "`$f")"
  cp "`$REMOTE/`$f" "`$BASE/`$f"
done
for c in $containerList; do
  for f in "`${FILES[@]}"; do
    docker exec "`$c" mkdir -p "/srv/mesh/`$(dirname "`$f")"
    docker cp "`$REMOTE/`$f" "`$c:/srv/mesh/`$f"
  done
done
docker restart $containerList
sleep 7
curl -sS -o /dev/null -w 'smoke=%{http_code}\n' '$SmokeUrl' || true
echo SHIP_$($Target.ToUpper())_OK
"@

$localSh = Join-Path $env:TEMP "mesh_ship_install.sh"
[System.IO.File]::WriteAllText($localSh, ($install -replace "`r`n", "`n" -replace "`r", "`n"))

& scp -P $DeployPort -o StrictHostKeyChecking=no $localSh "root@${DeployHost}:/tmp/mesh_ship_install.sh"
& ssh @sshBase "bash /tmp/mesh_ship_install.sh"
Write-Host "DONE $Target"
