# Run Benchmarks

This repo supports two SWE-bench paths:

- `swebench_multilingual` (legacy Rust-focused flow)
- `swebench_pro` (new Pro flow, JS/TS-first)

Top-level configs:

- Multilingual legacy:
  - `configs/swebench/codex.toml`
  - `configs/swebench/ollama.toml`
- SWE-bench Pro:
  - `configs/swebench/codex_pro.toml`
  - `configs/swebench/ollama_pro.toml`
- Optional OpenCode templates:
  - `configs/swebench/opencode.toml.disabled`
  - `configs/swebench/opencode_pro.toml.disabled`

Each config supports the same two modes:

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
command -v ollama     # for Ollama
command -v bitloops   # for with_bitloops
docker info
```

Authenticate the agent CLI you plan to use before running a real benchmark.
For SWE-bench Pro evaluation, clone the evaluator repo:

```bash
mkdir -p third_party
git clone https://github.com/scaleapi/SWE-bench_Pro-os third_party/SWE-bench_Pro-os
```

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

Legacy multilingual with Ollama baseline:

```bash
CONFIG=configs/swebench/ollama.toml
MODE=baseline
```

SWE-bench Pro with Codex baseline:

```bash
CONFIG=configs/swebench/codex_pro.toml
MODE=baseline
```

SWE-bench Pro with Ollama baseline:

```bash
CONFIG=configs/swebench/ollama_pro.toml
MODE=baseline
```

## 3. Create Dataset If Missing

Multilingual legacy default:

```text
datasets/swebench_multilingual.test.rust_all.jsonl
```

```bash
./.venv/bin/python -m benchkit.swebench.cli export-hf \
  --benchmark swebench_multilingual \
  --split test \
  --language rust \
  --output datasets/swebench_multilingual.test.rust_all.jsonl \
  --overwrite
```

SWE-bench Pro default:

```text
datasets/swebench_pro.test.js_ts.jsonl
```

```bash
./.venv/bin/python -m benchkit.swebench.cli export-hf \
  --benchmark swebench_pro \
  --split test \
  --language javascript \
  --output datasets/swebench_pro.test.js_ts.jsonl \
  --overwrite
```

To export TypeScript instead, switch `--language typescript`.
Set `HF_TOKEN` first if Hugging Face requires authentication.

## 4. Choose Tasks

Open your chosen config and edit these fields:

```toml
[run]
dataset_path = "datasets/<your-dataset>.jsonl"
include_repos = []
include_instance_ids = []
max_instances = 1
attempts = 1
max_workers = 1
```

For a first run, leave `max_instances = 1`.

To target one task:

```toml
include_instance_ids = ["instance_NodeBB__NodeBB-..."]
max_instances = 1
```

To target one repo:

```toml
include_repos = ["NodeBB/NodeBB"]
max_instances = 3
```

## SWE-bench config reference

This section documents the fields used in `configs/swebench/*.toml`. Values merge with the selected **`preset`** from `benchkit.common.config` — see `load_run_config` and `_config_presets()` in [`src/benchkit/common/config.py`](../src/benchkit/common/config.py).

### `preset`

Preset families:

- Multilingual legacy: `codex`, `ollama`, `opencode`
- SWE-bench Pro: `codex_pro`, `ollama_pro`, `opencode_pro`

### CLI `--mode`

When you pass `--mode baseline` or `--mode with_bitloops` to `benchkit.swebench.cli`, benchkit merges the matching entry under `[modes.<name>]` from the preset (over `run`, `agent`, `model`, `evaluation`). That switches condition (e.g. Bitloops), timeouts, and agent `extra_args` without duplicating the full config.

### `[run]`

| Field | Meaning |
| --- | --- |
| `benchmark` | Benchmark profile (`swebench_multilingual` or `swebench_pro`). |
| `dataset_path` | Path to the task JSONL file (relative to the repo root unless absolute). |
| `language` | Language filter for selected rows (e.g. `rust`, `javascript`, `typescript`, `python`, `go`). |
| `include_repos` | If non-empty, only instances whose repository is in this list are selected. |
| `include_instance_ids` | If non-empty, only these SWE-bench instance IDs are selected. |
| `instance_ids_file` | Optional path to a text file (one instance ID per line, `#` comments allowed). Relative paths are resolved next to the config file. IDs are merged with `include_instance_ids`. |
| `max_instances` | Stop after this many instances after filters (useful for smoke runs). |
| `attempts` | How many independent solve attempts to run per instance (each attempt is a full agent run). |
| `max_workers` | How many benchmark tasks (instances) run concurrently. |
| `timeout_seconds` | Per-instance agent/solve timeout in seconds. |
| `workspace_timeout_seconds` | Timeout for workspace preparation (clone, checkout, etc.) in seconds. |

Other `[run]` keys exist in presets (for example `prepare_workspace`, `workspace_isolation_mode`, `bitloops_sandbox_mode`, `repo_url_template`, `prompt_context`). Edit them only when you need behavior beyond the stock overlays.

When `prepare_workspace = true`, benchkit prepares strict benchmark workspaces by cloning only the requested `base_commit` history and removing all configured Git remotes from the prepared repo.

### `[evaluation]`

Post-run evaluation settings.

| Field | Meaning |
| --- | --- |
| `swebench_repo` | Path to evaluator repo. For multilingual, this is the SWE-bench harness checkout. For Pro, this is `scaleapi/SWE-bench_Pro-os`. |
| `max_workers` | Parallel workers for evaluation. |
| `timeout_seconds` | Wall-clock cap for evaluation work (seconds). |
| `pro_use_local_docker` | Pro only: run Pro evaluator with `--use_local_docker`. |
| `pro_dockerhub_username` | Pro only: Docker Hub namespace for Pro images (default `jefzda`). |

### `[model_map]`

Same mapping behavior as before: keys are canonical names (`model.name`), values are runtime model IDs per agent.

## 5. Preview The Run

Always run `plan` first:

```bash
./.venv/bin/python -m benchkit.swebench.cli plan \
  --config "$CONFIG" \
  --mode "$MODE"
```

Check:

- `Benchmark`
- `Agent`
- `Condition`
- `Selected instances`
- `Sample instance IDs`

## 6. Run

A normal run always writes harness artifacts under `runs/…` (see [Find results](#7-find-results)). The **appendix** outputs (aggregated CSV/Markdown tables, tool breakdowns, and related files) are generated only if you pass **`--appendix`** or **`--appendix-output-dir`**.

To produce both the run **and** the appendix in one step with a stable, timestamped folder name under `reports/appendix/`, use **`--appendix`**. The directory name is `<agent_id>_bitloops_<YYYYMMDD_HHMMSS>` or `<agent_id>_baseline_<YYYYMMDD_HHMMSS>` (date and time come from the harness `run_id`):

```bash
./.venv/bin/python -m benchkit.swebench.cli run \
  --config "$CONFIG" \
  --mode "$MODE" \
  --appendix
```

To choose the report directory yourself, pass **`--appendix-output-dir`** instead (it overrides **`--appendix`** if both are set):

```bash
./.venv/bin/python -m benchkit.swebench.cli run \
  --config "$CONFIG" \
  --mode "$MODE" \
  --appendix-output-dir "reports/appendix/${MODE}"
```

If you already completed a run without appendix flags, generate the appendix from the printed `Run root` path:

```bash
./.venv/bin/python -m benchkit.swebench.cli appendix \
  --run-root "<path-to-run-root>" \
  --output-dir "reports/appendix/<your-label>"
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
runs/<benchmark>/<date>/<run_id>/
```

Most useful files:

- `summary.json`
- `run_manifest.json`
- `attempts/attempt-01/predictions.jsonl`
- `attempts/attempt-01/trace.jsonl`
- `attempts/attempt-01/evaluation.json`

Appendix CSV/Markdown files exist only when you passed `--appendix` or `--appendix-output-dir`
during `run`, or when you ran the `appendix` command afterward.

## Notes

- Multilingual Rust flow is still supported as legacy (`swebench_multilingual`).
- SWE-bench Pro flow uses `scaleapi/SWE-bench_Pro-os` for evaluation.
- Codex runtime config lives in `configs/codex/codex.json`.
- Ollama runtime config lives in `configs/ollama/ollama.json`.
- OpenCode runtime config lives in `configs/opencode/opencode.json`.
- `configs/swebench/*.toml` controls benchmark scope and selection; agent JSON files control runtime behavior.
- For Bitloops init debugging, see `docs/bitloops-init-status-guide.md`.
