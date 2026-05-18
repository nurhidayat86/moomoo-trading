"""Load and validate collector configuration from YAML."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from opend_env import PROJECT_ROOT

SYMBOL_RE = re.compile(r"^(US|HK|SH|SZ|SG|CC)\.[A-Za-z0-9.]+$")
VALID_SESSIONS = frozenset({"NONE", "RTH", "ETH", "ALL"})


@dataclass(frozen=True)
class CollectorConfig:
    extended_time: bool
    session: str
    queue_maxsize: int
    writer_batch_size: int
    writer_flush_interval_sec: float
    ticker_rotate_minutes: int
    orderbook_rotate_minutes: int
    sqlite_checkpoint_interval_sec: float
    reconnect_initial_sec: float
    reconnect_max_sec: float
    is_first_push: bool
    ticks_sqlite_retention_hours: int
    orderbook_sqlite_retention_hours: int
    health_interval_sec: float
    shutdown_drain_timeout_sec: float


@dataclass(frozen=True)
class PathsConfig:
    data_root: Path

    @property
    def collector_dir(self) -> Path:
        return self.data_root / "collector"

    @property
    def state_db(self) -> Path:
        return self.collector_dir / "state.db"

    @property
    def spill_dir(self) -> Path:
        return self.data_root / "spill"

    @property
    def parquet_dir(self) -> Path:
        return self.data_root / "parquet"

    @property
    def health_file(self) -> Path:
        return self.collector_dir / "health.json"


@dataclass(frozen=True)
class LoggingConfig:
    level: str
    json: bool


@dataclass(frozen=True)
class AppConfig:
    symbols: tuple[str, ...]
    collector: CollectorConfig
    paths: PathsConfig
    logging: LoggingConfig

    @property
    def required_subscription_slots(self) -> int:
        return 2 * len(self.symbols)


def _resolve_path(raw: str) -> Path:
    path = Path(raw)
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path.resolve()


def load_config(path: Path | str) -> AppConfig:
    config_path = Path(path)
    if not config_path.is_file():
        raise FileNotFoundError(f"Config not found: {config_path}")

    with config_path.open(encoding="utf-8") as handle:
        raw: dict[str, Any] = yaml.safe_load(handle) or {}

    symbols_raw = raw.get("symbols") or []
    if not isinstance(symbols_raw, list) or not symbols_raw:
        raise ValueError("config.symbols must be a non-empty list")

    symbols: list[str] = []
    seen: set[str] = set()
    for item in symbols_raw:
        code = str(item).strip().upper()
        if not SYMBOL_RE.match(code):
            raise ValueError(f"Invalid symbol format: {item!r}")
        if code in seen:
            continue
        seen.add(code)
        symbols.append(code)

    if not symbols:
        raise ValueError("config.symbols is empty after deduplication")

    coll = raw.get("collector") or {}
    session = str(coll.get("session", "NONE")).strip().upper()
    if session == "OVERNIGHT":
        raise ValueError("collector.session OVERNIGHT is not supported for subscribe")
    if session not in VALID_SESSIONS:
        raise ValueError(f"collector.session must be one of {sorted(VALID_SESSIONS)}")

    collector = CollectorConfig(
        extended_time=bool(coll.get("extended_time", True)),
        session=session,
        queue_maxsize=int(coll.get("queue_maxsize", 100_000)),
        writer_batch_size=int(coll.get("writer_batch_size", 1000)),
        writer_flush_interval_sec=float(coll.get("writer_flush_interval_sec", 0.25)),
        ticker_rotate_minutes=int(coll.get("ticker_rotate_minutes", 15)),
        orderbook_rotate_minutes=int(coll.get("orderbook_rotate_minutes", 5)),
        sqlite_checkpoint_interval_sec=float(
            coll.get("sqlite_checkpoint_interval_sec", 30)
        ),
        reconnect_initial_sec=float(coll.get("reconnect_initial_sec", 1)),
        reconnect_max_sec=float(coll.get("reconnect_max_sec", 60)),
        is_first_push=bool(coll.get("is_first_push", True)),
        ticks_sqlite_retention_hours=int(coll.get("ticks_sqlite_retention_hours", 48)),
        orderbook_sqlite_retention_hours=int(
            coll.get("orderbook_sqlite_retention_hours", 24)
        ),
        health_interval_sec=float(coll.get("health_interval_sec", 60)),
        shutdown_drain_timeout_sec=float(coll.get("shutdown_drain_timeout_sec", 30)),
    )

    paths_raw = raw.get("paths") or {}
    paths = PathsConfig(data_root=_resolve_path(str(paths_raw.get("data_root", "data"))))

    log_raw = raw.get("logging") or {}
    logging_cfg = LoggingConfig(
        level=str(log_raw.get("level", "INFO")).upper(),
        json=bool(log_raw.get("json", False)),
    )

    return AppConfig(
        symbols=tuple(symbols),
        collector=collector,
        paths=paths,
        logging=logging_cfg,
    )


def session_enum(session_name: str):
    from moomoo import Session

    return {
        "NONE": Session.NONE,
        "RTH": Session.RTH,
        "ETH": Session.ETH,
        "ALL": Session.ALL,
    }[session_name]


def check_subscription_quota(quote_ctx, config: AppConfig) -> dict[str, int]:
    """Return subscription usage; raise if insufficient remain quota."""
    from moomoo import RET_OK

    required = config.required_subscription_slots

    ret, user_raw = quote_ctx.get_user_info()
    if ret != RET_OK:
        raise RuntimeError(f"get_user_info failed: {user_raw}")

    ret, sub_raw = quote_ctx.query_subscription(is_all_conn=True)
    if ret != RET_OK:
        raise RuntimeError(f"query_subscription failed: {sub_raw}")

    if isinstance(user_raw, dict):
        sub_quota = int(user_raw.get("sub_quota", 0) or 0)
    else:
        sub_quota = int(getattr(user_raw, "sub_quota", 0) or 0)

    if not isinstance(sub_raw, dict):
        raise RuntimeError(f"unexpected query_subscription payload: {sub_raw}")

    remain = int(sub_raw.get("remain", 0) or 0)
    total_used = int(sub_raw.get("total_used", 0) or 0)

    if remain < required:
        raise RuntimeError(
            f"Insufficient subscription quota: need {required} slots "
            f"(2 × {len(config.symbols)} symbols), remain={remain}, "
            f"tier_limit={sub_quota}, used={total_used}"
        )

    return {
        "required": required,
        "remain": remain,
        "total_used": total_used,
        "tier_limit": sub_quota,
    }
