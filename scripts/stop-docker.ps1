$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$useComposePlugin = $false
& docker compose version *> $null
if ($LASTEXITCODE -eq 0) {
  $useComposePlugin = $true
}
Push-Location $repoRoot
try {
  if ($useComposePlugin) {
    docker compose down
  } else {
    docker-compose down
  }
} finally {
  Pop-Location
}
