#!/usr/bin/env bash
# Start the API without watching .venv (avoids endless WatchFiles reloads).
set -euo pipefail
cd "$(dirname "$0")/.."
exec .venv/bin/uvicorn src.api.main:app \
  --reload \
  --reload-dir src \
  --reload-dir frontend \
  --port 8000
