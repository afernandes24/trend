# TREND FOLLOWING — obraz do wdrożenia (Railway / dowolny serwer z Dockerem)
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TZ=UTC \
    DATABASE_PATH=/data/aura_trend.db

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend ./backend
COPY frontend ./frontend
RUN mkdir -p data/import

EXPOSE 8000
# 1 worker = 1 skaner (więcej workerów wysyłałoby zdublowane alerty)
CMD ["sh", "-c", "python -m uvicorn app.main:app --app-dir backend --host 0.0.0.0 --port ${PORT:-8000} --workers 1 --proxy-headers --forwarded-allow-ips='*'"]
