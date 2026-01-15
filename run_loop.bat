@echo off
title RSS Reader Auto-Update (Every 10 Mins)
cd /d "%~dp0"

:loop
cls
echo ========================================================
echo [%date% %time%] Starting Update...
echo ========================================================

.\venv\Scripts\python.exe build.py

echo.
echo ========================================================
echo [%date% %time%] Update Complete.
echo Waiting 10 minutes for next update...
echo (You can minimize this window window)
echo ========================================================

timeout /t 600 >nul
goto loop
