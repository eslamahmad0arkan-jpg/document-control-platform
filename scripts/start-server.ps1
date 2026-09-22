$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

if (Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue) {
    Write-Host "Server already listening on port 8000."
    exit 0
}

Remove-Item logs\server.out.log, logs\server.err.log -ErrorAction SilentlyContinue
$p = Start-Process -FilePath ".\.venv\Scripts\python.exe" `
    -ArgumentList "-m", "uvicorn", "app.main:app", "--app-dir", "backend", `
        "--host", "127.0.0.1", "--port", "8000" `
    -WorkingDirectory $root `
    -RedirectStandardOutput "logs\server.out.log" `
    -RedirectStandardError "logs\server.err.log" `
    -PassThru -WindowStyle Hidden
$p.Id | Set-Content logs\server.pid
Write-Host "Started server PID $($p.Id)"