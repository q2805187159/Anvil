$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$backendRoot = Join-Path $repoRoot "backend"
$env:PYTHONPATH = "$backendRoot;$backendRoot\packages\harness"

Push-Location $backendRoot
try {
  python -m app.smoke local
} finally {
  Pop-Location
}
