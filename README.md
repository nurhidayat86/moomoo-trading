# moomoo-trading

Python tooling for [moomoo OpenAPI](https://openapi.moomoo.com/moomoo-api-doc/) (OpenD): connection checks, account/quota inspection, position queries, and a **24/7 market data collector** that archives live **tick-by-tick trades** and **order book** snapshots.

The collector uses push subscriptions only (no polling). Data is written to **Parquet** for long-term storage and **SQLite** for operational state, deduplication, and health metrics.

## Prerequisites

- **moomoo OpenD** running on the same machine (default `127.0.0.1:11111`)
- A moomoo account with quote permissions for the markets you subscribe to
- **Python 3.12+**
- [Conda](https://docs.conda.io/) (recommended) or a virtualenv

Subscription quota matters for the collector: each symbol uses **two** real-time slots (one for `TICKER`, one for `ORDER_BOOK`). Check your remaining quota before adding symbols.

## Installation

### 1. Create the conda environment

```bash
conda env create -f environment.yml
conda activate moomoo-trading
```

Or create the env manually and install dependencies:

```bash
conda create -n moomoo-trading python=3.12 -y
conda activate moomoo-trading
pip install -r requirements.txt
```

### 2. Configure OpenD connection

Copy the example env file and edit it for your setup:

```bash
cp .env_example .env
```

| Variable | Description |
|----------|-------------|
| `OPEND_HOST` | OpenD host (default `127.0.0.1`) |
| `OPEND_PORT` | OpenD port (default `11111`) |
| `RSA_PRIVATE_KEY_PATH` | PKCS#1 RSA private key matching OpenD encryption settings |
| `OPEND_ENABLE_ENCRYPT` | `true` / `false` — must match OpenD |
| `FUTU_TRD_ENV` | `SIMULATE` or `REAL` for trading scripts |

### 3. Configure the data collector (optional)

```bash
cp src/config.yaml.example src/config.yaml
```

Edit `symbols` and collector settings in `src/config.yaml`. This file is gitignored so local symbol lists stay private.

## Running commands

Use the `./run` helper to execute commands inside the `moomoo-trading` conda env:

```bash
./run python src/<script>.py [args...]
```

Equivalent:

```bash
conda activate moomoo-trading
python src/<script>.py [args...]
```

## What the code does

### Shared layer

| Module | Role |
|--------|------|
| `src/opend_env.py` | Loads `.env`, OpenD host/port, RSA encryption, SDK version check |
| `src/quote_context.py` | Factory for `OpenQuoteContext` used across scripts |

### Utility scripts

| Script | Purpose |
|--------|---------|
| `src/check_connection.py` | TCP + API probe (`get_global_state`) — verify OpenD is reachable |
| `src/get_user_info.py` | Quote permissions, subscription quota, active subscriptions |
| `src/check_open_position.py` | List open US stock and option positions (simulate or real) |

Examples:

```bash
./run python src/check_connection.py
./run python src/get_user_info.py
./run python src/get_user_info.py --json
./run python src/check_open_position.py
```

### Market data collector

The collector (`src/collector/`) subscribes to **TICKER** and **ORDER_BOOK** for every symbol in `src/config.yaml`, then:

1. **Push handlers** enqueue records in memory (no disk I/O on the callback thread).
2. A **writer thread** batches rows into SQLite and rotating Parquet files.
3. On queue pressure, records **spill** to JSONL under `data/spill/` and are replayed when the queue drains.
4. On disconnect, the process **reconnects** with exponential backoff and resubscribes.
5. **Health** and **checkpoints** are updated periodically for operations.

```
OpenD  →  TickerHandler / OrderBookHandler  →  Queue  →  Writer  →  SQLite + Parquet
                                              ↘ spill (if full)
```

#### Run the collector

Validate config and subscription quota without subscribing:

```bash
./run python src/collector/main.py --config src/config.yaml --dry-run
```

Start collecting (OpenD must be running):

```bash
./run python src/collector/main.py --config src/config.yaml
```

Stop gracefully with `Ctrl+C` or `SIGTERM`; the writer drains the queue (default 30s) before exit.

#### Output layout

| Path | Contents |
|------|----------|
| `data/parquet/ticker/` | Tick archive (`symbol=…/date=…/part-*.parquet`) |
| `data/parquet/orderbook/` | Order book snapshots (rotates every 5 minutes by default) |
| `data/collector/state.db` | Recent ticks/books, checkpoints, connection log, hourly ingest counts |
| `data/collector/health.json` | Live status (`connected`, `queue_depth`, `spill_count`, …) |
| `data/spill/` | Overflow JSONL when the ingest queue is full |

SQLite retains ticks for **48 hours** and order book rows for **24 hours** by default; Parquet under `data/parquet/` is the long-term archive.

#### Config highlights

See `src/config.yaml.example` for all options. Important fields:

- `symbols` — list of codes, e.g. `US.AAPL`, `HK.00700`
- `collector.session` — `NONE`, `RTH`, `ETH`, or `ALL` (not `OVERNIGHT`)
- `collector.extended_time` — include extended hours where supported
- `collector.queue_maxsize` — backpressure threshold before spill

Full design and schemas: [docs/PRD-data-collector.md](docs/PRD-data-collector.md).  
Day-2 operations: [docs/ops-collector.md](docs/ops-collector.md).  
systemd unit example: [docs/systemd/moomoo-collector.service.example](docs/systemd/moomoo-collector.service.example).

## Tests

```bash
./run python -m pytest tests/ -q
```

Covers config validation, partition dates, SQLite deduplication, and spill read/replay.

## Project layout

```
moomoo-trading/
├── run                    # conda wrapper
├── environment.yml
├── requirements.txt
├── .env_example           # copy to .env
├── src/
│   ├── opend_env.py
│   ├── quote_context.py
│   ├── config_loader.py
│   ├── config.yaml.example
│   ├── check_connection.py
│   ├── get_user_info.py
│   ├── check_open_position.py
│   └── collector/         # 24/7 TICKER + ORDER_BOOK collector
├── tests/
└── docs/
    ├── PRD-data-collector.md
    ├── ops-collector.md
    └── systemd/
```

Generated data under `data/` is gitignored.

## Documentation

- [Product requirements (collector)](docs/PRD-data-collector.md)
- [Collector runbook](docs/ops-collector.md)
- [Moomoo OpenAPI docs](https://openapi.moomoo.com/moomoo-api-doc/)

## License

No license file is included yet; treat as private project code unless stated otherwise.
