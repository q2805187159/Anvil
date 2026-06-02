$ErrorActionPreference = "Stop"

$scriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$argsString = $args
python (Join-Path $scriptRoot "generate-contracts.py") @argsString
