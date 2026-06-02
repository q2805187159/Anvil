param(
  [string]$Host = "127.0.0.1",
  [int]$Port = 18000,
  [int]$FrontendPort = 13200
)

$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$backendScript = Join-Path $PSScriptRoot "start-backend.ps1"
$frontendRoot = Join-Path $repoRoot "frontend"
$env:NEXT_PUBLIC_ANVIL_GATEWAY_URL = if ([string]::IsNullOrWhiteSpace($env:NEXT_PUBLIC_ANVIL_GATEWAY_URL)) { "http://127.0.0.1:$Port" } else { $env:NEXT_PUBLIC_ANVIL_GATEWAY_URL }

Start-Process powershell -ArgumentList "-ExecutionPolicy", "Bypass", "-File", $backendScript, "-Host", $Host, "-Port", $Port
Push-Location $frontendRoot
try {
  npm run dev -- --hostname 127.0.0.1 --port $FrontendPort
} finally {
  Pop-Location
}
