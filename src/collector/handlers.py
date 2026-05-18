"""Push handlers: enqueue only, no I/O."""

from __future__ import annotations

import hashlib
import json
import queue
import time
from typing import Any

from moomoo import OrderBookHandlerBase, RET_OK, TickerHandlerBase

from collector.queues import OrderBookRecord, Record, TickRecord
from collector.spill import SpillWriter


class TickerHandler(TickerHandlerBase):
    def __init__(
        self,
        out_queue: queue.Queue[Record | None],
        spill: SpillWriter,
    ) -> None:
        super().__init__()
        self._queue = out_queue
        self._spill = spill

    def on_recv_rsp(self, rsp_pb):
        ret, content = super().on_recv_rsp(rsp_pb)
        if ret != RET_OK or content is None:
            return ret, content

        recv_ts = time.time()
        for _, row in content.iterrows():
            record = TickRecord(
                code=str(row["code"]),
                recv_ts=recv_ts,
                exchange_time=str(row["time"]),
                price=float(row["price"]),
                volume=int(row["volume"]),
                turnover=float(row["turnover"]),
                ticker_direction=str(row.get("ticker_direction", "")),
            )
            self._enqueue(record)
        return ret, content

    def _enqueue(self, record: Record) -> None:
        try:
            self._queue.put_nowait(record)
        except queue.Full:
            self._spill.append_ticker(record)


class OrderBookHandler(OrderBookHandlerBase):
    def __init__(
        self,
        out_queue: queue.Queue[Record | None],
        spill: SpillWriter,
    ) -> None:
        super().__init__()
        self._queue = out_queue
        self._spill = spill

    def on_recv_rsp(self, rsp_pb):
        ret, content = super().on_recv_rsp(rsp_pb)
        if ret != RET_OK or content is None:
            return ret, content

        recv_ts = time.time()
        if isinstance(content, dict):
            items = [content]
        else:
            items = list(content)

        for data in items:
            record = self._to_record(data, recv_ts)
            if record is not None:
                self._enqueue(record)
        return ret, content

    @staticmethod
    def _to_record(data: Any, recv_ts: float) -> OrderBookRecord | None:
        code = data.get("code")
        if not code:
            return None

        bid = data.get("Bid") or data.get("bid") or []
        ask = data.get("Ask") or data.get("ask") or []
        bid_json = json.dumps(bid, separators=(",", ":"), sort_keys=True)
        ask_json = json.dumps(ask, separators=(",", ":"), sort_keys=True)
        digest = hashlib.sha256(f"{bid_json}|{ask_json}".encode()).hexdigest()

        return OrderBookRecord(
            code=str(code),
            recv_ts=recv_ts,
            bid_json=bid_json,
            ask_json=ask_json,
            bid_levels=len(bid),
            ask_levels=len(ask),
            snapshot_hash=digest,
        )

    def _enqueue(self, record: Record) -> None:
        try:
            self._queue.put_nowait(record)
        except queue.Full:
            self._spill.append_orderbook(record)
