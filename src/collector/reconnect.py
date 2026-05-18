"""Exponential backoff for reconnect attempts."""

from __future__ import annotations


class ReconnectBackoff:
    def __init__(self, initial_sec: float, max_sec: float) -> None:
        self._initial = initial_sec
        self._max = max_sec
        self._attempt = 0

    def reset(self) -> None:
        self._attempt = 0

    def next_delay(self) -> float:
        delay = min(self._initial * (2**self._attempt), self._max)
        self._attempt += 1
        return delay
