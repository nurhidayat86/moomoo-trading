"""Rotating Parquet writers per symbol and partition date."""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from collector.partition import market_zone, partition_date
from collector.queues import OrderBookRecord, TickRecord

TICKER_SCHEMA = pa.schema(
    [
        ("code", pa.string()),
        ("recv_ts", pa.float64()),
        ("exchange_time", pa.string()),
        ("price", pa.float64()),
        ("volume", pa.int64()),
        ("turnover", pa.float64()),
        ("ticker_direction", pa.string()),
    ]
)

ORDERBOOK_SCHEMA = pa.schema(
    [
        ("code", pa.string()),
        ("recv_ts", pa.float64()),
        ("bid_json", pa.string()),
        ("ask_json", pa.string()),
        ("bid_levels", pa.int32()),
        ("ask_levels", pa.int32()),
        ("snapshot_hash", pa.string()),
    ]
)


@dataclass
class _OpenWriter:
    path: Path
    writer: pq.ParquetWriter
    opened_at: float
    partition: date
    part_index: int = 0


class RotatingParquetSink:
    def __init__(
        self,
        root: Path,
        stream: str,
        rotate_minutes: int,
        schema: pa.Schema,
    ) -> None:
        self._root = root / stream
        self._rotate_sec = rotate_minutes * 60
        self._schema = schema
        self._open: dict[tuple[str, date], _OpenWriter] = {}

    def _part_path(self, code: str, part_date: date, part_index: int) -> Path:
        symbol_dir = self._root / f"symbol={code}" / f"date={part_date.isoformat()}"
        symbol_dir.mkdir(parents=True, exist_ok=True)
        return symbol_dir / f"part-{part_index:05d}.parquet"

    def _should_rotate(self, handle: _OpenWriter) -> bool:
        return (time.time() - handle.opened_at) >= self._rotate_sec

    def _close_handle(self, key: tuple[str, date]) -> None:
        handle = self._open.pop(key, None)
        if handle is not None:
            handle.writer.close()

    def _get_writer(self, code: str, part_date: date) -> _OpenWriter:
        key = (code, part_date)
        handle = self._open.get(key)
        if handle is not None and not self._should_rotate(handle):
            return handle

        if handle is not None:
            self._close_handle(key)
            part_index = handle.part_index + 1
        else:
            part_index = 0

        path = self._part_path(code, part_date, part_index)
        writer = pq.ParquetWriter(path, self._schema, compression="zstd")
        handle = _OpenWriter(
            path=path,
            writer=writer,
            opened_at=time.time(),
            partition=part_date,
            part_index=part_index,
        )
        self._open[key] = handle
        return handle

    def write_ticks(self, rows: list[TickRecord]) -> None:
        if not rows:
            return
        by_key: dict[tuple[str, date], list[TickRecord]] = {}
        for row in rows:
            part = partition_date(row.code, row.exchange_time)
            by_key.setdefault((row.code, part), []).append(row)

        for (code, part), group in by_key.items():
            handle = self._get_writer(code, part)
            table = pa.Table.from_pylist(
                [
                    {
                        "code": r.code,
                        "recv_ts": r.recv_ts,
                        "exchange_time": r.exchange_time,
                        "price": r.price,
                        "volume": r.volume,
                        "turnover": r.turnover,
                        "ticker_direction": r.ticker_direction,
                    }
                    for r in group
                ],
                schema=self._schema,
            )
            handle.writer.write_table(table)

    def write_orderbooks(self, rows: list[OrderBookRecord]) -> None:
        if not rows:
            return
        by_key: dict[tuple[str, date], list[OrderBookRecord]] = {}
        for row in rows:
            zone = market_zone(row.code)
            part = datetime.fromtimestamp(row.recv_ts, tz=zone).date()
            by_key.setdefault((row.code, part), []).append(row)

        for (code, part), group in by_key.items():
            handle = self._get_writer(code, part)
            table = pa.Table.from_pylist(
                [
                    {
                        "code": r.code,
                        "recv_ts": r.recv_ts,
                        "bid_json": r.bid_json,
                        "ask_json": r.ask_json,
                        "bid_levels": r.bid_levels,
                        "ask_levels": r.ask_levels,
                        "snapshot_hash": r.snapshot_hash,
                    }
                    for r in group
                ],
                schema=self._schema,
            )
            handle.writer.write_table(table)

    def close(self) -> None:
        for key in list(self._open):
            self._close_handle(key)


class ParquetSinks:
    def __init__(self, parquet_root: Path, ticker_rotate: int, orderbook_rotate: int) -> None:
        self.ticker = RotatingParquetSink(
            parquet_root, "ticker", ticker_rotate, TICKER_SCHEMA
        )
        self.orderbook = RotatingParquetSink(
            parquet_root, "orderbook", orderbook_rotate, ORDERBOOK_SCHEMA
        )

    def close(self) -> None:
        self.ticker.close()
        self.orderbook.close()
