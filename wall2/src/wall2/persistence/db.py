"""SQLite store: signals (taken and skipped), trades, shadow results, cash snapshots, events.

Write volume is tiny (a few rows a minute), so plain synchronous sqlite3 in WAL mode is enough.
"""

from __future__ import annotations

import sqlite3
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from wall2.core.types import Bar

SCHEMA = """
CREATE TABLE IF NOT EXISTS signals (
    id INTEGER PRIMARY KEY,
    ts TEXT NOT NULL, day TEXT NOT NULL, underlying TEXT NOT NULL, direction TEXT NOT NULL,
    trigger_level REAL, price REAL, reason TEXT,
    outcome TEXT NOT NULL,              -- traded | skipped
    reject_reason TEXT,
    contract TEXT, delta REAL, delta_source TEXT, ask TEXT, contracts INTEGER
);
CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY,
    signal_id INTEGER, day TEXT NOT NULL, underlying TEXT NOT NULL, contract TEXT NOT NULL,
    right_ TEXT NOT NULL, strike TEXT NOT NULL, qty INTEGER NOT NULL, adopted INTEGER NOT NULL,
    entry_ts TEXT NOT NULL, entry_price TEXT NOT NULL, entry_fees TEXT NOT NULL, delta REAL,
    exit_ts TEXT, exit_price TEXT, exit_fees TEXT, exit_reason TEXT, unsold INTEGER, pnl TEXT,
    entry_spot REAL, exit_spot REAL, trigger_level REAL, screenshot TEXT
);
CREATE TABLE IF NOT EXISTS bars (
    underlying TEXT NOT NULL, start TEXT NOT NULL, open REAL, high REAL, low REAL, close REAL,
    volume REAL, PRIMARY KEY (underlying, start)
);
CREATE TABLE IF NOT EXISTS entry_bars (
    underlying TEXT NOT NULL, day TEXT NOT NULL, start TEXT NOT NULL,
    open REAL, high REAL, low REAL,
    close REAL, ema REAL, vwap REAL, PRIMARY KEY (underlying, start)
);
CREATE TABLE IF NOT EXISTS shadow (
    id INTEGER PRIMARY KEY,
    signal_id INTEGER NOT NULL, day TEXT NOT NULL, contract TEXT NOT NULL, qty INTEGER NOT NULL,
    entry_ts TEXT NOT NULL, entry_price TEXT NOT NULL,
    exit_ts TEXT, exit_price TEXT, exit_reason TEXT, pnl TEXT
);
CREATE TABLE IF NOT EXISTS cash (
    day TEXT PRIMARY KEY, settled_open TEXT, settled_close TEXT, unsettled_close TEXT
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY, ts TEXT NOT NULL, kind TEXT NOT NULL, detail TEXT
);
"""


def _s(x: Any) -> Any:
    if isinstance(x, Decimal):
        return str(x)
    if isinstance(x, (datetime, date)):
        return x.isoformat()
    return x


class Store:
    def __init__(self, path: str | Path) -> None:
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(path))
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(SCHEMA)

    def _insert(self, table: str, **cols: Any) -> int:
        keys = ", ".join(cols)
        qs = ", ".join("?" for _ in cols)
        cur = self.conn.execute(
            f"INSERT INTO {table} ({keys}) VALUES ({qs})", [_s(v) for v in cols.values()]
        )
        self.conn.commit()
        return int(cur.lastrowid or 0)

    def _update(self, table: str, row_id: int, **cols: Any) -> None:
        sets = ", ".join(f"{k} = ?" for k in cols)
        self.conn.execute(
            f"UPDATE {table} SET {sets} WHERE id = ?", [*(_s(v) for v in cols.values()), row_id]
        )
        self.conn.commit()

    def add_signal(self, **cols: Any) -> int:
        return self._insert("signals", **cols)

    def update_signal(self, signal_id: int, **cols: Any) -> None:
        self._update("signals", signal_id, **cols)

    def open_trade(self, **cols: Any) -> int:
        return self._insert("trades", **cols)

    def close_trade(self, trade_id: int, **cols: Any) -> None:
        self._update("trades", trade_id, **cols)

    def open_shadow(self, **cols: Any) -> int:
        return self._insert("shadow", **cols)

    def close_shadow(self, shadow_id: int, **cols: Any) -> None:
        self._update("shadow", shadow_id, **cols)

    def add_bar(self, underlying: str, b: Bar) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO bars VALUES (?, ?, ?, ?, ?, ?, ?)",
            [underlying, b.start.isoformat(), b.open, b.high, b.low, b.close, b.volume],
        )

    def add_entry_bar(self, underlying: str, b: Bar, ema: float | None, vwap: float | None) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO entry_bars VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                underlying,
                b.start.date().isoformat(),
                b.start.isoformat(),
                b.open,
                b.high,
                b.low,
                b.close,
                ema,
                vwap,
            ],
        )

    def commit(self) -> None:
        self.conn.commit()

    def event(self, ts: datetime, kind: str, detail: str = "") -> None:
        self._insert("events", ts=ts, kind=kind, detail=detail)

    def cash(self, day: date, **cols: Any) -> None:
        keys = ["day", *cols]
        self.conn.execute(
            f"INSERT INTO cash ({', '.join(keys)}) VALUES ({', '.join('?' for _ in keys)}) "
            f"ON CONFLICT(day) DO UPDATE SET {', '.join(f'{k}=excluded.{k}' for k in cols)}",
            [_s(day), *(_s(v) for v in cols.values())],
        )
        self.conn.commit()

    def rows(self, sql: str, *args: Any) -> list[sqlite3.Row]:
        return list(self.conn.execute(sql, [_s(a) for a in args]))
