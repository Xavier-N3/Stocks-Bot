# Puts the parked forex desk back into the bot. Run from the "Stocks bot" folder:  .\optional\forex\install.ps1
# Safe: if it can't apply cleanly it undoes itself and changes nothing.
$ErrorActionPreference = "Stop"
Set-Location (Resolve-Path "$PSScriptRoot\..\..")

if (Test-Path "trader\oanda.py") { Write-Host "The forex desk is already installed (trader\oanda.py exists)." -ForegroundColor Yellow; exit 0 }
if (git status --porcelain --untracked-files=no) {
    Write-Host "You have unsaved code changes. Commit or undo them first, then run this again." -ForegroundColor Red; exit 1
}

$ok = $false
Write-Host "Restoring the forex desk from the saved bookmark (forex-desk-v1)..."
git cherry-pick --no-commit forex-desk-v1 2>$null
if ($LASTEXITCODE -eq 0) { $ok = $true } else { git cherry-pick --abort 2>$null; git reset --hard -q HEAD }

if (-not $ok) {
    Write-Host "Bookmark didn't apply cleanly - trying the patch file..."
    git apply --3way --whitespace=nowarn "optional\forex\forex_desk.patch" 2>$null
    if ($LASTEXITCODE -eq 0) { $ok = $true } else { git reset --hard -q HEAD }
}

if (-not $ok) {
    Write-Host "Couldn't install automatically - the bot's code has changed too much since the desk was parked." -ForegroundColor Red
    Write-Host "Nothing was changed. Ask Claude: 'install the forex desk from optional/forex' (it can port the patch by hand)."
    exit 1
}

git add -A
git commit -q -m "Install the forex desk from optional/forex"
Write-Host ""
Write-Host "Forex desk installed." -ForegroundColor Green
Write-Host "Next: add OANDA_API_TOKEN=... to .env (see optional\forex\README.md), then run .\start.ps1"
