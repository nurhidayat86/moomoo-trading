from pathlib import Path

from collector.queues import TickRecord
from collector.sqlite_sink import SqliteSink


def test_tick_primary_key_dedup(tmp_path: Path):
    db = SqliteSink(tmp_path / "state.db")
    row = TickRecord(
        code="US.AAPL",
        recv_ts=1.0,
        exchange_time="2024-01-02 10:00:00",
        price=100.0,
        volume=10,
        turnover=1000.0,
        ticker_direction="BUY",
    )
    db.insert_ticks([row, row])
    cur = db._conn.execute("SELECT COUNT(*) FROM ticks")
    assert cur.fetchone()[0] == 1
    db.close()
