param(
  [Alias("Host")]
  [string]$BindHost = "127.0.0.1",
  [int]$Port = 18000
)

$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$backendRoot = Join-Path $repoRoot "backend"
$env:PYTHONPATH = "$backendRoot;$backendRoot\packages\harness"

Push-Location $backendRoot
try {
  python -m app.gateway.main --host $BindHost --port $Port
} finally {
  Pop-Location
}
