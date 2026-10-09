# Write a report right now (instead of waiting for the next 8-hour one). Run:  ./report_now.ps1
Set-Location -Path $PSScriptRoot
& .venv\Scripts\python.exe run.py --report
