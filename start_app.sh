#!/bin/bash

# Start all services in background
if command -v livekit-server >/dev/null 2>&1; then
  livekit-server --dev &
else
  echo "Warning: livekit-server not found. Skipping local LiveKit startup and using your configured LIVEKIT_URL instead."
fi

(cd backend && uv run python -m uvicorn src.server:app --host 0.0.0.0 --port 8000 --reload) &
(cd frontend && pnpm dev) &

# Wait for all background jobs
wait
