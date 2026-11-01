$ErrorActionPreference = "Stop"

function Test-CommandExists {
  param([string]$CommandName)

  return $null -ne (Get-Command $CommandName -ErrorAction SilentlyContinue)
}

if (-not (Test-CommandExists "uv")) {
  Write-Error "Missing required command: uv"
}

$pnpmCmd = "pnpm"
if (-not (Test-CommandExists "pnpm")) {
  if (Test-CommandExists "npx.cmd") {
    $pnpmCmd = "npx.cmd pnpm"
  } elseif (Test-CommandExists "npx") {
    $pnpmCmd = "npx pnpm"
  } else {
    Write-Error "Missing required command: pnpm or npx"
  }
}

$repoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path

# Start each service in its own native cmd window to avoid powershell 0xc0000142 DLL init limits
Start-Process cmd -ArgumentList "/k", "title MyCitiPad Backend :8000 && cd /d `"$repoRoot\backend`" && uv run python -m uvicorn src.server:app --host 0.0.0.0 --port 8000 --reload"
Start-Process cmd -ArgumentList "/k", "title MyCitiPad Frontend :3000 && cd /d `"$repoRoot\frontend`" && call $pnpmCmd dev"

Write-Host "===============================================================================" -ForegroundColor Cyan
Write-Host "Started FastAPI backend (port 8000) and Next.js frontend (port 3000)!" -ForegroundColor Green
Write-Host "  * Main Voice Client:    http://localhost:3000" -ForegroundColor White
Write-Host "  * Staff Operations:     http://localhost:3000/dashboard" -ForegroundColor White
Write-Host "  * FastAPI OpenAPI Docs:  http://localhost:8000/docs" -ForegroundColor White
Write-Host "===============================================================================" -ForegroundColor Cyan
