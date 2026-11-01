@echo off
title MyCitiPad Voice Receptionist Launcher
cd /d "%~dp0"

echo ===============================================================================
echo                Starting MyCitiPad Voice Receptionist Servers
echo ===============================================================================
echo.
echo [1/2] Launching Backend Server on http://localhost:8000 ...
start "MyCitiPad Backend :8000" "%~dp0run_backend.bat"

echo [2/2] Launching Frontend Server on http://localhost:3000 ...
start "MyCitiPad Frontend :3000" "%~dp0run_frontend.bat"

echo.
echo ===============================================================================
echo Both servers have been launched in dedicated windows!
echo.
echo   * Main Voice Client:    http://localhost:3000
echo   * Staff Operations:     http://localhost:3000/dashboard
echo   * FastAPI OpenAPI Docs:  http://localhost:8000/docs
echo   * Backend Health:       http://localhost:8000/health
echo.
echo To stop servers, close each individual console window or press CTRL+C.
echo ===============================================================================
