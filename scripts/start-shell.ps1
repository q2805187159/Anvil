param(
  [string]$Profile = "",
  [string]$AnvilHome = ""
)

$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$backendRoot = Join-Path $repoRoot "backend"
$env:PYTHONPATH = "$backendRoot;$backendRoot\packages\harness"

$argsList = @("-m", "app.shell.main")
if ($Profile) {
  $argsList += @("--profile", $Profile)
}
if ($AnvilHome) {
  $argsList += @("--anvil-home", $AnvilHome)
}

Push-Location $backendRoot
try {
  python @argsList
} finally {
  Pop-Location
}
