@echo off
cd /d "%~dp0backend"
py -m uvicorn app.main:app --host 0.0.0.0 --port 8000 1>>"C:\Users\Eslam Ahmad\AppData\Local\Temp\opencode\drive-server\boot.out.log" 2>>"C:\Users\Eslam Ahmad\AppData\Local\Temp\opencode\drive-server\boot.err.log"
