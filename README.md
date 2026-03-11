# Bitloops Benchmarks

Scaffold for agent-focused benchmarking with SWE-bench Multilingual first, then additional benchmark suites.

## Current Scope

- SWE-bench Multilingual integration scaffold
- Rust-first task filtering
- Agent adapters (Claude Code, Cursor, noop)
- Run artifacts and metadata for reproducibility

## Environment Setup

Run these before `./scripts/swebench/phase1_tokio_run.sh`.

1. System prerequisites:
   - Python `>=3.11`
   - `git`
   - Docker Desktop / Docker daemon running
   - `claude` CLI installed and authenticated
   - `cursor-agent` CLI installed and authenticated
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
docker info
```

5. Run Phase 1:

```bash
./scripts/swebench/phase1_tokio_run.sh
```

If needed, you can force the script to use a specific interpreter:

```bash
PYTHON_BIN=python ./scripts/swebench/phase1_tokio_run.sh
```

## Quick Start

1. Pick a config:
   - `configs/swebench/rust_canary.toml` (mock agent)
   - `configs/swebench/rust_claude_code.toml` (Claude Code wrapper)
   - `configs/swebench/rust_cursor.toml` (Cursor wrapper)
   - `configs/swebench/rust_tokio_phase1_claude.toml` (Tokio Phase 1)
   - `configs/swebench/rust_tokio_phase1_cursor.toml` (Tokio Phase 1)
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
3. Run `run` for Claude and Cursor baselines.
4. Run `appendix` on both run roots to generate appendix files.

Use `model_map` in config if an agent expects a different CLI model ID than the
canonical benchmark model name.

`plan` and `run` now perform strict agent/model normalization checks and fail
fast on mismatches (example: Cursor with `claude-opus-4-6` will error and suggest
`opus-4.6`).

Run the full Tokio Phase 1 flow in one command:

```bash
./scripts/swebench/phase1_tokio_run.sh
```

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
