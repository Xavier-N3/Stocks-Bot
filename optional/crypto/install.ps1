# Puts the parked crypto desk back into the bot. Run from the "Stocks bot" folder:  .\optional\crypto\install.ps1
# Safe: if it can't apply cleanly it undoes itself and changes nothing.
$ErrorActionPreference = "Stop"
Set-Location (Resolve-Path "$PSScriptRoot\..\..")

if (Test-Path "trader\crypto_shadow.py") { Write-Host "The crypto desk is already installed." -ForegroundColor Yellow; exit 0 }
if (git status --porcelain --untracked-files=no) {
    Write-Host "You have unsaved code changes. Commit or undo them first, then run this again." -ForegroundColor Red; exit 1
}

$ok = $false
Write-Host "Restoring the crypto desk (reversing the change that parked it)..."
git revert --no-commit crypto-removed 2>$null
if ($LASTEXITCODE -eq 0) { $ok = $true } else { git revert --abort 2>$null; git reset --hard -q HEAD }

if (-not $ok) {
    Write-Host "That didn't apply cleanly - trying the patch file..."
    git apply --3way --whitespace=nowarn "optional\crypto\crypto_desk.patch" 2>$null
    if ($LASTEXITCODE -eq 0) { $ok = $true } else { git reset --hard -q HEAD }
}

if (-not $ok) {
    Write-Host "Couldn't install automatically - the bot's code has changed too much since crypto was parked." -ForegroundColor Red
    Write-Host "Nothing was changed. Ask Claude: 'install the crypto desk from optional/crypto' (it can port the patch by hand)."
    exit 1
}

git add -A
git commit -q -m "Install the crypto desk from optional/crypto"
Write-Host ""
Write-Host "Crypto desk installed." -ForegroundColor Green
Write-Host "Next: .\start.ps1 (optional settings: see optional\crypto\README.md)"
