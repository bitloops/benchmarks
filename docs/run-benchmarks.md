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

## 3. Create Dataset If Missing

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

## 4. Choose Tasks

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

## SWE-bench config reference

This section documents the fields used in `configs/swebench/codex.toml` and `configs/swebench/opencode.toml`. Values merge with the **`preset`** (`codex` or `opencode`) from `benchkit.common.config` — see `load_run_config` and `_config_presets()` in [`src/benchkit/common/config.py`](../src/benchkit/common/config.py).

### `preset`

Selects built-in defaults: agent command and `extra_args`, default `model` provider (and other model fields unless overridden), typical `[run]` timeouts and flags, `[evaluation]` defaults, and a **`[modes]`** table. Your TOML only needs to set what you want to change.

### CLI `--mode`

When you pass `--mode baseline` or `--mode with_bitloops` to `benchkit.swebench.cli`, benchkit merges the matching entry under `[modes.<name>]` from the preset (over `run`, `agent`, `model`, `evaluation`). That switches condition (e.g. Bitloops), timeouts, and agent `extra_args` without duplicating the full config.

### `[run]`

| Field | Meaning |
| --- | --- |
| `dataset_path` | Path to the task JSONL file (relative to the repo root unless absolute). |
| `include_repos` | If non-empty, only instances whose repository is in this list are selected. |
| `include_instance_ids` | If non-empty, only these SWE-bench instance IDs are selected. |
| `instance_ids_file` | Optional path to a text file (one instance ID per line, `#` comments allowed). Relative paths are resolved next to the config file. IDs are merged with `include_instance_ids`. |
| `max_instances` | Stop after this many instances after filters (useful for smoke runs). |
| `attempts` | How many independent solve attempts to run per instance (each attempt is a full agent run). |
| `max_workers` | How many benchmark tasks (instances) run concurrently. |
| `timeout_seconds` | Per-instance agent/solve timeout in seconds. |
| `workspace_timeout_seconds` | Timeout for workspace preparation (clone, checkout, etc.) in seconds. |

Other `[run]` keys exist in presets (for example `prepare_workspace`, `workspace_isolation_mode`, `bitloops_sandbox_mode`, `repo_url_template`, `prompt_context`). Edit them only when you need behavior beyond the stock `codex` / `opencode` overlays.

### `[model]`

| Field | Meaning |
| --- | --- |
| `name` | Canonical model label for the run (and for mapping). Codex/OpenCode runtime settings are sourced from their JSON files in `configs/codex/` and `configs/opencode/`. |

### `[evaluation]`

Post-run SWE-bench evaluation (when enabled in the preset). These settings control the **evaluation** subprocess, not the same thing as `run.max_workers` for the agent phase.

| Field | Meaning |
| --- | --- |
| `max_workers` | Parallel workers for evaluation. |
| `timeout_seconds` | Wall-clock cap for evaluation work (seconds). |

### `[model_map]` (OpenCode)

OpenCode configs use a nested table, for example `[model_map.opencode]`. Keys are **canonical** names (matching `model.name`); values are the **model id** passed through to the agent CLI after resolution. The subtable name should match the agent id (`opencode`). Resolution logic lives in [`src/benchkit/swebench/model_mapper.py`](../src/benchkit/swebench/model_mapper.py).

Runtime options for the OpenCode process are configured separately in **`configs/opencode/opencode.json`** (see [Notes](#notes)).

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

A normal run always writes harness artifacts under `runs/…` (see [Find results](#7-find-results)). The **appendix** outputs (aggregated CSV/Markdown tables, tool breakdowns, and related files) are **only** generated if you pass **`--appendix-output-dir`**. If you omit it, those appendix files are not written.

To produce both the run **and** the appendix in one step, use:

```bash
./.venv/bin/python -m benchkit.swebench.cli run \
  --config "$CONFIG" \
  --mode "$MODE" \
  --appendix-output-dir "reports/appendix/${MODE}"
```

If you already completed a run without `--appendix-output-dir`, generate the appendix from the printed `Run root` path:

```bash
./.venv/bin/python -m benchkit.swebench.cli appendix \
  --run-root "<path-to-run-root>" \
  --output-dir "reports/appendix/<your-label>"
```

To override parallelism without editing the config (add `--appendix-output-dir …` if you still want appendix files):

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

Appendix CSV/Markdown files exist only when you passed `--appendix-output-dir`
during `run`, or when you ran the `appendix` command afterward (see [Run](#6-run)).

## Notes

- Full TOML field reference: [SWE-bench config reference](#swe-bench-config-reference).
- Codex runtime config lives in `configs/codex/codex.json`.
- OpenCode runtime config lives in `configs/opencode/opencode.json`.
- `configs/swebench/*.toml` controls benchmark scope and selection; agent JSON files control runtime behavior.
- OpenCode credentials usually live in OpenCode's own auth storage.
- For Bitloops init debugging, see `docs/bitloops-init-status-guide.md`.
