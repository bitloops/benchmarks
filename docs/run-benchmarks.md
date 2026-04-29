# Run Benchmarks

This repo runs SWE-bench Multilingual tasks with either Codex or OpenCode.

There are only two benchmark configs:

- `configs/swebench/codex.toml`
- `configs/swebench/opencode.toml`

Each config has two modes:

- `baseline`: run the agent normally
- `with_bitloops`: run the same agent with Bitloops enabled

## 1. Install

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e '.[hf]'
python -m pip install swebench
```

Make sure the tools you need are available:

```bash
command -v codex      # for Codex
command -v opencode   # for OpenCode
command -v bitloops   # for with_bitloops
docker info
```

Authenticate the agent CLI you plan to use before running a real benchmark.

## 2. Pick Agent And Mode

Codex baseline:

```bash
CONFIG=configs/swebench/codex.toml
MODE=baseline
```

Codex with Bitloops:

```bash
CONFIG=configs/swebench/codex.toml
MODE=with_bitloops
```

OpenCode baseline:

```bash
CONFIG=configs/swebench/opencode.toml
MODE=baseline
```

OpenCode with Bitloops:

```bash
CONFIG=configs/swebench/opencode.toml
MODE=with_bitloops
```

## 3. Choose Tasks

Open your chosen config and edit these fields:

```toml
[run]
dataset_path = "datasets/swebench_multilingual.test.rust_all.jsonl"
include_repos = []
include_instance_ids = []
max_instances = 1
attempts = 1
max_workers = 1
```

For a first run, leave `max_instances = 1`.

To target one task:

```toml
include_instance_ids = ["astral-sh__ruff-15309"]
max_instances = 1
```

To target one repo:

```toml
include_repos = ["tokio-rs/tokio"]
max_instances = 3
```

## 4. Create Dataset If Missing

The default configs use:

```text
datasets/swebench_multilingual.test.rust_all.jsonl
```

If that file is missing, create it:

```bash
./.venv/bin/python -m benchkit.swebench.cli export-hf \
  --split test \
  --language rust \
  --output datasets/swebench_multilingual.test.rust_all.jsonl \
  --overwrite
```

Set `HF_TOKEN` first if Hugging Face requires authentication.

## 5. Preview The Run

Always run `plan` first:

```bash
./.venv/bin/python -m benchkit.swebench.cli plan \
  --config "$CONFIG" \
  --mode "$MODE"
```

Check:

- `Agent`
- `Condition`
- `Selected instances`
- `Sample instance IDs`

## 6. Run

```bash
./.venv/bin/python -m benchkit.swebench.cli run \
  --config "$CONFIG" \
  --mode "$MODE" \
  --appendix-output-dir "reports/appendix/${MODE}"
```

To override parallelism without editing the config:

```bash
./.venv/bin/python -m benchkit.swebench.cli run \
  --config "$CONFIG" \
  --mode "$MODE" \
  --max-workers 2
```

## 7. Find Results

The command prints a `Run root`. Results are under:

```text
runs/swebench_multilingual/<date>/<run_id>/
```

Most useful files:

- `summary.json`
- `run_manifest.json`
- `attempts/attempt-01/predictions.jsonl`
- `attempts/attempt-01/trace.jsonl`
- `attempts/attempt-01/evaluation.json`

Appendix CSV/Markdown files go to the directory passed with
`--appendix-output-dir`.

## Notes

- OpenCode sampling lives in `configs/opencode/opencode.json`.
- OpenCode credentials usually live in OpenCode's own auth storage.
- For Bitloops init debugging, see `docs/bitloops-init-status-guide.md`.
