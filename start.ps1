# Start the AI trading firm in the background (all 8 agents). Run:  ./start.ps1
Set-Location -Path $PSScriptRoot
$pidFile = "state\run.pid"
if ((Test-Path $pidFile) -and (Get-Process -Id (Get-Content $pidFile) -ErrorAction SilentlyContinue)) {
    Write-Host "Already running (PID $(Get-Content $pidFile))." -ForegroundColor Yellow
    exit 0
}
if (-not (Test-Path ".env")) {
    Write-Host "No .env file. Run ./setup.ps1 first." -ForegroundColor Red
    exit 1
}
if (-not (Test-Path ".venv\Scripts\pythonw.exe")) {
    Write-Host "Python environment missing. Run ./setup.ps1 first." -ForegroundColor Red
    exit 1
}
Start-Process ".venv\Scripts\pythonw.exe" -ArgumentList "run.py" -WorkingDirectory $PSScriptRoot -WindowStyle Hidden
Start-Sleep -Seconds 15
if (Test-Path $pidFile) {
    Write-Host "AI trading firm STARTED (PID $(Get-Content $pidFile))." -ForegroundColor Green
    Write-Host "Watch it live:   Get-Content logs\system.log -Wait -Tail 30"
    Write-Host "Latest report:   reports\LATEST.md   (new one every 8 hours)"
    Write-Host "The Studio:      http://localhost:8765   (or double-click 'The Studio' on your desktop)" -ForegroundColor Cyan
} else {
    Write-Host "It didn't start - run 'python run.py' to see the error." -ForegroundColor Red
}
