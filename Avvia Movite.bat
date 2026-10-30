@echo off
title Movite
cd /d "%~dp0"
where python >nul 2>nul
if %errorlevel%==0 (
  python server\server.py
) else (
  py -3 server\server.py
)
pause
