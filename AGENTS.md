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
