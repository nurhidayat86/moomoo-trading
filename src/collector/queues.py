"""In-process record types and queue factory."""

from __future__ import annotations

import queue
from dataclasses import dataclass
from enum import Enum
from typing import Any


class StreamType(str, Enum):
    TICKER = "ticker"
    ORDER_BOOK = "orderbook"


@dataclass(frozen=True, slots=True)
class TickRecord:
    code: str
    recv_ts: float
    exchange_time: str
    price: float
    volume: int
    turnover: float
    ticker_direction: str


@dataclass(frozen=True, slots=True)
class OrderBookRecord:
    code: str
    recv_ts: float
    bid_json: str
    ask_json: str
    bid_levels: int
    ask_levels: int
    snapshot_hash: str


Record = TickRecord | OrderBookRecord


def make_queue(maxsize: int) -> queue.Queue[Record | None]:
    return queue.Queue(maxsize=maxsize)
