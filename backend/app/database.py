"""Warstwa bazy danych (SQLite).

Jedno miejsce z całym SQL-em aplikacji. Żeby przejść na PostgreSQL, wystarczy
zaimplementować te same metody klasy `Database` na innym sterowniku.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

import pandas as pd

from .market_data.base import COLUMNS, empty_frame, epoch_seconds

SCHEMA = """
CREATE TABLE IF NOT EXISTS candles (
    symbol TEXT NOT NULL, tf TEXT NOT NULL, ts INTEGER NOT NULL,
    open REAL, high REAL, low REAL, close REAL, volume REAL,
    PRIMARY KEY (symbol, tf, ts)
);
CREATE TABLE IF NOT EXISTS signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at INTEGER NOT NULL,
    symbol TEXT NOT NULL, tf TEXT NOT NULL, direction TEXT NOT NULL,
    bar_ts INTEGER NOT NULL,
    entry REAL, sl REAL, tp1 REAL, tp2 REAL, tp3 REAL,
    score INTEGER, reasons TEXT, mtf TEXT,
    status TEXT NOT NULL DEFAULT 'OPEN',   -- OPEN | CLOSED | EXPIRED
    result TEXT,                           -- SL | TP1 | TP2 | TP3 | EXPIRED
    result_r REAL,
    closed_at INTEGER,
    source TEXT NOT NULL DEFAULT 'LIVE',   -- LIVE | BACKFILL
    UNIQUE (symbol, tf, direction, bar_ts)
);
CREATE INDEX IF NOT EXISTS ix_signals_time ON signals (bar_ts DESC);
CREATE TABLE IF NOT EXISTS alerts_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts INTEGER NOT NULL, signal_id INTEGER, symbol TEXT, direction TEXT,
    channel TEXT, status TEXT, message TEXT, error TEXT
);
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY, value TEXT NOT NULL
);
"""


class Database:
    def __init__(self, path: Path | str):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.executescript(SCHEMA)
            self._conn.commit()

    # ------------------------------------------------------------------ candles
    def upsert_candles(self, symbol: str, tf: str, df: pd.DataFrame) -> int:
        if df is None or df.empty:
            return 0
        ts = epoch_seconds(df.index)
        rows = list(zip([symbol] * len(df), [tf] * len(df), ts,
                        *(df[c].astype(float).tolist() for c in COLUMNS)))
        with self._lock:
            self._conn.executemany(
                "INSERT OR REPLACE INTO candles (symbol, tf, ts, open, high, low, close, volume) "
                "VALUES (?,?,?,?,?,?,?,?)", rows)
            self._conn.commit()
        return len(rows)

    def load_candles(self, symbol: str, tf: str, since_ts: int | None = None) -> pd.DataFrame:
        q = "SELECT ts, open, high, low, close, volume FROM candles WHERE symbol=? AND tf=?"
        args: list[Any] = [symbol, tf]
        if since_ts:
            q += " AND ts>=?"
            args.append(since_ts)
        q += " ORDER BY ts"
        with self._lock:
            rows = self._conn.execute(q, args).fetchall()
        if not rows:
            return empty_frame()
        df = pd.DataFrame(rows, columns=["ts", *COLUMNS])
        df.index = pd.to_datetime(df.pop("ts"), unit="s", utc=True).rename("time")
        return df.astype(float)

    def candle_stats(self) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT symbol, tf, COUNT(*) n, MIN(ts) first, MAX(ts) last FROM candles GROUP BY symbol, tf"
            ).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------ signals
    def insert_signal(self, sig: dict) -> int | None:
        cols = ["created_at", "symbol", "tf", "direction", "bar_ts", "entry", "sl", "tp1", "tp2", "tp3",
                "score", "reasons", "mtf", "status", "result", "result_r", "closed_at", "source"]
        data = {**sig}
        data.setdefault("created_at", int(time.time()))
        data.setdefault("status", "OPEN")
        data.setdefault("source", "LIVE")
        for k in ("reasons", "mtf"):
            if not isinstance(data.get(k), (str, type(None))):
                data[k] = json.dumps(data[k], ensure_ascii=False)
        with self._lock:
            cur = self._conn.execute(
                f"INSERT OR IGNORE INTO signals ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                [data.get(c) for c in cols])
            self._conn.commit()
            return cur.lastrowid if cur.rowcount else None

    def update_signal(self, sig_id: int, **fields) -> None:
        if not fields:
            return
        sets = ", ".join(f"{k}=?" for k in fields)
        with self._lock:
            self._conn.execute(f"UPDATE signals SET {sets} WHERE id=?", [*fields.values(), sig_id])
            self._conn.commit()

    def list_signals(self, symbol: str | None = None, direction: str | None = None,
                     min_score: int | None = None, result: str | None = None,
                     date_from: int | None = None, date_to: int | None = None,
                     source: str | None = None, limit: int = 500) -> list[dict]:
        q, args = "SELECT * FROM signals WHERE 1=1", []
        if symbol:
            q += " AND symbol=?"; args.append(symbol)
        if direction:
            q += " AND direction=?"; args.append(direction)
        if min_score is not None:
            q += " AND score>=?"; args.append(min_score)
        if result:
            if result == "OPEN":
                q += " AND status='OPEN'"
            elif result == "WIN":
                q += " AND result_r>0"
            elif result == "LOSS":
                q += " AND result_r<0"
            else:
                q += " AND result=?"; args.append(result)
        if date_from:
            q += " AND bar_ts>=?"; args.append(date_from)
        if date_to:
            q += " AND bar_ts<=?"; args.append(date_to)
        if source:
            q += " AND source=?"; args.append(source)
        q += " ORDER BY bar_ts DESC LIMIT ?"
        args.append(limit)
        with self._lock:
            rows = self._conn.execute(q, args).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            for k in ("reasons", "mtf"):
                try:
                    d[k] = json.loads(d[k]) if d[k] else None
                except (TypeError, ValueError):
                    pass
            out.append(d)
        return out

    def open_signals(self) -> list[dict]:
        return self.list_signals(result="OPEN", limit=10_000)

    def delete_signals(self, source: str) -> int:
        with self._lock:
            cur = self._conn.execute("DELETE FROM signals WHERE source=?", [source])
            self._conn.commit()
            return cur.rowcount

    # ------------------------------------------------------------------ alerts
    def log_alert(self, **entry) -> None:
        entry.setdefault("ts", int(time.time()))
        cols = ["ts", "signal_id", "symbol", "direction", "channel", "status", "message", "error"]
        with self._lock:
            self._conn.execute(
                f"INSERT INTO alerts_log ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                [entry.get(c) for c in cols])
            self._conn.commit()

    def last_alert_ts(self, symbol: str, direction: str) -> int | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT MAX(ts) t FROM alerts_log WHERE symbol=? AND direction=? AND status='sent'",
                [symbol, direction]).fetchone()
        return row["t"] if row and row["t"] else None

    def list_alerts(self, limit: int = 100, since_id: int = 0) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM alerts_log WHERE id>? ORDER BY id DESC LIMIT ?", [since_id, limit]).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------ settings
    def get_setting(self, key: str, default: Any = None) -> Any:
        with self._lock:
            row = self._conn.execute("SELECT value FROM settings WHERE key=?", [key]).fetchone()
        return json.loads(row["value"]) if row else default

    def set_setting(self, key: str, value: Any) -> None:
        with self._lock:
            self._conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
                               [key, json.dumps(value, ensure_ascii=False)])
            self._conn.commit()
