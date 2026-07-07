$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$LogDir = Join-Path $Root "logs"
$LogFile = Join-Path $LogDir "web-current.log"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
Set-Location $Root

# Auto-detect Node.js, fall back to Python
$node = $null
$candidates = @(
    "node",
    (Join-Path $env:LOCALAPPDATA "Programs\nodejs\node.exe"),
    "C:\Users\Administrator\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe"
)
foreach ($c in $candidates) {
    if (Get-Command $c -ErrorAction SilentlyContinue) {
        $node = (Get-Command $c -ErrorAction SilentlyContinue).Source
        break
    }
    if ($c -and (Test-Path $c)) {
        $node = $c
        break
    }
}

$ErrorActionPreference = "Continue"
if ($node) {
    Write-Host "Using Node.js: $node"
    & $node .\dev-server.mjs *> $LogFile
} else {
    $py = Join-Path $Root ".venv\Scripts\python.exe"
    if (-not (Test-Path $py)) { $py = "python" }
    Write-Host "Node.js not found, using Python: $py"
    & $py .\dev_server.py *> $LogFile
}
