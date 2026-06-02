$HostName = if ([string]::IsNullOrWhiteSpace($env:ANVIL_FRONTEND_HOST)) { "127.0.0.1" } else { $env:ANVIL_FRONTEND_HOST }
$Port = if ([string]::IsNullOrWhiteSpace($env:ANVIL_FRONTEND_PORT)) { "13200" } else { $env:ANVIL_FRONTEND_PORT }
$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$frontendRoot = Join-Path $repoRoot "frontend"

Push-Location $frontendRoot
try {
  npm run dev -- --hostname $HostName --port $Port
} finally {
  Pop-Location
}
