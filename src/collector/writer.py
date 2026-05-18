"""Single writer thread: queue drain, SQLite, Parquet, spill replay."""

from __future__ import annotations

import logging
import queue
import threading
import time
from typing import Callable

from config_loader import AppConfig
from collector.metrics import IngestMetrics
from collector.parquet_sink import ParquetSinks
from collector.queues import OrderBookRecord, Record, StreamType, TickRecord
from collector.spill import SpillWriter, iter_spill_files, load_spill_record
from collector.sqlite_sink import SqliteSink

logger = logging.getLogger(__name__)


class WriterThread:
    def __init__(
        self,
        config: AppConfig,
        in_queue: queue.Queue[Record | None],
        sqlite: SqliteSink,
        parquet: ParquetSinks,
        spill: SpillWriter,
        metrics: IngestMetrics,
        stop_event: threading.Event,
    ) -> None:
        self._config = config
        self._queue = in_queue
        self._sqlite = sqlite
        self._parquet = parquet
        self._spill = spill
        self._metrics = metrics
        self._stop = stop_event
        self._thread: threading.Thread | None = None
        self._last_checkpoint = 0.0
        self._last_prune = 0.0
        self._hour_bucket: int | None = None
        self._hour_counts: dict[str, int] = {"ticker": 0, "orderbook": 0}
        self._last_tick_exchange: str | None = None
        self._last_tick_recv: float | None = None
        self._last_book_recv: float | None = None
        self._processed_spill: set[str] = set()

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="writer", daemon=True)
        self._thread.start()

    def join(self, timeout: float | None = None) -> None:
        if self._thread is not None:
            self._thread.join(timeout=timeout)

    def _run(self) -> None:
        coll = self._config.collector
        batch_size = coll.writer_batch_size
        flush_interval = coll.writer_flush_interval_sec

        tick_buf: list[TickRecord] = []
        book_buf: list[OrderBookRecord] = []
        last_flush = time.monotonic()

        while True:
            if self._stop.is_set() and self._queue.empty():
                self._flush(tick_buf, book_buf)
                self._maybe_checkpoint(force=True)
                self._maybe_flush_hourly(force=True)
                break

            try:
                item = self._queue.get(timeout=flush_interval)
            except queue.Empty:
                item = None

            if item is None:
                if self._stop.is_set():
                    continue
                self._replay_spill_when_idle()
            else:
                if isinstance(item, TickRecord):
                    tick_buf.append(item)
                    self._metrics.note_tick_recv()
                    self._last_tick_exchange = item.exchange_time
                    self._last_tick_recv = item.recv_ts
                else:
                    book_buf.append(item)
                    self._metrics.note_book_recv()
                    self._last_book_recv = item.recv_ts

            depth = self._queue.qsize()
            self._metrics.note_queue_depth(depth)

            now = time.monotonic()
            if (
                len(tick_buf) + len(book_buf) >= batch_size
                or (tick_buf or book_buf) and now - last_flush >= flush_interval
            ):
                self._flush(tick_buf, book_buf)
                tick_buf.clear()
                book_buf.clear()
                last_flush = now
                self._replay_spill_when_idle()
                self._maybe_checkpoint()
                self._maybe_flush_hourly()
                self._maybe_prune()

        logger.info("Writer thread stopped")

    def _flush(self, ticks: list[TickRecord], books: list[OrderBookRecord]) -> None:
        if ticks:
            self._sqlite.insert_ticks(ticks)
            self._parquet.ticker.write_ticks(ticks)
            self._metrics.ticks_written += len(ticks)
            self._hour_counts["ticker"] += len(ticks)
        if books:
            self._sqlite.insert_orderbooks(books)
            self._parquet.orderbook.write_orderbooks(books)
            self._metrics.orderbooks_written += len(books)
            self._hour_counts["orderbook"] += len(books)

    def _maybe_checkpoint(self, force: bool = False) -> None:
        interval = self._config.collector.sqlite_checkpoint_interval_sec
        now = time.time()
        if not force and now - self._last_checkpoint < interval:
            return
        self._last_checkpoint = now
        if self._last_tick_exchange or self._last_tick_recv:
            self._sqlite.update_checkpoint(
                StreamType.TICKER.value,
                self._last_tick_exchange,
                self._last_tick_recv,
            )
        if self._last_book_recv:
            self._sqlite.update_checkpoint(
                StreamType.ORDER_BOOK.value,
                None,
                self._last_book_recv,
            )

    def _maybe_flush_hourly(self, force: bool = False) -> None:
        bucket = int(time.time() // 3600)
        if self._hour_bucket is None:
            self._hour_bucket = bucket
            return
        if not force and bucket == self._hour_bucket:
            return
        self._sqlite.flush_hourly(self._hour_bucket, dict(self._hour_counts))
        self._hour_bucket = bucket
        self._hour_counts = {"ticker": 0, "orderbook": 0}

    def _maybe_prune(self) -> None:
        now = time.time()
        if now - self._last_prune < 3600:
            return
        self._last_prune = now
        coll = self._config.collector
        ticks_before = now - coll.ticks_sqlite_retention_hours * 3600
        books_before = now - coll.orderbook_sqlite_retention_hours * 3600
        deleted = self._sqlite.prune(ticks_before, books_before)
        logger.info("SQLite prune: ticks=%s orderbook=%s", deleted[0], deleted[1])

    def _replay_spill_when_idle(self) -> None:
        if self._queue.qsize() > self._config.collector.writer_batch_size:
            return
        for spill_file in iter_spill_files(self._config.paths.spill_dir):
            key = str(spill_file)
            if key in self._processed_spill:
                continue
            try:
                lines = spill_file.read_text(encoding="utf-8").splitlines()
            except OSError as exc:
                logger.warning("Cannot read spill %s: %s", spill_file, exc)
                continue
            for line in lines:
                if not line.strip():
                    continue
                try:
                    record = load_spill_record(line)
                    self._queue.put_nowait(record)
                except (queue.Full, ValueError) as exc:
                    logger.debug("Spill replay deferred: %s", exc)
                    return
            self._processed_spill.add(key)
            try:
                spill_file.unlink()
            except OSError:
                pass
