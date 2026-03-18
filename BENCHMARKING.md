# Benchmarking Guide

End-to-end instructions for running SWE-bench Multilingual benchmarks on Rust tasks
with the Claude Code agent.

## Prerequisites

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e '.[hf]'
python -m pip install swebench
```

You also need:
- Docker Desktop running (`docker info` should succeed)
- `claude` CLI installed and authenticated (`claude --version`)
- Git

## 1. Export the dataset

Pull Rust tasks from HuggingFace into a local JSONL file:

```bash
python3 -m benchkit.swebench.cli export-hf \
  --split test \
  --language rust \
  --output datasets/swebench_multilingual.test.rust_all.jsonl \
  --overwrite
```

To export only a single repo:

```bash
python3 -m benchkit.swebench.cli export-hf \
  --split test \
  --repo astral-sh/ruff \
  --output datasets/swebench_multilingual.test.ruff.jsonl \
  --overwrite
```

Set `HF_TOKEN` if you need authenticated access.

## 2. Run a single task

Use an existing config as-is or copy it as a starting point.

**Baseline** (no extra context):

```bash
python3 -m benchkit.swebench.cli run \
  --config configs/swebench/ruff_15309_claude.toml
```

See [`configs/swebench/ruff_15309_claude.toml`](configs/swebench/ruff_15309_claude.toml)
— targets `astral-sh__ruff-15309`, 3 attempts, Opus 4.6, condition `baseline`.

**With extra context**:

```bash
python3 -m benchkit.swebench.cli run \
  --config configs/swebench/ruff_15309_claude_with_context.toml
```

See [`configs/swebench/ruff_15309_claude_with_context.toml`](configs/swebench/ruff_15309_claude_with_context.toml)
— same task but with `prompt_context` injected and condition `with_testlens_context`.

Other single-task configs available:

| Config | Task | Condition |
| --- | --- | --- |
| [`ruff_15309_claude.toml`](configs/swebench/ruff_15309_claude.toml) | `astral-sh__ruff-15309` | baseline |
| [`ruff_15309_claude_with_context.toml`](configs/swebench/ruff_15309_claude_with_context.toml) | `astral-sh__ruff-15309` | with_testlens_context |
| [`ruff_15330_claude.toml`](configs/swebench/ruff_15330_claude.toml) | `astral-sh__ruff-15330` | baseline |
| [`ruff_15330_claude_with_context.toml`](configs/swebench/ruff_15330_claude_with_context.toml) | `astral-sh__ruff-15330` | with_testlens_context |

### Key TOML fields

| Field | Purpose |
| --- | --- |
| `include_instance_ids` | Target specific tasks by SWE-bench ID |
| `attempts` | How many times to run each task |
| `condition` | Label for reports (`baseline`, `with_testlens_context`, etc.) |
| `prompt_context` | Extra text appended to the agent's prompt |

### Override attempts from the CLI

```bash
python3 -m benchkit.swebench.cli run \
  --config configs/swebench/ruff_15309_claude.toml --attempts 5
```

## 3. Run all Rust tasks (or a whole repo)

```bash
python3 -m benchkit.swebench.cli run \
  --config configs/swebench/ruff_all_baseline.toml
```

See [`configs/swebench/ruff_all_baseline.toml`](configs/swebench/ruff_all_baseline.toml)
— runs all `astral-sh/ruff` tasks (up to `max_instances = 7`), 1 attempt each,
condition `baseline`.

To target a different scope, edit these fields in your config:

- `include_repos = ["astral-sh/ruff"]` — filter by repository
- `include_instance_ids = []` — leave empty for all tasks matching repo/language
- `language = "rust"` — filter by language (alternative to `include_repos`)
- `max_instances` — cap the number of tasks

## 4. Changing the model

The model is configured in two TOML sections. No code changes needed.

**Claude Opus 4.6** (default — used by all existing configs):

```toml
[model]
name = "opus-4-6"

[model_map.claude_code]
"opus-4-6" = "claude-opus-4-6"
```

**Claude Sonnet 4.6**:

```toml
[model]
name = "sonnet-4-6"

[model_map.claude_code]
"sonnet-4-6" = "claude-sonnet-4-6"
```

`[model].name` is the canonical label used in reports and the `model_version` CSV
column. `model_map` maps it to the actual `--model` CLI flag value passed to the
`claude` command.

## 5. Generate reports

After a run completes, generate CSV and Markdown reports:

```bash
python3 -m benchkit.swebench.cli appendix \
  --run-root runs/swebench_multilingual/<date>/<run_id> \
  --output-dir reports/appendix/my_report
```

The run root path is printed at the end of every `run` command.

### Compare multiple runs

Pass multiple `--run-root` flags to combine baseline and with-context runs into
a single report:

```bash
python3 -m benchkit.swebench.cli appendix \
  --run-root runs/swebench_multilingual/20260317/20260317_151318_1a9dbd \
  --run-root runs/swebench_multilingual/20260317/20260317_153808_af673c \
  --output-dir reports/appendix/baseline_vs_context
```

### Output files

The appendix command generates five files:

| File | Description |
| --- | --- |
| `appendix_minimal_per_task_log.jsonl` | One JSON object per task-attempt with all fields |
| `appendix_minimal_per_task_log.csv` | Same data as a flat CSV (see column reference below) |
| `appendix_minimal_results_table.csv` | Aggregated results grouped by agent/condition |
| `appendix_minimal_results_table.md` | Markdown version of the results table |
| `appendix_per_attempt_breakdown.md` | Detailed per-attempt markdown table |

## CSV column reference

The per-task CSV (`appendix_minimal_per_task_log.csv`) contains these columns:

| Column | Source | Description |
| --- | --- | --- |
| task_id | instance JSONL | SWE-bench instance ID (e.g. `astral-sh__ruff-15309`) |
| attempt | directory name | Attempt number (1-based) |
| benchmark | run manifest | e.g. `swebench_multilingual` |
| benchmark_version | run manifest | Dataset path + split |
| repo | instance | e.g. `astral-sh/ruff` |
| repo_label | instance metadata | Owner prefix (e.g. `astral-sh`) |
| language | instance / manifest | e.g. `rust` |
| agent | run manifest | e.g. `claude_code` |
| model_version | run manifest | Resolved model name (e.g. `claude-opus-4-6`) |
| condition | run manifest | `baseline`, `with_testlens_context`, etc. |
| status | evaluation harness | `solved` / `unsolved` / `invalid` |
| runtime_sec | trace metadata | Agent wall-clock time in seconds |
| token_input | Claude JSON | Input tokens consumed |
| token_output | Claude JSON | Output tokens generated |
| estimated_cost | Claude JSON | Total cost in USD (reported by Claude CLI) |
| tool_calls | Claude JSON `num_turns` | Number of agentic tool-use turns |
| shell_commands | Claude JSON | Bash tool invocations |
| file_reads | Claude JSON | Read tool invocations |
| search_actions | Claude JSON | Search/grep invocations |
| files_edited | prediction patch | Count of files changed (`diff --git` headers) |
| patch_size | prediction patch | Character length of the generated patch |
| first_file_opened | trace metadata | First file the agent read |
| first_file_edited | trace metadata | First file the agent edited |
| first_test_command | trace metadata | First test command the agent ran |
| bitloops_context_tokens | trace metadata | Token count of injected context |
| evaluator_result | evaluation harness | Raw JSON verdict from SWE-bench |

## Quick reference

```bash
# Export all Rust tasks
python3 -m benchkit.swebench.cli export-hf --split test --language rust \
  --output datasets/swebench_multilingual.test.rust_all.jsonl --overwrite

# Run a single task (baseline, 3 attempts)
python3 -m benchkit.swebench.cli run --config configs/swebench/ruff_15309_claude.toml

# Run a single task (with context, 3 attempts)
python3 -m benchkit.swebench.cli run --config configs/swebench/ruff_15309_claude_with_context.toml

# Run all ruff tasks (baseline, 1 attempt each)
python3 -m benchkit.swebench.cli run --config configs/swebench/ruff_all_baseline.toml

# Generate reports
python3 -m benchkit.swebench.cli appendix \
  --run-root runs/swebench_multilingual/<date>/<run_id> \
  --output-dir reports/appendix/my_report
```
