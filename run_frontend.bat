@echo off
title MyCitiPad Frontend :3000
cd /d "%~dp0frontend"
echo ===============================================================================
echo                Starting MyCitiPad Frontend (Next.js on Port 3000)
echo ===============================================================================
echo.
call npx pnpm dev
pause
