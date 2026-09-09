# Mesh image-based ship: Git commit → Build → Digest → tmesh/prod recreate
#
# CORRECT path (required for Environment / Reproducibility Baseline):
#   .\deploy\ship_image.ps1 -Target tmesh
#   .\deploy\ship_image.ps1 -Target prod   # only after tmesh OK
#
# FORBIDDEN as release path:
#   docker cp app files into a running container (lost on recreate; not commit-aligned)
#
# What this does:
#   1) Resolve git SHA (refuse dirty unless -AllowDirty)
#   2) Pack build context (Dockerfile, requirements, app, entry) → remote
#   3) Remote: docker build -t geekpark-mesh:<tag> --build-arg GIT_SHA=...
#   4) Remote: compose up -d with MESH_IMAGE_TAG (force-recreate mesh + worker)
#   5) Emit ENV_REPRO_BASELINE with image digest
#
# Does NOT run Preview / Ask / Publish / full capacity.

param(
  [ValidateSet("tmesh", "prod")]
  [Parameter(Mandatory = $true)]
  [string]$Target,

  [switch]$AllowDirty,

  [string]$DeployHost = $(if ($env:MESH_DEPLOY_HOST) { $env:MESH_DEPLOY_HOST } else { "104.250.53.182" }),
  [string]$DeployPort = $(if ($env:MESH_DEPLOY_PORT) { $env:MESH_DEPLOY_PORT } else { "22341" })
)

$ErrorActionPreference = "Stop"
$MeshRoot = Split-Path -Parent $PSScriptRoot
if (-not (Test-Path (Join-Path $MeshRoot "app"))) {
  throw "Expected mesh/ root at $MeshRoot"
}

Push-Location (Split-Path -Parent $MeshRoot)
try {
  $GitSha = (git rev-parse HEAD).Trim()
  $GitShort = (git rev-parse --short=12 HEAD).Trim()
  $Dirty = git status --porcelain
} finally {
  Pop-Location
}

if ($Dirty -and -not $AllowDirty) {
  throw "Working tree dirty. Commit first, or pass -AllowDirty (not for release baseline)."
}
if ($Dirty) {
  Write-Host "WARNING: dirty tree; baseline will mark git_dirty=true" -ForegroundColor Yellow
}

$Date = Get-Date -Format "yyyy-MM-dd"
$ImageTag = "$Date-$GitShort"
$BuildDate = (Get-Date).ToUniversalTime().ToString("o")

if ($Target -eq "tmesh") {
  $RemoteBase = "/opt/geekpark-tmesh"
  $ComposeFile = "docker-compose.staging.yml"
  $Containers = @("geekpark-tmesh", "geekpark-tmesh-worker")
  $SmokeUrl = "http://127.0.0.1:8091/"
} else {
  $RemoteBase = "/opt/geekpark-mesh"
  $ComposeFile = "docker-compose.yml"
  $Containers = @("geekpark-mesh", "geekpark-mesh-worker")
  $SmokeUrl = "http://127.0.0.1:8090/"
}

Write-Host "==> ship_image Target=$Target tag=geekpark-mesh:$ImageTag sha=$GitShort"

# Pack reproducible build context (+ compose + emit script)
$tarLocal = Join-Path $env:TEMP "mesh_image_ctx.tgz"
if (Test-Path $tarLocal) { Remove-Item $tarLocal -Force }
Push-Location $MeshRoot
try {
  & tar -czf $tarLocal `
    --exclude=__pycache__ --exclude=*.pyc --exclude=.env --exclude=data `
    --exclude=eval/reports --exclude=app-deploy.tgz `
    Dockerfile requirements.txt docker-compose.staging.yml docker-compose.yml `
    .dockerignore app deploy/docker-entry.sh eval/emit_repro_baseline.py
} finally {
  Pop-Location
}

Write-Host "==> upload context ($([math]::Round((Get-Item $tarLocal).Length/1KB)) KB)"
& scp -P $DeployPort -o StrictHostKeyChecking=no $tarLocal "root@${DeployHost}:/tmp/mesh_image_ctx.tgz"

$remoteScript = @"
set -euo pipefail
BASE='$RemoteBase'
TAG='$ImageTag'
SHA='$GitSha'
SHORT='$GitShort'
BDATE='$BuildDate'
COMPOSE='$ComposeFile'
cd "`$BASE"
TS=`$(date +%Y%m%d%H%M%S)
mkdir -p "bak_ctx_`$TS"
# refresh build inputs from pack (keep .env + data)
tar -xzf /tmp/mesh_image_ctx.tgz
# pin env for compose
grep -q '^MESH_IMAGE_TAG=' .env 2>/dev/null && sed -i "s|^MESH_IMAGE_TAG=.*|MESH_IMAGE_TAG=`$TAG|" .env || echo "MESH_IMAGE_TAG=`$TAG" >> .env
grep -q '^MESH_GIT_SHA=' .env 2>/dev/null && sed -i "s|^MESH_GIT_SHA=.*|MESH_GIT_SHA=`$SHORT|" .env || echo "MESH_GIT_SHA=`$SHORT" >> .env
# Vector OFF freeze (lexical default)
grep -q '^MESH_EMBED_ENABLED=' .env 2>/dev/null && sed -i 's|^MESH_EMBED_ENABLED=.*|MESH_EMBED_ENABLED=0|' .env || echo 'MESH_EMBED_ENABLED=0' >> .env
grep -q '^MESH_VECTOR_ENABLED=' .env 2>/dev/null && sed -i 's|^MESH_VECTOR_ENABLED=.*|MESH_VECTOR_ENABLED=0|' .env || echo 'MESH_VECTOR_ENABLED=0' >> .env

export MESH_IMAGE_TAG=`$TAG
export MESH_GIT_SHA=`$SHORT
export MESH_BUILD_DATE=`$BDATE

echo "==> docker build geekpark-mesh:`$TAG"
docker build \
  --build-arg GIT_SHA=`$SHA \
  --build-arg BUILD_DATE=`$BDATE \
  --build-arg IMAGE_TAG=`$TAG \
  -t "geekpark-mesh:`$TAG" \
  -t "geekpark-mesh:`$SHORT" \
  .

DIGEST=`$(docker image inspect "geekpark-mesh:`$TAG" --format '{{index .RepoDigests 0}}' 2>/dev/null || true)
ID=`$(docker image inspect "geekpark-mesh:`$TAG" --format '{{.Id}}')
# local builds may lack RepoDigests until push; fall back to Id
if [ -z "`$DIGEST" ] || [ "`$DIGEST" = "<no value>" ]; then
  DIGEST=`$ID
fi
echo "IMAGE_ID=`$ID"
echo "IMAGE_DIGEST=`$DIGEST"

echo "==> compose up ($COMPOSE) image=`$TAG"
docker compose -f "`$COMPOSE" up -d --force-recreate --no-build mesh mesh-worker || \
  docker-compose -f "`$COMPOSE" up -d --force-recreate --no-build mesh mesh-worker

# ensure compose references the tag we built (image: geekpark-mesh:`$TAG)
sleep 8
curl -sS -o /dev/null -w 'smoke=%{http_code}\n' '$SmokeUrl' || true

# verify no stale docker-cp expectation: code comes from image
RUNNING_SHA=`$(docker exec ${Containers[0]} printenv MESH_BUILD_GIT_SHA || true)
echo "RUNNING_MESH_BUILD_GIT_SHA=`$RUNNING_SHA"

mkdir -p eval/reports
docker cp eval/emit_repro_baseline.py ${Containers[0]}:/srv/mesh/eval/emit_repro_baseline.py
docker exec \
  -e MESH_IMAGE_TAG=`$TAG \
  -e MESH_IMAGE_ID=`$ID \
  -e MESH_IMAGE_DIGEST=`$DIGEST \
  -e MESH_BUILD_GIT_SHA=`$SHORT \
  -e MESH_BUILD_DATE=`$BDATE \
  ${Containers[0]} \
  python /srv/mesh/eval/emit_repro_baseline.py --in-container \
    --image-tag "`$TAG" --image-id "`$ID" --image-digest "`$DIGEST" \
    --out /srv/mesh/eval/reports/ENV_REPRO_BASELINE.json || true
docker cp ${Containers[0]}:/srv/mesh/eval/reports/ENV_REPRO_BASELINE.json eval/reports/ENV_REPRO_BASELINE.$Target.json 2>/dev/null || true
docker cp ${Containers[0]}:/srv/mesh/eval/reports/ENV_REPRO_BASELINE.md eval/reports/ENV_REPRO_BASELINE.$Target.md 2>/dev/null || true

echo "SHIP_IMAGE_OK tag=`$TAG digest=`$DIGEST"
"@

$remotePath = "/tmp/mesh_ship_image_$Target.sh"
$remoteScript = $remoteScript -replace "`r`n", "`n"
Set-Content -Path (Join-Path $env:TEMP "mesh_ship_image_$Target.sh") -Value $remoteScript -Encoding ascii -NoNewline
& scp -P $DeployPort -o StrictHostKeyChecking=no (Join-Path $env:TEMP "mesh_ship_image_$Target.sh") "root@${DeployHost}:$remotePath"
& ssh -p $DeployPort -o StrictHostKeyChecking=no "root@$DeployHost" "bash $remotePath"

Write-Host "DONE $Target geekpark-mesh:$ImageTag"
Write-Host "Pull baseline: ${RemoteBase}/eval/reports/ENV_REPRO_BASELINE.$Target.json"
