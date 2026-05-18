#!/usr/bin/env python3
"""24/7 moomoo TICKER + ORDER_BOOK collector."""

from __future__ import annotations

import argparse
import logging
import signal
import sys
import threading
import time
from pathlib import Path

# Scripts live in src/collector/; add src/ for opend_env, config_loader, quote_context.
_SRC = Path(__file__).resolve().parent.parent
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from config_loader import AppConfig, load_config, session_enum  # noqa: E402
from collector.handlers import OrderBookHandler, TickerHandler  # noqa: E402
from collector.health import HealthReporter  # noqa: E402
from collector.metrics import IngestMetrics  # noqa: E402
from collector.parquet_sink import ParquetSinks  # noqa: E402
from collector.queues import make_queue  # noqa: E402
from collector.reconnect import ReconnectBackoff  # noqa: E402
from collector.spill import SpillWriter  # noqa: E402
from collector.sqlite_sink import SqliteSink  # noqa: E402
from collector.startup import run_startup_checks  # noqa: E402
from collector.writer import WriterThread  # noqa: E402
from opend_env import configure_encryption, load_dotenv, load_settings  # noqa: E402
from quote_context import create_quote_context  # noqa: E402


def _configure_logging(config: AppConfig) -> None:
    level = getattr(logging, config.logging.level, logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )


def _subscribe(quote_ctx, config: AppConfig) -> None:
    from moomoo import RET_OK, SubType

    coll = config.collector
    ret, msg = quote_ctx.subscribe(
        list(config.symbols),
        [SubType.TICKER, SubType.ORDER_BOOK],
        is_first_push=coll.is_first_push,
        subscribe_push=True,
        extended_time=coll.extended_time,
        session=session_enum(coll.session),
    )
    if ret != RET_OK:
        raise RuntimeError(f"subscribe failed: {msg}")


def _run_collector(config: AppConfig) -> None:
    load_dotenv()
    settings = load_settings()
    configure_encryption(settings)

    stop_event = threading.Event()

    def _handle_signal(signum, _frame) -> None:
        logging.getLogger(__name__).info("Signal %s received, shutting down", signum)
        stop_event.set()

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    quota = run_startup_checks(settings, config, dry_run=False)
    logging.info(
        "Subscription quota OK: required=%s remain=%s used=%s",
        quota["required"],
        quota["remain"],
        quota["total_used"],
    )

    config.paths.data_root.mkdir(parents=True, exist_ok=True)
    metrics = IngestMetrics()
    in_queue = make_queue(config.collector.queue_maxsize)
    spill = SpillWriter(config.paths.spill_dir)
    sqlite = SqliteSink(config.paths.state_db)
    parquet = ParquetSinks(
        config.paths.parquet_dir,
        config.collector.ticker_rotate_minutes,
        config.collector.orderbook_rotate_minutes,
    )

    writer = WriterThread(
        config, in_queue, sqlite, parquet, spill, metrics, stop_event
    )
    writer.start()

    health = HealthReporter(
        config.paths.health_file,
        metrics,
        queue_depth_fn=in_queue.qsize,
        spill_count_fn=lambda: spill.count,
        interval_sec=config.collector.health_interval_sec,
        stop_event=stop_event,
    )
    health.start()

    backoff = ReconnectBackoff(
        config.collector.reconnect_initial_sec,
        config.collector.reconnect_max_sec,
    )
    quote_ctx = None
    ticker_handler = None
    book_handler = None

    try:
        while not stop_event.is_set():
            try:
                quote_ctx = create_quote_context(settings)
                ticker_handler = TickerHandler(in_queue, spill)
                book_handler = OrderBookHandler(in_queue, spill)
                quote_ctx.set_handler(ticker_handler)
                quote_ctx.set_handler(book_handler)
                quote_ctx.start()

                _subscribe(quote_ctx, config)
                metrics.connected = True
                sqlite.log_connection("connected", "subscribed")
                backoff.reset()
                logging.info("Subscribed to %s", ", ".join(config.symbols))

                while not stop_event.is_set():
                    ret, state = quote_ctx.get_global_state()
                    from moomoo import RET_OK

                    if ret != RET_OK:
                        raise RuntimeError(f"get_global_state failed: {state}")
                    time.sleep(5)

            except Exception as exc:
                metrics.connected = False
                sqlite.log_connection("disconnected", str(exc))
                logging.exception("Collector error: %s", exc)
                if quote_ctx is not None:
                    try:
                        quote_ctx.close()
                    except Exception:
                        pass
                    quote_ctx = None

                if stop_event.is_set():
                    break

                delay = backoff.next_delay()
                logging.info("Reconnecting in %.1fs", delay)
                stop_event.wait(delay)

    finally:
        metrics.connected = False
        stop_event.set()
        in_queue.put(None)

        drain = config.collector.shutdown_drain_timeout_sec
        writer.join(timeout=drain)
        health.stop()
        parquet.close()
        sqlite.close()
        if quote_ctx is not None:
            try:
                quote_ctx.close()
            except Exception:
                pass
        logging.info("Collector exited")


def main() -> None:
    parser = argparse.ArgumentParser(description="Moomoo TICKER + ORDER_BOOK collector")
    parser.add_argument(
        "--config",
        default=str(_SRC / "config.yaml.example"),
        help="Path to YAML config",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate config and quota only",
    )
    args = parser.parse_args()

    config = load_config(args.config)
    _configure_logging(config)

    if args.dry_run:
        load_dotenv()
        settings = load_settings()
        configure_encryption(settings)
        quota = run_startup_checks(settings, config, dry_run=True)
        print(
            f"OK: {len(config.symbols)} symbols, "
            f"need {quota['required']} slots, remain {quota['remain']}"
        )
        return

    _run_collector(config)


if __name__ == "__main__":
    main()
