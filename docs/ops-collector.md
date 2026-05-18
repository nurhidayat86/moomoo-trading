# Collector operations runbook

## Prerequisites

1. **OpenD** running on the same host (default `127.0.0.1:11111`).
2. Copy `src/config.yaml.example` to `src/config.yaml` and set your symbol list.
3. Ensure subscription quota: **2 slots per symbol** (TICKER + ORDER_BOOK). Check with:

```bash
./run python src/get_user_info.py
```

## Run locally

```bash
./run python src/collector/main.py --config src/config.yaml
```

Dry-run (config + quota only):

```bash
./run python src/collector/main.py --config src/config.yaml --dry-run
```

## Outputs

| Path | Purpose |
|------|---------|
| `data/parquet/ticker/` | Tick Parquet archive |
| `data/parquet/orderbook/` | Order book Parquet archive |
| `data/collector/state.db` | SQLite operational store |
| `data/collector/health.json` | Live health snapshot |
| `data/spill/` | Queue overflow JSONL |

## Systemd

See [systemd/moomoo-collector.service.example](systemd/moomoo-collector.service.example). Adjust `WorkingDirectory`, `ExecStart`, and user.

## Monitoring

- **Disk**: ORDER_BOOK grows faster than TICKER; watch `data/parquet/orderbook/`.
- **Health**: `data/collector/health.json` — `connected`, `queue_depth`, `spill_count`.
- **Quota**: Re-run `get_user_info.py` if you add symbols elsewhere.

## Graceful stop

Send `SIGTERM` or `SIGINT`; the writer drains the queue (default 30s) before exit.
