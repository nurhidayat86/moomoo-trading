"""JSONL spill files when the ingest queue is full."""

from __future__ import annotations

import json
import threading
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from collector.queues import OrderBookRecord, Record, StreamType, TickRecord


class SpillWriter:
    def __init__(self, spill_root: Path) -> None:
        self._root = spill_root
        self._lock = threading.Lock()
        self._count = 0

    @property
    def count(self) -> int:
        return self._count

    def _day_dir(self, stream: StreamType) -> Path:
        day = datetime.now(timezone.utc).strftime("%Y%m%d")
        path = self._root / stream.value / day
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _append(self, stream: StreamType, record: Record) -> None:
        payload = {"stream": stream.value, **asdict(record)}
        path = self._day_dir(stream) / "spill.jsonl"
        line = json.dumps(payload, separators=(",", ":")) + "\n"
        with self._lock:
            with path.open("a", encoding="utf-8") as handle:
                handle.write(line)
            self._count += 1

    def append_ticker(self, record: TickRecord) -> None:
        self._append(StreamType.TICKER, record)

    def append_orderbook(self, record: OrderBookRecord) -> None:
        self._append(StreamType.ORDER_BOOK, record)


def iter_spill_files(spill_root: Path) -> Iterator[Path]:
    if not spill_root.is_dir():
        return
    for stream_dir in sorted(spill_root.iterdir()):
        if not stream_dir.is_dir():
            continue
        for day_dir in sorted(stream_dir.iterdir()):
            if not day_dir.is_dir():
                continue
            spill_file = day_dir / "spill.jsonl"
            if spill_file.is_file():
                yield spill_file


def load_spill_record(line: str) -> Record:
    data = json.loads(line)
    stream = data.pop("stream")
    if stream == StreamType.TICKER.value:
        return TickRecord(**data)
    return OrderBookRecord(**data)
