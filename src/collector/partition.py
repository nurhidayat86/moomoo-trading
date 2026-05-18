"""Exchange-local calendar dates for Parquet partitioning."""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

US_TZ = ZoneInfo("America/New_York")
ASIA_TZ = ZoneInfo("Asia/Shanghai")


def market_zone(code: str) -> ZoneInfo:
    if code.startswith("US."):
        return US_TZ
    return ASIA_TZ


def parse_exchange_time(exchange_time: str, zone: ZoneInfo) -> datetime:
    text = exchange_time.strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M:%S.%f"):
        try:
            naive = datetime.strptime(text, fmt)
            return naive.replace(tzinfo=zone)
        except ValueError:
            continue
    raise ValueError(f"Unrecognized exchange_time: {exchange_time!r}")


def partition_date(code: str, exchange_time: str) -> date:
    zone = market_zone(code)
    return parse_exchange_time(exchange_time, zone).date()
