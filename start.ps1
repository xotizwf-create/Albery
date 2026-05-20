param(
  [int]$Port = 5002,
  [switch]$SkipInstall
)

$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$VenvPython = Join-Path $Root ".venv\Scripts\python.exe"
$Frontend = Get-ChildItem -LiteralPath $Root -Directory |
  Where-Object { Test-Path -LiteralPath (Join-Path $_.FullName "package.json") } |
  Select-Object -First 1 -ExpandProperty FullName

Set-Location $Root

if (-not (Test-Path -LiteralPath (Join-Path $Root ".env"))) {
  throw ".env was not found. Create .env and set DATABASE_URL and BITRIX_WEBHOOK_BASE."
}

if (-not $Frontend) {
  throw "Frontend folder with package.json was not found."
}

if (-not (Test-Path -LiteralPath $VenvPython)) {
  Write-Host "Creating Python venv..."
  python -m venv .venv
}

if (-not $SkipInstall) {
  Write-Host "Installing Python dependencies..."
  & $VenvPython -m pip install -r requirements.txt
}

Write-Host "Checking PostgreSQL database and schema..."
& $VenvPython .\scripts\ensure_postgres.py

Set-Location $Frontend
if ((-not $SkipInstall) -or (-not (Test-Path -LiteralPath "node_modules"))) {
  Write-Host "Installing frontend dependencies..."
  npm install
}

Write-Host "Building frontend..."
npm run build

Set-Location $Root
Write-Host "Starting app: http://127.0.0.1:$Port"
& $VenvPython -m flask --app app run --host 127.0.0.1 --port $Port
