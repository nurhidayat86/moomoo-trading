"""SQLite operational store."""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

from collector.queues import OrderBookRecord, TickRecord

DDL = """
CREATE TABLE IF NOT EXISTS ticks (
    code TEXT NOT NULL,
    exchange_time TEXT NOT NULL,
    recv_ts REAL NOT NULL,
    price REAL NOT NULL,
    volume INTEGER NOT NULL,
    turnover REAL NOT NULL,
    ticker_direction TEXT,
    PRIMARY KEY (code, exchange_time, price, volume)
);

CREATE TABLE IF NOT EXISTS orderbook_snapshots (
    code TEXT NOT NULL,
    recv_ts REAL NOT NULL,
    snapshot_hash TEXT NOT NULL,
    bid_json TEXT NOT NULL,
    ask_json TEXT NOT NULL,
    bid_levels INTEGER NOT NULL,
    ask_levels INTEGER NOT NULL,
    PRIMARY KEY (code, recv_ts, snapshot_hash)
);

CREATE TABLE IF NOT EXISTS stream_checkpoint (
    stream TEXT NOT NULL PRIMARY KEY,
    last_exchange_time TEXT,
    last_recv_ts REAL,
    updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS connection_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event TEXT NOT NULL,
    detail TEXT,
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS ingest_hourly (
    hour_ts INTEGER NOT NULL,
    stream TEXT NOT NULL,
    count INTEGER NOT NULL,
    PRIMARY KEY (hour_ts, stream)
);
"""


class SqliteSink:
    def __init__(self, db_path: Path) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.executescript(DDL)
        self._conn.commit()

    def insert_ticks(self, rows: list[TickRecord]) -> int:
        if not rows:
            return 0
        cur = self._conn.cursor()
        cur.executemany(
            """
            INSERT OR IGNORE INTO ticks
            (code, exchange_time, recv_ts, price, volume, turnover, ticker_direction)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    r.code,
                    r.exchange_time,
                    r.recv_ts,
                    r.price,
                    r.volume,
                    r.turnover,
                    r.ticker_direction,
                )
                for r in rows
            ],
        )
        self._conn.commit()
        return cur.rowcount if cur.rowcount >= 0 else len(rows)

    def insert_orderbooks(self, rows: list[OrderBookRecord]) -> int:
        if not rows:
            return 0
        cur = self._conn.cursor()
        cur.executemany(
            """
            INSERT OR IGNORE INTO orderbook_snapshots
            (code, recv_ts, snapshot_hash, bid_json, ask_json, bid_levels, ask_levels)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    r.code,
                    r.recv_ts,
                    r.snapshot_hash,
                    r.bid_json,
                    r.ask_json,
                    r.bid_levels,
                    r.ask_levels,
                )
                for r in rows
            ],
        )
        self._conn.commit()
        return cur.rowcount if cur.rowcount >= 0 else len(rows)

    def update_checkpoint(
        self,
        stream: str,
        last_exchange_time: str | None,
        last_recv_ts: float | None,
    ) -> None:
        self._conn.execute(
            """
            INSERT INTO stream_checkpoint (stream, last_exchange_time, last_recv_ts, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(stream) DO UPDATE SET
                last_exchange_time=excluded.last_exchange_time,
                last_recv_ts=excluded.last_recv_ts,
                updated_at=excluded.updated_at
            """,
            (stream, last_exchange_time, last_recv_ts, time.time()),
        )
        self._conn.commit()

    def log_connection(self, event: str, detail: str | None = None) -> None:
        self._conn.execute(
            "INSERT INTO connection_log (event, detail, created_at) VALUES (?, ?, ?)",
            (event, detail, time.time()),
        )
        self._conn.commit()

    def flush_hourly(self, hour_ts: int, counts: dict[str, int]) -> None:
        for stream, count in counts.items():
            if count <= 0:
                continue
            self._conn.execute(
                """
                INSERT INTO ingest_hourly (hour_ts, stream, count) VALUES (?, ?, ?)
                ON CONFLICT(hour_ts, stream) DO UPDATE SET count = count + excluded.count
                """,
                (hour_ts, stream, count),
            )
        self._conn.commit()

    def prune(self, ticks_before: float, books_before: float) -> tuple[int, int]:
        cur = self._conn.cursor()
        cur.execute("DELETE FROM ticks WHERE recv_ts < ?", (ticks_before,))
        ticks_deleted = cur.rowcount
        cur.execute("DELETE FROM orderbook_snapshots WHERE recv_ts < ?", (books_before,))
        books_deleted = cur.rowcount
        self._conn.commit()
        return ticks_deleted, books_deleted

    def close(self) -> None:
        self._conn.close()
