$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$example = Join-Path $repoRoot "config.example.yaml"
$target = Join-Path $repoRoot "config.yaml"

if (-not (Test-Path $example)) {
  throw "Missing config.example.yaml"
}

if (-not (Test-Path $target)) {
  Copy-Item -LiteralPath $example -Destination $target
  Write-Host "Created $target from config.example.yaml"
} else {
  Write-Host "config.yaml already exists: $target"
}
