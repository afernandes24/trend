"""TREND FOLLOWING — punkt startowy aplikacji (FastAPI + frontend statyczny).

Uruchomienie:  python -m uvicorn app.main:app --app-dir backend --port 8000
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

import base64
import secrets

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

from .api.routes import router
from .config import settings
from .database import Database
from .market_data.service import MarketDataService
from .notifications.dispatcher import Notifier
from .services.analysis import AnalysisService
from .services.positions import PositionService
from .services.scheduler import Scheduler

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def build_services(app: FastAPI, db: Database | None = None, market: MarketDataService | None = None) -> None:
    app.state.db = db or Database(settings.database_path)
    app.state.market = market or MarketDataService(app.state.db)
    app.state.notifier = Notifier(app.state.db, settings, fx=app.state.market.fx_rates)
    app.state.notifier.source = app.state.market.provider.name
    app.state.analysis = AnalysisService(app.state.db, app.state.market, app.state.notifier)
    app.state.positions = PositionService(app.state.db, app.state.market, app.state.notifier)
    live = app.state.market.provider.name == "capital"
    app.state.scheduler = Scheduler(app.state.market, app.state.analysis, settings.scan_interval_seconds,
                                    settings.h1_refresh_seconds,
                                    offset_seconds=settings.scan_offset_seconds if settings.scan_offset_seconds >= 0
                                    else (3 if live else 20))


@asynccontextmanager
async def lifespan(app: FastAPI):
    if not hasattr(app.state, "db"):
        build_services(app)
    if settings.scanner_enabled and getattr(app.state, "autostart", True):
        app.state.scheduler.start()
    yield
    await app.state.scheduler.stop()


app = FastAPI(title="TREND FOLLOWING — analiza (XTB)", version="1.0.0", lifespan=lifespan)
app.include_router(router)


@app.middleware("http")
async def basic_auth(request: Request, call_next):
    """Logowanie hasłem (APP_USERNAME / APP_PASSWORD). /api/health zostaje otwarte dla healthchecku."""
    if not settings.auth_password or request.url.path == "/api/health":
        return await call_next(request)
    header = request.headers.get("authorization", "")
    if header.startswith("Basic "):
        try:
            user, _, pwd = base64.b64decode(header[6:]).decode("utf-8").partition(":")
        except (ValueError, UnicodeDecodeError):
            user, pwd = "", ""
        if secrets.compare_digest(user, settings.auth_username or "admin") and \
                secrets.compare_digest(pwd, settings.auth_password):
            return await call_next(request)
    return Response("Wymagane logowanie", status_code=401,
                    headers={"WWW-Authenticate": 'Basic realm="TREND FOLLOWING", charset="UTF-8"'})

_assets = settings.frontend_dir / "assets"
app.mount("/assets", StaticFiles(directory=_assets), name="assets")


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(settings.frontend_dir / "index.html", headers={"Cache-Control": "no-cache"})
