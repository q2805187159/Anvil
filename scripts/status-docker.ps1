$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$backendPort = if ([string]::IsNullOrWhiteSpace($env:ANVIL_BACKEND_PORT)) { "18000" } else { $env:ANVIL_BACKEND_PORT }
$frontendPort = if ([string]::IsNullOrWhiteSpace($env:ANVIL_FRONTEND_PORT)) { "13200" } else { $env:ANVIL_FRONTEND_PORT }
$useComposePlugin = $false
& docker compose version *> $null
if ($LASTEXITCODE -eq 0) {
  $useComposePlugin = $true
}
function Invoke-AnvilCompose {
  param([string[]]$ComposeArgs)
  if ($useComposePlugin) {
    & docker compose @ComposeArgs
  } else {
    & docker-compose @ComposeArgs
  }
}
function Get-PublishedEndpointOnce {
  param(
    [string]$Service,
    [int]$TargetPort
  )
  $jsonLines = Invoke-AnvilCompose -ComposeArgs @("ps", "--format", "json") 2>$null
  if ($LASTEXITCODE -ne 0 -or $null -eq $jsonLines) {
    return $null
  }
  foreach ($line in @($jsonLines)) {
    if ([string]::IsNullOrWhiteSpace($line)) {
      continue
    }
    try {
      $decoded = $line | ConvertFrom-Json
    } catch {
      continue
    }
    foreach ($entry in @($decoded)) {
      if ($entry.Service -ne $Service) {
        continue
      }
      foreach ($publisher in @($entry.Publishers)) {
        if ($null -eq $publisher) {
          continue
        }
        $publishedPort = [int]$publisher.PublishedPort
        if ([int]$publisher.TargetPort -eq $TargetPort -and $publishedPort -gt 0) {
          $hostName = [string]$publisher.URL
          if ([string]::IsNullOrWhiteSpace($hostName) -or $hostName -eq "0.0.0.0" -or $hostName -eq "::") {
            $hostName = "127.0.0.1"
          }
          return [pscustomobject]@{
            Host = $hostName
            Port = $publishedPort
          }
        }
      }
      return [pscustomobject]@{
        Host = $null
        Port = 0
      }
    }
  }
  return $null
}
function Get-PublishedEndpoint {
  param(
    [string]$Service,
    [int]$TargetPort,
    [int]$Retries = 1,
    [int]$RetryDelaySeconds = 1
  )
  $lastEndpoint = $null
  for ($attempt = 1; $attempt -le $Retries; $attempt++) {
    $lastEndpoint = Get-PublishedEndpointOnce -Service $Service -TargetPort $TargetPort
    if ($lastEndpoint -and [int]$lastEndpoint.Port -gt 0) {
      return $lastEndpoint
    }
    if ($attempt -lt $Retries) {
      Start-Sleep -Seconds $RetryDelaySeconds
    }
  }
  return $lastEndpoint
}
function Format-EndpointUrl {
  param(
    [object]$Endpoint,
    [int]$TargetPort
  )
  if ($null -eq $Endpoint) {
    return "unavailable (compose status could not be read)"
  }
  if ([int]$Endpoint.Port -le 0) {
    return "not published (target $TargetPort/tcp)"
  }
  return "http://$($Endpoint.Host):$($Endpoint.Port)"
}
Push-Location $repoRoot
try {
  $backendEndpoint = Get-PublishedEndpoint -Service "backend" -TargetPort 18000 -Retries 3
  $frontendEndpoint = Get-PublishedEndpoint -Service "frontend" -TargetPort 13200 -Retries 3
  $backendUrl = Format-EndpointUrl -Endpoint $backendEndpoint -TargetPort 18000
  $frontendUrl = Format-EndpointUrl -Endpoint $frontendEndpoint -TargetPort 13200
  Write-Host "== Compose Services =="
  $composeOutput = Invoke-AnvilCompose -ComposeArgs @("ps", "-a") 2>&1
  if ($LASTEXITCODE -eq 0) {
    $composeOutput | ForEach-Object { Write-Host $_ }
  } else {
    Write-Host "Docker compose is unavailable or the local engine is not running."
    if ($composeOutput) {
      $composeOutput | ForEach-Object { Write-Host $_ }
    }
  }
  Write-Host ""
  Write-Host "== Endpoints =="
  Write-Host "Frontend: $frontendUrl"
  Write-Host "Backend:  $backendUrl"
  if ($backendEndpoint -and [int]$backendEndpoint.Port -gt 0) {
    Write-Host "Health:   $backendUrl/health"
  } else {
    Write-Host "Health:   not available until backend port is published"
  }
  Write-Host ""
  Write-Host "== Health Check =="
  if ($backendEndpoint -and [int]$backendEndpoint.Port -gt 0) {
    try {
      $health = Invoke-WebRequest -UseBasicParsing "$backendUrl/health" -TimeoutSec 5
      Write-Host $health.Content
    } catch {
      Write-Host "Backend health endpoint is not reachable."
    }
  } else {
    Write-Host "Backend target port 18000 is not published."
  }
  Write-Host ""
  Write-Host "== Frontend Check =="
  if ($frontendEndpoint -and [int]$frontendEndpoint.Port -gt 0) {
    try {
      $frontend = Invoke-WebRequest -UseBasicParsing $frontendUrl -TimeoutSec 5
      Write-Host "Frontend status: $($frontend.StatusCode)"
    } catch {
      Write-Host "Frontend endpoint is not reachable."
    }
  } else {
    Write-Host "Frontend target port 13200 is not published."
  }
  Write-Host ""
  Write-Host "== Verification =="
  Write-Host "backend:  python -m pytest -q"
  Write-Host "frontend: npm test"
  Write-Host "frontend: npm run typecheck"
  Write-Host "frontend: npm run build"
} finally {
  Pop-Location
}
