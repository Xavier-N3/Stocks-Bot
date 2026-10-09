# Is it running, how is each agent doing, what do we hold. Run:  ./status.ps1
Set-Location -Path $PSScriptRoot
$pidFile = "state\run.pid"
if ((Test-Path $pidFile) -and (Get-Process -Id (Get-Content $pidFile) -ErrorAction SilentlyContinue)) {
    Write-Host "STATUS: RUNNING (PID $(Get-Content $pidFile))" -ForegroundColor Green
} else {
    Write-Host "STATUS: STOPPED   (start with ./start.ps1)" -ForegroundColor Yellow
}
& .venv\Scripts\python.exe -m trader.status
