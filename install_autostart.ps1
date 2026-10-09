# Start the AI trading firm automatically every time you log in to Windows.
$startup = [Environment]::GetFolderPath('Startup')
$vbs = Join-Path $startup "StocksBot.vbs"
$cmd = "powershell.exe -WindowStyle Hidden -ExecutionPolicy Bypass -File ""$PSScriptRoot\start.ps1"""
Set-Content -Path $vbs -Value "CreateObject(""WScript.Shell"").Run ""$cmd"", 0, False" -Encoding ASCII
Write-Host "Auto-start installed: $vbs" -ForegroundColor Green
Write-Host "Remove it anytime with ./uninstall_autostart.ps1"
