@echo off
REM Uruchomienie AURA TREND (Windows)
cd /d %~dp0
if not exist .venv (
  python -m venv .venv
  .venv\Scripts\pip install -q -r requirements.txt
)
if not exist .env copy .env.example .env
echo AURA TREND: http://127.0.0.1:8000
.venv\Scripts\python -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8000
