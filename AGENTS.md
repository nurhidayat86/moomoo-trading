# Agent instructions

## Python environment

Always use the **`moomoo-trading`** conda environment for this repository.

- Interpreter: `/home/arif/miniconda3/envs/moomoo-trading/bin/python`
- Before running Python, pip, pytest, or other Python tooling in the shell:

```bash
source /home/arif/miniconda3/etc/profile.d/conda.sh && conda activate moomoo-trading
```

- Or run a one-off command:

```bash
conda run -n moomoo-trading --no-capture-output python your_script.py
```

- Or use the project helper:

```bash
./run python your_script.py
```

Do not use the system Python or other conda envs unless the user explicitly asks.

## Cursor Cloud specific instructions

Miniconda is pre-installed at `/home/arif/miniconda3` with the `moomoo-trading` env (Python 3.12) already created, so `./run ...` works as documented in `README.md`. The startup update script refreshes pip deps; you normally don't need to recreate the env.

- Run tests/scripts via the `./run` helper or `conda run -n moomoo-trading ...` (see `README.md`). Lint: the repo ships no linter config, so there is no separate lint step.
- Local-only config files (`.env`, `src/config.yaml`) are gitignored. `.env` here sets `OPEND_ENABLE_ENCRYPT=false` so no RSA key is needed for local dev.
- The product needs **moomoo OpenD** running and **logged into a real moomoo account** to serve the API on `127.0.0.1:11111`. OpenD (GUI AppImage) is installed under `~/Desktop/moomoo_OpenD_*`; it was extracted with `--appimage-extract` (FUSE userspace tooling is absent) — launch it via `~/Desktop/moomoo_OpenD_*/moomoo_OpenD-GUI_*/squashfs-root/AppRun` with `DISPLAY=:1`.
- Without an OpenD login, the API port stays closed. Scripts that open an `OpenQuoteContext` (`get_user_info.py`, `check_open_position.py`, `collector/main.py`, including `--dry-run`) will **retry connecting indefinitely** (logging `ECONNREFUSED`) — they do not exit on their own, so don't expect them to terminate until OpenD is logged in. `check_connection.py` is the exception: it does a single TCP probe and exits cleanly.
- To exercise the collector's write pipeline (TICKER/ORDER_BOOK → SQLite + Parquet) without a moomoo account, drive `collector.writer.WriterThread` with synthetic `TickRecord`/`OrderBookRecord` objects; no live OpenD connection is required for the sink/writer path.
- The moomoo OpenD "Skills" (`install-moomoo-opend`, `moomooapi`) are installed in the global skills dir `~/.claude/skills/`.
