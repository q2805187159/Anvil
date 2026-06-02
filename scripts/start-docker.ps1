param(
  [switch]$Build
)

$ErrorActionPreference = "Stop"

$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$backendPort = if ([string]::IsNullOrWhiteSpace($env:ANVIL_BACKEND_PORT)) { "18000" } else { $env:ANVIL_BACKEND_PORT }
$frontendPort = if ([string]::IsNullOrWhiteSpace($env:ANVIL_FRONTEND_PORT)) { "13200" } else { $env:ANVIL_FRONTEND_PORT }
$script:ComposeFileArgs = @("-f", "docker-compose.yml")
$useComposePlugin = $false
& docker compose version *> $null
if ($LASTEXITCODE -eq 0) {
  $useComposePlugin = $true
}
function Invoke-AnvilCompose {
  param([string[]]$ComposeArgs)
  if ($useComposePlugin) {
    & docker compose @script:ComposeFileArgs @ComposeArgs
  } else {
    & docker-compose @script:ComposeFileArgs @ComposeArgs
  }
}
function New-AnvilBindMount {
  param(
    [string]$Source,
    [string]$Target
  )
  return [ordered]@{
    type = "bind"
    source = $Source
    target = $Target
    read_only = $false
  }
}
function New-AnvilHostPathOverride {
  param([string]$RepoRootPath)
  $anvilHome = if ([string]::IsNullOrWhiteSpace($env:ANVIL_HOME_HOST)) {
    Join-Path $HOME ".anvil"
  } else {
    $env:ANVIL_HOME_HOST
  }
  $anvilHome = (New-Item -ItemType Directory -Force -Path $anvilHome).FullName
  $sharedWorkspace = if ([string]::IsNullOrWhiteSpace($env:ANVIL_WORKSPACE_HOST)) {
    Join-Path $anvilHome "workspace"
  } else {
    $env:ANVIL_WORKSPACE_HOST
  }
  $sharedWorkspace = (New-Item -ItemType Directory -Force -Path $sharedWorkspace).FullName

  $mounts = @()
  $bridges = @()
  $bridges += "shared_workspace|$sharedWorkspace|/mnt/host-workspaces/harness"

  $drives = Get-PSDrive -PSProvider FileSystem |
    Where-Object { $_.Root -match '^[A-Za-z]:\\$' -and (Test-Path -LiteralPath $_.Root) } |
    Sort-Object Name
  foreach ($drive in $drives) {
    $letter = $drive.Name.ToUpperInvariant()
    $alias = "$($letter.ToLowerInvariant())_drive"
    $target = "/mnt/host/$alias"
    $mounts += [pscustomobject]@{
      Source = $drive.Root
      Target = $target
    }
    $bridges += "$alias|$($letter):|$target"
  }

  $overrideRoot = Join-Path ([System.IO.Path]::GetTempPath()) "anvil-docker"
  $overrideRoot = (New-Item -ItemType Directory -Force -Path $overrideRoot).FullName
  $repoSlug = ([Convert]::ToBase64String([System.Text.Encoding]::UTF8.GetBytes($RepoRootPath))).TrimEnd("=") -replace "[+/]", "-"
  $overridePath = Join-Path $overrideRoot "docker-compose.host-paths.$repoSlug.json"
  $bridgeValue = $bridges -join ";"
  $volumes = @(
    (New-AnvilBindMount -Source $anvilHome -Target "/app/.anvil"),
    (New-AnvilBindMount -Source $sharedWorkspace -Target "/mnt/host-workspaces/harness")
  )
  if ($mounts.Count -gt 0) {
    foreach ($mount in $mounts) {
      $volumes += (New-AnvilBindMount -Source $mount.Source -Target $mount.Target)
    }
  }
  $override = [ordered]@{
    services = [ordered]@{
      backend = [ordered]@{
        environment = [ordered]@{
          ANVIL_HOME = "/app/.anvil"
          ANVIL_PATH_BRIDGES = $bridgeValue
        }
        volumes = $volumes
      }
    }
  }
  $override | ConvertTo-Json -Depth 10 | Set-Content -Path $overridePath -Encoding UTF8
  $env:ANVIL_HOME_HOST = $anvilHome
  $env:ANVIL_WORKSPACE_HOST = $sharedWorkspace
  $env:ANVIL_PATH_BRIDGES = $bridgeValue
  $script:ComposeFileArgs = @("-f", "docker-compose.yml", "-f", $overridePath)
  Write-Output $overridePath
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
function Test-AnvilHttpEndpoint {
  param(
    [string]$Name,
    [string]$Url
  )
  try {
    $response = Invoke-WebRequest -UseBasicParsing $Url -TimeoutSec 10
    Write-Host "$Name check: $($response.StatusCode)"
    return $true
  } catch {
    Write-Warning "$Name check failed: $($_.Exception.Message)"
    return $false
  }
}
Push-Location $repoRoot
try {
  New-AnvilHostPathOverride -RepoRootPath $repoRoot.Path
  Invoke-AnvilCompose -ComposeArgs @("config", "--quiet")
  if ($LASTEXITCODE -ne 0) {
    throw "Docker Compose configuration is invalid"
  }
  $args = @("up", "-d")
  if ($Build) {
    $args += "--build"
  } else {
    Write-Host "Docker images are being reused. After source changes, run this script with -Build to refresh the frontend/backend images."
  }
  Invoke-AnvilCompose -ComposeArgs $args
  if ($LASTEXITCODE -ne 0) {
    throw "Docker Compose up failed with exit code $LASTEXITCODE"
  }
  $backendEndpoint = Get-PublishedEndpoint -Service "backend" -TargetPort 18000 -Retries 10
  $frontendEndpoint = Get-PublishedEndpoint -Service "frontend" -TargetPort 13200 -Retries 10
  $backendUrl = Format-EndpointUrl -Endpoint $backendEndpoint -TargetPort 18000
  $frontendUrl = Format-EndpointUrl -Endpoint $frontendEndpoint -TargetPort 13200
  Write-Host ""
  Write-Host "Anvil local Docker workspace is starting."
  Write-Host "Frontend: $frontendUrl"
  Write-Host "Backend:  $backendUrl"
  if ($backendEndpoint -and [int]$backendEndpoint.Port -gt 0) {
    Write-Host "Health:   $backendUrl/health"
  } else {
    Write-Host "Health:   not available until backend port is published"
  }
  Write-Host "Path bridge: $env:ANVIL_PATH_BRIDGES"
  $endpointFailures = @()
  if ($backendEndpoint -and [int]$backendEndpoint.Port -gt 0) {
    if (-not (Test-AnvilHttpEndpoint -Name "Backend health" -Url "$backendUrl/health")) {
      $endpointFailures += "backend health is not reachable at $backendUrl/health"
    }
  } else {
    $endpointFailures += "backend target port 18000 is not published"
  }
  if ($frontendEndpoint -and [int]$frontendEndpoint.Port -gt 0) {
    if (-not (Test-AnvilHttpEndpoint -Name "Frontend" -Url $frontendUrl)) {
      $endpointFailures += "frontend is not reachable at $frontendUrl"
    }
  } else {
    $endpointFailures += "frontend target port 13200 is not published"
  }
  if ($endpointFailures.Count -gt 0) {
    Write-Warning "Docker workspace started, but published endpoint verification failed:"
    foreach ($failure in $endpointFailures) {
      Write-Warning "- $failure"
    }
    throw "Docker endpoint verification failed"
  }
} finally {
  Pop-Location
}
