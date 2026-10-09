# Stop auto-starting at Windows login.
$vbs = Join-Path ([Environment]::GetFolderPath('Startup')) "StocksBot.vbs"
Remove-Item $vbs -ErrorAction SilentlyContinue
Write-Host "Auto-start removed." -ForegroundColor Green
