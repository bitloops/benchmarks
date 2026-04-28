# Bitloops Benchmarks

Scaffold for agent-focused benchmarking with SWE-bench Multilingual first, then additional benchmark suites.

## Current Scope

- SWE-bench Multilingual integration scaffold
- Rust-first task filtering
- Agent adapters (Claude Code, Cursor, Codex, OpenCode, noop)
- Run artifacts and metadata for reproducibility

## Environment Setup

Run these before `./scripts/swebench/phase1_tokio_run.sh`.

1. System prerequisites:
   - Python `>=3.11`
   - `git`
   - Docker Desktop / Docker daemon running
   - `claude` CLI installed and authenticated
   - `cursor-agent` CLI installed and authenticated
   - `codex` CLI installed and authenticated
   - `opencode` CLI installed and authenticated
   - `bitloops` CLI installed (required for `with_bitloops` runs)
2. Create and activate a virtual environment:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
```

3. Install Python dependencies:

```bash
python -m pip install -e '.[hf]'
python -m pip install swebench
```

4. Optional preflight checks:

```bash
python -c "import datasets, swebench; print('python deps ok')"
command -v claude
command -v cursor-agent
command -v codex
command -v opencode
docker info
```

5. Run Phase 1:

```bash
./scripts/swebench/phase1_tokio_run.sh
```

By default, Phase 1 now runs Claude, Cursor, and OpenCode baselines in parallel and runs up to
2 tasks concurrently per baseline (`RUN_MAX_WORKERS=2`).

Within a single benchmark run, `max_workers` applies to attempt-instance jobs. If `attempts > 1`
and `max_workers > 1`, the runner can execute multiple attempts for the same task instance in
parallel. When workspace preparation is enabled, those parallel attempts automatically use isolated
attempt-scoped workspaces.

If needed, you can force the script to use a specific interpreter:

```bash
PYTHON_BIN=python ./scripts/swebench/phase1_tokio_run.sh
```

Tuning parallelism:

```bash
# Disable parallel baseline runs
RUN_AGENTS_IN_PARALLEL=0 ./scripts/swebench/phase1_tokio_run.sh

# Keep agents parallel, but change per-run task concurrency
RUN_MAX_WORKERS=3 ./scripts/swebench/phase1_tokio_run.sh
```

## Quick Start

1. Pick a config:
   - `configs/swebench/rust_canary.toml` (mock agent)
   - `configs/swebench/rust_opencode.toml` (OpenCode + Bitloops)
   - `configs/swebench/rust_opencode_baseline.toml` (OpenCode baseline)
   - `configs/swebench/rust_tokio_phase1_claude.toml` (Tokio Phase 1)
   - `configs/swebench/rust_tokio_phase1_claude_with_bitloops.toml` (Tokio Phase 1 + Bitloops)
   - `configs/swebench/rust_tokio_phase1_codex.toml` (Tokio Phase 1)
   - `configs/swebench/rust_tokio_phase1_codex_with_bitloops.toml` (Tokio Phase 1 + Bitloops)
   - `configs/swebench/rust_all_repos_claude_with_bitloops.toml` (multi-repo Rust subset + Claude + Bitloops)

   Historical one-off configs are kept under `configs/swebench/archive/`.
2. Export SWE-bench Multilingual data into local JSONL:

```bash
python3 -m benchkit.swebench.cli export-hf \
  --split test \
  --repo tokio-rs/tokio \
  --max-instances 3 \
  --output datasets/swebench_multilingual.test.tokio.jsonl \
  --overwrite
```

Set `HF_TOKEN` if the dataset requires authenticated access.
The exporter normalizes language aliases (for example `tokio-rs` -> `rust`) and
adds `repo_label` (owner/org prefix from `repo`) to each row.
3. Inspect a planned run:

```bash
python3 -m benchkit.swebench.cli plan --config configs/swebench/rust_canary.toml
```

4. Run a dry-run baseline (no real agent call):

```bash
python3 -m benchkit.swebench.cli run --config configs/swebench/rust_canary.toml --dry-run
```

Run outputs are written under `runs/`.

Enable direct SWE-bench harness evaluation after prediction generation with:

```toml
[evaluation]
enabled = true
python_bin = "python3"
swebench_repo = "/absolute/path/to/SWE-bench"  # optional if swebench is installed
dataset_name = "SWE-bench/SWE-bench_Multilingual"
split = "dev"
max_workers = 4
timeout_seconds = 7200
```

Each attempt will write:
- `attempts/attempt-XX/evaluation.json`
- `attempts/attempt-XX/evaluation.stdout.log`
- `attempts/attempt-XX/evaluation.stderr.log`
- `attempts/attempt-XX/agent:<...>.<run_id>-attempt-XX.json` (raw SWE-bench report)

To run against real repo checkouts at each task `base_commit`, enable in `[run]`:

```toml
condition = "baseline"
include_repos = ["tokio-rs/tokio"]      # optional
include_instance_ids = []               # optional
# instance_ids_file = "tokio_task_ids.txt"
prepare_workspace = true
repo_url_template = "https://github.com/{repo}.git"
git_bin = "git"
workspace_timeout_seconds = 600
max_workers = 2
```

Generate appendix files from one or more completed runs:

```bash
python3 -m benchkit.swebench.cli appendix \
  --run-root runs/swebench_multilingual/<date>/<run_id_1> \
  --run-root runs/swebench_multilingual/<date>/<run_id_2> \
  --output-dir reports/appendix
```

Phase 1 (2-3 Tokio tasks) flow:
1. Copy `configs/swebench/tokio_task_ids.sample.txt` to `configs/swebench/tokio_task_ids.txt` and keep only your selected task IDs.
2. Run `plan` with one of the `rust_tokio_phase1_*.toml` configs.
3. Run `run` for Claude, Cursor, and OpenCode baselines.
4. Run `appendix` on all run roots to generate appendix files.

Use `model_map` in config if an agent expects a different CLI model ID than the
canonical benchmark model name.

For Codex baseline defaults:
- canonical model: `gpt-5.4`
- resolved model: `gpt-5.4` (via `[model_map.codex]`)

For OpenCode baseline defaults:
- canonical model: `gpt-5`
- resolved model: `openai/gpt-5` (via `[model_map.opencode]`)
- optional reproducibility knob: `[model].seed = 4242`

For OpenCode credentials:
- keep provider API keys in OpenCode auth storage, typically `~/.local/share/opencode/auth.json`
- keep benchmark runtime knobs in TOML (`[model].name`, `temperature`, optional `seed`)

`plan` and `run` now perform strict agent/model normalization checks and fail
fast on mismatches (example: Cursor with `claude-opus-4-6` will error and suggest
`opus-4.6`).

Run the full Tokio Phase 1 flow in one command:

```bash
./scripts/swebench/phase1_tokio_run.sh
```

Run a reusable A/B comparison (baseline vs `with_bitloops`) in one command:

```bash
./scripts/swebench/run_ab_compare.sh
```

Useful overrides:

```bash
# Point to any baseline/experiment config pair
BASELINE_CONFIG=configs/swebench/rust_tokio_phase1_claude.toml \
EXPERIMENT_CONFIG=configs/swebench/rust_tokio_phase1_claude_with_bitloops.toml \
APPENDIX_DIR=reports/appendix/tokio_claude_bitloops_ab \
./scripts/swebench/run_ab_compare.sh

# Override run concurrency for both runs
RUN_MAX_WORKERS=2 ./scripts/swebench/run_ab_compare.sh
```

Bitloops-enabled Claude configs now use the same wrapper setup pattern:

```toml
[run]
condition = "with_bitloops"
workspace_timeout_seconds = 1800
bitloops_sandbox_mode = "per_task_daemon"

[agent]
extra_args = ["--bitloops-init", "--bitloops-embeddings-runtime", "platform"]
```

Field reference:
- `run.max_instances`: Optional cap on how many instances are selected after repo/language/ID filters. Omit it to run all matched instances. Must be `>= 1` when set.
- `run.max_workers`: Maximum concurrent attempt-instance jobs inside a single run.
- `run.workspace_isolation_mode`: Workspace reuse policy. Supported values are `shared_repo_commit`, `task_scoped`, and `attempt_scoped`. Multi-attempt parallel runs automatically promote to `attempt_scoped`.
- `agent.extra_args`: Extra CLI args appended to `agent.command` in order. For Claude/Cursor/OpenCode/Codex wrappers, this is where Bitloops flags are passed.
- `model.seed`: Optional integer seed recorded in run metadata and forwarded to wrappers that support deterministic sampling. OpenCode maps it into runtime config automatically.

With this setup, the wrapper brings up an isolated task-local Bitloops runtime and runs
`bitloops init --agent <agent> --telemetry=false --sync=true --ingest=false --embeddings-runtime platform`.

Bitloops flags via `extra_args` (wrapper defaults in parentheses):
- `--bitloops-init`: enable Bitloops setup before the agent command.
- `--bitloops-sync true|false` (`true`): queue sync during `bitloops init`.
- `--bitloops-ingest true|false` (`false`): queue ingest during `bitloops init`.
- `--bitloops-embeddings-runtime local|platform`: choose embeddings runtime.

Use the same `extra_args` pattern for Cursor configs to compare `baseline` vs
`with_bitloops` under the `cursor` agent as well.
Use the same pattern for OpenCode with
`configs/swebench/rust_tokio_phase1_opencode.toml` plus Bitloops flags in
`agent.extra_args`.
Use the same pattern for Codex with
`configs/swebench/rust_tokio_phase1_codex_with_bitloops.toml`.

## Dataset viewer

A Streamlit app for browsing bug-report datasets (JSONL/JSON) with a GitHub-style diff viewer for patches.

1. Install viewer dependencies:

```bash
pip install -r requirements-dataset-viewer.txt
```

Or with the project installed: `pip install -e '.[viewer]'`.

2. Run the app:

```bash
streamlit run app.py
```

3. In the sidebar, set the dataset path (default: `datasets/swebench_multilingual.test.tokio.jsonl`), filter by repo, search problem statements, or jump to a record by `instance_id`. The main panel shows metadata, problem statement, hints, and side-by-side patch and test-patch diffs (rendered with diff2html).
