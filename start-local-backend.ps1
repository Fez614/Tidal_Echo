$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$EnvFile = Join-Path $Root "backend\relay.env.local"

Get-Content -LiteralPath $EnvFile | ForEach-Object {
  $line = $_.Trim()
  if (-not $line -or $line.StartsWith("#") -or -not $line.Contains("=")) { return }
  $key, $value = $line.Split("=", 2)
  [Environment]::SetEnvironmentVariable($key.Trim(), $value.Trim(), "Process")
}

Set-Location (Join-Path $Root "backend")
& (Join-Path $Root ".venv\Scripts\python.exe") -m uvicorn app:app --host 127.0.0.1 --port 3011
