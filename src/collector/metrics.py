"""In-memory ingest counters."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field


@dataclass
class IngestMetrics:
    ticks_written: int = 0
    orderbooks_written: int = 0
    ticks_dropped_spill: int = 0
    orderbooks_dropped_spill: int = 0
    queue_high_water: int = 0
    last_tick_recv: float | None = None
    last_book_recv: float | None = None
    connected: bool = False
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def note_tick_recv(self) -> None:
        with self._lock:
            self.last_tick_recv = time.time()

    def note_book_recv(self) -> None:
        with self._lock:
            self.last_book_recv = time.time()

    def note_queue_depth(self, depth: int) -> None:
        with self._lock:
            if depth > self.queue_high_water:
                self.queue_high_water = depth

    def snapshot(self) -> dict[str, int | float | bool | None]:
        with self._lock:
            return {
                "ticks_written": self.ticks_written,
                "orderbooks_written": self.orderbooks_written,
                "ticks_dropped_spill": self.ticks_dropped_spill,
                "orderbooks_dropped_spill": self.orderbooks_dropped_spill,
                "queue_high_water": self.queue_high_water,
                "last_tick_recv": self.last_tick_recv,
                "last_book_recv": self.last_book_recv,
                "connected": self.connected,
            }
