# One-time setup: private Python environment + packages + connection check. Run:  ./setup.ps1
Set-Location -Path $PSScriptRoot
if (-not (Test-Path ".env")) { Copy-Item ".env.example" ".env"; Write-Host "Created .env - open it and paste your keys." -ForegroundColor Yellow }
if (-not (Test-Path ".venv\Scripts\python.exe")) {
    Write-Host "Creating the bot's own Python environment (.venv)..."
    if (Get-Command py -ErrorAction SilentlyContinue) { py -3 -m venv .venv } else { python -m venv .venv }
}
& .venv\Scripts\python.exe -m pip install -q --upgrade pip
& .venv\Scripts\python.exe -m pip install -q -r requirements.txt
& .venv\Scripts\python.exe selftest.py
