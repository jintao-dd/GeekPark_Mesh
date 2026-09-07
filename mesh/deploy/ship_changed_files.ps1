# Mesh local → tmesh → prod ship helper (Windows PowerShell)
# Usage:
#   .\deploy\ship_changed_files.ps1 -Target tmesh -Files @(
#     "app\eval_dashboard.py",
#     "app\templates\eval_dashboard.html"
#   )
#   .\deploy\ship_changed_files.ps1 -Target prod -Files @(...)   # only after tmesh OK
#
# Does NOT run Preview / Ask / Publish.

param(
  [ValidateSet("tmesh", "prod")]
  [Parameter(Mandatory = $true)]
  [string]$Target,

  [Parameter(Mandatory = $true)]
  [string[]]$Files,

  [string]$DeployHost = $(if ($env:MESH_DEPLOY_HOST) { $env:MESH_DEPLOY_HOST } else { "104.250.53.182" }),
  [string]$DeployPort = $(if ($env:MESH_DEPLOY_PORT) { $env:MESH_DEPLOY_PORT } else { "22341" })
)

$ErrorActionPreference = "Stop"
$MeshRoot = Split-Path -Parent $PSScriptRoot
if (-not (Test-Path (Join-Path $MeshRoot "app"))) {
  throw "Expected mesh/ root at $MeshRoot"
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

$RemoteTmp = "/tmp/mesh_ship_$(Get-Random)"
$sshBase = @("-p", $DeployPort, "-o", "StrictHostKeyChecking=no", "root@$DeployHost")

Write-Host "==> prepare $RemoteTmp on $DeployHost ($Target)"
& ssh @sshBase "rm -rf $RemoteTmp; mkdir -p $RemoteTmp"

$unixFiles = @()
foreach ($rel in $Files) {
  $relUnix = ($rel -replace "\\", "/").TrimStart("./")
  $local = Join-Path $MeshRoot $rel
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
# LF endings for bash
[System.IO.File]::WriteAllText($localSh, ($install -replace "`r`n", "`n" -replace "`r", "`n"))

& scp -P $DeployPort -o StrictHostKeyChecking=no $localSh "root@${DeployHost}:/tmp/mesh_ship_install.sh"
& ssh @sshBase "bash /tmp/mesh_ship_install.sh"
Write-Host "DONE $Target"
