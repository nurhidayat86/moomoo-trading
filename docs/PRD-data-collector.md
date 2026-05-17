# PRD: Moomoo Market Data Collector (TICKER + ORDER_BOOK)

| Field | Value |
|-------|--------|
| Status | Draft — implementation guide for coding agents |
| Version | 0.1 |
| Python | `>=3.12` |
| Primary SDK | `moomoo-api>=10.4.6408` |
| Runtime dependency | OpenD >= 10.4.6408 (always on for 24/7) |

---

## 1. Purpose

Build a **long-running Python service** that subscribes to moomoo OpenAPI push streams for every symbol listed in `src/config.yaml`, persists **tick-by-tick trades (TICKER)** and **order book snapshots (ORDER_BOOK)** with **minimum practical data loss**, and runs continuously (**24 hours × 7 days**) on a single host connected to local OpenD.

This document is the **single source of truth** for scope, architecture, storage layout, reliability targets, and acceptance criteria. Implementation must reuse existing project utilities in `src/opend_env.py` and follow `AGENTS.md` (conda env `moomoo-trading`, `./run` wrapper).

---

## 2. Goals

| ID | Goal |
|----|------|
| G1 | Ingest **TICKER** and **ORDER_BOOK** for all configured symbols via **push subscriptions** (no polling loops). |
| G2 | Persist data in **Apache Parquet** (analytics archive) and **SQLite** (operational / recovery layer). |
| G3 | Survive **process restarts, OpenD reconnects, and brief I/O stalls** without blocking SDK callbacks. |
| G4 | Operate **24/7** with automatic reconnect, structured logging, and health signals. |
| G5 | Keep **subscription quota** usage predictable: `2 × number_of_symbols` (one slot per symbol per stream type). |

---

## 3. Non-goals (v1)

- Historical backfill of full TICKER tape (moomoo does not provide archival tick history; live capture only).
- Sub-second OHLCV aggregation (downstream job; not in collector v1).
- Multi-host replication or cloud deployment.
- Crypto (`CC.*`) unless explicitly added to config later.
- Order placement, portfolio queries, or trading.
- Calling `unlock_trade` or any trading unlock via SDK.

---

## 4. Definitions

| Term | Meaning |
|------|---------|
| **TICKER** | Tick-by-tick **executed trade** stream (`SubType.TICKER`). |
| **ORDER_BOOK** | Levelled bid/ask depth updates (`SubType.ORDER_BOOK`). |
| **Symbol** | Moomoo code with market prefix, e.g. `US.AAPL`. |
| **Lossless (practical)** | No drops attributable to **our** process under normal load; upstream feed gaps may still occur per quote tier. |
| **At-risk window** | Max duration of data only in memory if the process crashes before flush. |

---

## 5. User configuration

### 5.1 Config file location

**Path:** `src/config.yaml` (committed template allowed; secrets stay in `.env`).

### 5.2 Schema (normative)

```yaml
# src/config.yaml — example; validate on startup

symbols:
  - US.AAPL
  - US.TSLA

collector:
  # US extended hours (pre + RTH + post). OVERNIGHT not supported for subscribe.
  extended_time: true
  session: ALL          # NONE | RTH | ETH | ALL

  # Push handler → writer decoupling
  queue_maxsize: 100000
  writer_batch_size: 1000
  writer_flush_interval_sec: 0.25

  # Parquet file rotation (limits at-risk window on crash)
  ticker_rotate_minutes: 15
  orderbook_rotate_minutes: 5

  # SQLite checkpoint frequency (recovery metadata)
  sqlite_checkpoint_interval_sec: 30

  # Reconnect backoff
  reconnect_initial_sec: 1
  reconnect_max_sec: 60

  # First subscribe after connect: replay OpenD cache to reduce gaps
  is_first_push: true

paths:
  # Relative to repository root unless absolute
  data_root: data

logging:
  level: INFO
  json: false
```

### 5.3 Validation rules

- Every symbol must match `^(US|HK|SH|SZ|SG|CC)\.[A-Za-z0-9.]+$` (extend when adding markets).
- `symbols` must be non-empty and **deduplicated**.
- On startup, call `get_user_info` logic (or equivalent) and **fail fast** if `subscription_quota` &lt; `2 × len(symbols)`.
- Reject config if `session: OVERNIGHT` (not supported for subscribe).

---

## 6. System context

```mermaid
flowchart LR
  subgraph host [Collector Host 24/7]
    CFG[src/config.yaml]
    COL[Collector Service]
    SQLite[(SQLite state.db)]
    PQ[(Parquet datasets)]
    CFG --> COL
    COL --> SQLite
    COL --> PQ
  end
  OpenD[OpenD 127.0.0.1:11111]
  Moomoo[Moomoo quote servers]
  OpenD <-- TCP --> COL
  OpenD <-- Moomoo
```

**External dependencies**

- OpenD running before collector (`check_connection.py` pattern).
- `.env` for `OPEND_HOST`, `OPEND_PORT`, RSA encryption (`src/opend_env.py`).
- Stable network; host power/sleep disabled during market hours (document in ops runbook).

---

## 7. Architecture

### 7.1 Process model

- **One OS process**, one `OpenQuoteContext`, one subscription covering all symbols and both subtypes.
- **Main thread:** connection lifecycle, subscribe, sleep, reconnect loop.
- **Writer thread(s):** drain queues, batch writes to SQLite + Parquet.
- **Optional:** one writer thread per stream type (TICKER / ORDER_BOOK) if profiling shows contention.

### 7.2 Data path (loss minimization)

```text
TickerHandler / OrderBookHandler.on_recv_rsp
  → parse row(s) only
  → put immutable record(s) on bounded Queue  (NEVER block >1ms; never I/O here)
  → writer batches
  → SQLite INSERT (dedup) + append Parquet row group
  → fsync policy: SQLite commit per batch; Parquet close file on rotation
```

If queue is full:

1. Spill records to `data/spill/{stream}/{YYYYMMDD}/spill-{timestamp}.jsonl` (append-only).
2. Increment metric `spill_count`.
3. Background task replays spill files when queue drains.

### 7.3 Subscription (moomoo API)

Single call (pseudocode):

```python
ctx.subscribe(
    code_list=config.symbols,
    subtype_list=[SubType.TICKER, SubType.ORDER_BOOK],
    subscribe_push=True,
    is_first_push=config.is_first_push,
    extended_time=config.extended_time,
    session=session_enum,
)
```

**Quota:** `len(symbols) × 2` subscription slots. Document usage in startup log.

**Do not** poll `get_rt_ticker` or `get_order_book` in a loop for ingestion.

### 7.4 Reconnect strategy

On disconnect or subscribe failure:

1. Log `connection_log` row in SQLite.
2. Close context safely.
3. Exponential backoff (`reconnect_initial_sec` → cap `reconnect_max_sec`).
4. Reconnect; subscribe again with `is_first_push=True` to request cached pushes where OpenD supports it.
5. Deduplicate replays via primary keys (see §8.2).

**Gap honesty:** Outages longer than OpenD cache may leave **unrecoverable tick gaps**; log `gap_suspected` events when `recv_ts` − `exchange_time` &gt; threshold or sequence breaks.

---

## 8. Storage design

### 8.1 Design rationale

| Store | Role | Time granularity |
|-------|------|------------------|
| **Parquet** | Durable **analytics archive**; columnar; partition pruning | **Symbol + calendar date (US Eastern for US.\*)**; **rotate files every 15 min (TICKER) / 5 min (ORDER_BOOK)** |
| **SQLite** | **Operational** dedup, checkpoints, connection health, spill index | **Hourly** aggregates + per-batch checkpoints |

**Why 15 min / 5 min rotation?**

- Shorter rotation → smaller **at-risk window** if the process dies mid-file.
- Longer rotation → fewer files.  
- TICKER is append-heavy but row-small → **15 minutes** balances loss vs file count.
- ORDER_BOOK snapshots are fat (full book per push) → **5 minutes** limits huge single-file corruption risk.

**Why SQLite + Parquet?**

- Parquet: efficient storage and DuckDB/Pandas replay.
- SQLite: fast idempotent inserts (`INSERT OR IGNORE`), recovery state, and ops queries without scanning Parquet.

### 8.2 Directory layout

Repository root (default `data_root: data`):

```text
data/
  collector/
    state.db                 # SQLite WAL
  spill/
    ticker/...
    orderbook/...
  parquet/
    ticker/
      symbol=US.AAPL/
        date=2026-05-17/
          part-20260517T1430-0001.parquet
          part-20260517T1445-0002.parquet
    orderbook/
      symbol=US.AAPL/
        date=2026-05-17/
          part-20260517T1430-0001.parquet
```

Partition keys:

- `symbol` — moomoo code with dot replaced for Hive paths if needed (`US.AAPL` → `symbol=US.AAPL`).
- `date` — **US Eastern date** derived from exchange `time` for `US.*`; HK/CN use exchange doc rules (Asia/Shanghai for HK/CN). Implement `partition_date(code, exchange_time) -> date`.

File naming: `part-{YYYYMMDD}T{HHMM}-{seq:04d}.parquet` in UTC or exchange-local **consistent with partition date** (document choice in code; prefer **exchange-local** for `US.*`).

### 8.3 TICKER schemas

**Parquet (PyArrow)**

| Column | Type | Notes |
|--------|------|-------|
| `code` | string | |
| `time` | string | API `time` (`yyyy-MM-dd HH:mm:ss:xxx`) |
| `price` | float64 | |
| `volume` | int64 | |
| `turnover` | float64 | nullable |
| `direction` | string | `ticker_direction` |
| `recv_ts` | timestamp[us, UTC] | local receive time |

**SQLite `ticks`**

```sql
CREATE TABLE ticks (
    code TEXT NOT NULL,
    time TEXT NOT NULL,
    price REAL NOT NULL,
    volume INTEGER NOT NULL,
    turnover REAL,
    direction TEXT,
    recv_ts TEXT NOT NULL,
    parquet_part TEXT,
    PRIMARY KEY (code, time, price, volume, turnover, direction)
);
CREATE INDEX idx_ticks_recv ON ticks(recv_ts);
```

Retention: SQLite `ticks` keeps **rolling 48 hours**; older rows pruned by scheduled job (Parquet is canonical).

### 8.4 ORDER_BOOK schemas

Each push stores **one snapshot** (serialize book as JSON string or flattened top-N levels).

**Parquet**

| Column | Type | Notes |
|--------|------|-------|
| `code` | string | |
| `recv_ts` | timestamp[us, UTC] | |
| `bid_json` | string | JSON array `[[price, size], ...]` |
| `ask_json` | string | JSON array |
| `bid_levels` | int32 | count |
| `ask_levels` | int32 | count |

Alternative (v1.1): store top 10 levels as fixed columns `bid_px_1..10` for faster queries — optional optimization.

**SQLite `orderbook_snapshots`**

```sql
CREATE TABLE orderbook_snapshots (
    code TEXT NOT NULL,
    recv_ts TEXT NOT NULL,
    snapshot_hash TEXT NOT NULL,  -- sha256 of canonical JSON
    bid_json TEXT NOT NULL,
    ask_json TEXT NOT NULL,
    parquet_part TEXT,
    PRIMARY KEY (code, recv_ts, snapshot_hash)
);
```

Retention: SQLite **24 hours** rolling; Parquet canonical.

### 8.5 SQLite operational tables

```sql
CREATE TABLE stream_checkpoint (
    stream TEXT NOT NULL,          -- 'ticker' | 'orderbook'
    code TEXT NOT NULL,
    last_exchange_time TEXT,
    last_recv_ts TEXT NOT NULL,
    last_parquet_part TEXT,
  PRIMARY KEY (stream, code)
);

CREATE TABLE connection_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_ts TEXT NOT NULL,
    event TEXT NOT NULL,           -- connect | disconnect | subscribe_ok | subscribe_fail | spill
    detail TEXT
);

CREATE TABLE ingest_hourly (
    hour_utc TEXT NOT NULL,
    stream TEXT NOT NULL,
    code TEXT NOT NULL,
    rows_ingested INTEGER NOT NULL,
    rows_deduped INTEGER NOT NULL,
    spill_rows INTEGER NOT NULL,
    PRIMARY KEY (hour_utc, stream, code)
);
```

Checkpoint every `sqlite_checkpoint_interval_sec`: update `stream_checkpoint` after successful Parquet rotate + SQLite batch commit.

---

## 9. Reliability & “minimum loss” requirements

| ID | Requirement | Target |
|----|-------------|--------|
| R1 | Handler must not perform disk I/O | 100% |
| R2 | Bounded queue + spill | No silent drop when queue full |
| R3 | Batch + periodic flush | ≤ `writer_flush_interval_sec` lag under normal load |
| R4 | At-risk window (process crash) | ≤ `ticker_rotate_minutes` / `orderbook_rotate_minutes` of data |
| R5 | Dedup on reconnect replay | `INSERT OR IGNORE` / PK |
| R6 | Automatic reconnect | Backoff; max outage visibility via logs |
| R7 | Graceful shutdown | SIGINT/SIGTERM drains queue (timeout 30s) then closes files |
| R8 | OpenD dependency | Exit non-zero if OpenD unreachable at startup; retry in loop when already running |

**Not guaranteed:** exchange-wide completeness vs SIP; extended feed limits per moomoo quote tier.

---

## 10. Proposed code layout

```text
src/
  config.yaml              # symbol list + collector tuning
  config_loader.py         # load + validate YAML
  opend_env.py             # existing
  collector/
    __init__.py
    main.py                # CLI entry: python -m collector.main
    context.py             # OpenQuoteContext factory (reuse opend_env)
    handlers.py            # TickerHandler, OrderBookHandler
    queues.py              # record types, queue wiring
    writer.py              # batch writer thread
    parquet_sink.py        # rotation, schema, write
    sqlite_sink.py         # DDL, inserts, prune
    spill.py               # spill + replay
    reconnect.py           # backoff loop
    metrics.py             # counters for logs / hourly rollup
docs/
  PRD-data-collector.md    # this file
```

**CLI**

```bash
./run python -m collector.main
./run python -m collector.main --config src/config.yaml --dry-run  # validate only
```

---

## 11. Dependencies

Add to `requirements.txt` (implementation phase):

```text
moomoo-api>=10.4.6408
python-dotenv>=1.0.0
pyyaml>=6.0
pyarrow>=15.0
```

Python **`>=3.12`** enforced in `environment.yml` / packaging metadata.

---

## 12. Observability

| Signal | Mechanism |
|--------|-----------|
| Structured logs | `logging` with symbol, stream, event |
| Health file | `data/collector/health.json` updated every 60s: `last_tick_recv`, `queue_depth`, `connected` |
| Hourly rollups | `ingest_hourly` table |
| Subscription usage | Log `total_used` / `remain` at startup (from `get_user_info` pattern) |

**Alerts (manual v1):** operator monitors log for `spill`, `subscribe_fail`, `disconnect` bursts.

---

## 13. Security & git

- `.env` and `data/` must be in `.gitignore` (implementation: add `data/`, `*.db`, `*.parquet` under data).
- No credentials in `config.yaml`.
- RSA key path only via `.env` (`opend_env.py`).

---

## 14. Operations runbook (24/7)

1. Start OpenD; verify `./run python src/check_connection.py`.
2. Verify quota: `./run python src/get_user_info.py`.
3. Start collector under **systemd** `Restart=always` (user unit).
4. Disable host suspend on AC power.
5. Monitor `data/collector/health.json` and disk usage under `data/parquet/`.
6. Prune SQLite rolling windows daily (built-in job).

---

## 15. Acceptance criteria

| # | Criterion |
|---|-----------|
| AC1 | With `symbols: [US.AAPL]` in `src/config.yaml`, collector runs ≥1 hour without crash; Parquet files appear under `data/parquet/ticker/...` and `orderbook/...`. |
| AC2 | TICKER rows in SQLite dedupe on reconnect test (restart process; no duplicate PK explosion). |
| AC3 | Forced queue pressure writes spill files and recovers them. |
| AC4 | SIGTERM triggers graceful drain; last batch persisted. |
| AC5 | Startup fails clearly when `2 × len(symbols) > subscription_quota remain`. |
| AC6 | No `get_rt_ticker` / `get_order_book` polling in ingestion path (code review). |
| AC7 | Python 3.12+ type hints; `py_compile` / basic tests pass in `moomoo-trading` env. |

---

## 16. Implementation phases

| Phase | Deliverable |
|-------|-------------|
| **P0** | `config_loader`, connection + subscribe loop, TICKER → SQLite + Parquet |
| **P1** | ORDER_BOOK handler + schemas + rotation |
| **P2** | Spill/replay, reconnect backoff, `stream_checkpoint` |
| **P3** | Prune job, health.json, systemd unit example, tests |
| **P4** | Docs: `docs/ops-collector.md` (short runbook) — optional |

---

## 17. Open questions (defaults if unanswered)

| Question | Default for v1 |
|----------|----------------|
| US only symbols? | Yes; schema supports HK later |
| Store full book or top 10? | Full JSON in Parquet; prune levels in v1.1 if size issue |
| Exchange local vs UTC partitions? | US Eastern `date` partition for `US.*` |
| One writer or two? | Start with **one** writer thread |

---

## 18. References

- Project: `src/opend_env.py`, `src/get_user_info.py`, `src/check_connection.py`
- Moomoo skill: `~/.claude/skills/moomooapi/` (`push_ticker.py`, `push_orderbook.py`, `API_LIMITS.md`)
- Official: [request_history_kline](https://openapi.moomoo.com/moomoo-api-doc/en/quote/request-history-kline.html) (not used for TICKER archive)

---

*End of PRD v0.1*
