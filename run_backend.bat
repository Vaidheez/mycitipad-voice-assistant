@echo off
title MyCitiPad Backend :8000
cd /d "%~dp0backend"
echo ===============================================================================
echo                Starting MyCitiPad Backend (FastAPI on Port 8000)
echo ===============================================================================
echo.
uv run python -m uvicorn src.server:app --host 0.0.0.0 --port 8000 --reload
pause
