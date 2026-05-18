"""Periodic health.json writer."""

from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path

from collector.metrics import IngestMetrics

logger = logging.getLogger(__name__)


class HealthReporter:
    def __init__(
        self,
        path: Path,
        metrics: IngestMetrics,
        queue_depth_fn,
        spill_count_fn,
        interval_sec: float,
        stop_event: threading.Event,
    ) -> None:
        self._path = path
        self._metrics = metrics
        self._queue_depth_fn = queue_depth_fn
        self._spill_count_fn = spill_count_fn
        self._interval = interval_sec
        self._stop = stop_event
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._thread = threading.Thread(target=self._run, name="health", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        while not self._stop.wait(self._interval):
            self.write_once()

    def write_once(self) -> None:
        payload = {
            "ts": time.time(),
            **self._metrics.snapshot(),
            "queue_depth": self._queue_depth_fn(),
            "spill_count": self._spill_count_fn(),
        }
        tmp = self._path.with_suffix(".tmp")
        try:
            tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            tmp.replace(self._path)
        except OSError as exc:
            logger.warning("Failed to write health.json: %s", exc)

    def stop(self) -> None:
        self.write_once()
        if self._thread is not None:
            self._thread.join(timeout=self._interval + 1)
