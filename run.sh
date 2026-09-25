#!/usr/bin/env bash
# Uruchomienie AURA TREND (Linux / macOS)
set -e
cd "$(dirname "$0")"
if [ ! -d .venv ]; then
  python3 -m venv .venv
  .venv/bin/pip install -q -r requirements.txt
fi
[ -f .env ] || cp .env.example .env
PORT=$(grep -E '^APP_PORT=' .env | cut -d= -f2); PORT=${PORT:-8000}
echo "AURA TREND: http://127.0.0.1:${PORT}"
exec .venv/bin/python -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port "$PORT"
