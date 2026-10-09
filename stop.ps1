# Stop the AI trading firm gracefully. Run:  ./stop.ps1
# (Open positions stay protected by their stop-loss / take-profit orders at Alpaca.)
Set-Location -Path $PSScriptRoot
$pidFile = "state\run.pid"
if (-not (Test-Path $pidFile)) { Write-Host "Not running." -ForegroundColor Yellow; exit 0 }
$id = Get-Content $pidFile
New-Item -ItemType File -Force "state\stop.flag" | Out-Null
Write-Host "Asking all agents to finish their current step..."
for ($i = 0; $i -lt 45; $i++) {
    if (-not (Get-Process -Id $id -ErrorAction SilentlyContinue)) { break }
    Start-Sleep -Seconds 1
}
if (Get-Process -Id $id -ErrorAction SilentlyContinue) { Stop-Process -Id $id -Force }
Remove-Item $pidFile, "state\stop.flag" -ErrorAction SilentlyContinue
Write-Host "Stopped." -ForegroundColor Green
