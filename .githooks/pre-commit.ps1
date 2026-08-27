# Mesh frontend pre-commit (PowerShell). Runs local check only when
# staged files include app.js / console.js / dialog.js / style.css / key templates.
$ErrorActionPreference = "Stop"
$Root = (git rev-parse --show-toplevel).Trim()
Set-Location $Root

$staged = @(git diff --cached --name-only --diff-filter=ACMR)
$patterns = @(
  "mesh/app/static/app.js",
  "mesh/app/static/console.js",
  "mesh/app/static/dialog.js",
  "mesh/app/static/style.css",
  "mesh/app/templates/base.html",
  "mesh/app/templates/issue_console.html"
)
$need = $false
foreach ($f in $staged) {
  $norm = ($f -replace "\\", "/")
  foreach ($p in $patterns) {
    if ($norm -eq $p -or $norm.EndsWith("/" + $p)) {
      $need = $true
      break
    }
  }
  if ($need) { break }
}
if (-not $need) { exit 0 }

Write-Host "==> pre-commit: frontend check (local)"
$py = Get-Command python -ErrorAction SilentlyContinue
if (-not $py) { $py = Get-Command python3 -ErrorAction SilentlyContinue }
if (-not $py) {
  Write-Host "pre-commit: python not found, skip" -ForegroundColor Yellow
  exit 0
}
& $py.Source "mesh/scripts/check_frontend.py"
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
Write-Host "==> pre-commit: FRONTEND_OK"
