import json
import queue
from pathlib import Path

from collector.queues import TickRecord
from collector.spill import SpillWriter, load_spill_record


def test_spill_write_and_load_roundtrip(tmp_path: Path):
    spill = SpillWriter(tmp_path)
    record = TickRecord(
        code="US.AAPL",
        recv_ts=1.5,
        exchange_time="2024-01-02 10:00:00",
        price=1.0,
        volume=1,
        turnover=1.0,
        ticker_direction="",
    )
    spill.append_ticker(record)
    assert spill.count == 1

    spill_file = next(tmp_path.rglob("spill.jsonl"))
    line = spill_file.read_text(encoding="utf-8").strip()
    loaded = load_spill_record(line)
    assert loaded == record


def test_spill_replay_ordering(tmp_path: Path):
    spill = SpillWriter(tmp_path)
    q: queue.Queue = queue.Queue(maxsize=100)
    for i in range(3):
        spill.append_ticker(
            TickRecord(
                code="US.AAPL",
                recv_ts=float(i),
                exchange_time=f"2024-01-02 10:00:0{i}",
                price=float(i),
                volume=i,
                turnover=0.0,
                ticker_direction="",
            )
        )

    lines = next(tmp_path.rglob("spill.jsonl")).read_text().splitlines()
    order = []
    for line in lines:
        rec = load_spill_record(line)
        q.put_nowait(rec)
        order.append(rec.volume)

    assert order == [0, 1, 2]
