# Benchmarking Guide

End-to-end instructions for running SWE-bench Multilingual benchmarks on Rust tasks
with Claude Code, Cursor, OpenCode, and Codex agents.

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
- `cursor-agent` CLI installed and authenticated (`cursor-agent --version`)
- `codex` CLI installed and authenticated (`codex --version`)
- `opencode` CLI installed and authenticated (`opencode --version`)
- `bitloops` CLI installed (`bitloops --version`) for `with_bitloops` runs
- Git

### Bedrock environment

Run benchmarks with the repo virtualenv and Bedrock environment configured:

```bash
source .venv/bin/activate

export CLAUDE_CODE_USE_BEDROCK=1
export AWS_PROFILE=default
export AWS_REGION=eu-central-1
export AWS_SDK_LOAD_CONFIG=1
export BENCHKIT_REQUIRE_EXACT_TOOLS=1
```

Notes:
- Use `./.venv/bin/python` for commands in this repo.
- The benchmark setup assumes Bedrock auth is already working.
- `claude auth status` should show Bedrock before you start a run.
- `BENCHKIT_REQUIRE_EXACT_TOOLS=1` enforces per-tool event capture (run fails if only aggregate tool counts are available).

## 1. Export the dataset

Pull Rust tasks from HuggingFace into a local JSONL file:

```bash
./.venv/bin/python -m benchkit.swebench.cli export-hf \
  --split test \
  --language rust \
  --output datasets/swebench_multilingual.test.rust_all.jsonl \
  --overwrite
```

To export only a single repo:

```bash
./.venv/bin/python -m benchkit.swebench.cli export-hf \
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
./.venv/bin/python -m benchkit.swebench.cli run \
  --config configs/swebench/ruff_15309_claude.toml
```

See [`configs/swebench/ruff_15309_claude.toml`](configs/swebench/ruff_15309_claude.toml)
— targets `astral-sh__ruff-15309` with condition `baseline`.

**With extra context**:

```bash
./.venv/bin/python -m benchkit.swebench.cli run \
  --config configs/swebench/ruff_15309_claude_with_context.toml
```

See [`configs/swebench/ruff_15309_claude_with_context.toml`](configs/swebench/ruff_15309_claude_with_context.toml)
— same task but with `prompt_context` injected and condition `with_testlens_context`.

**Codex baseline**:

```bash
./.venv/bin/python -m benchkit.swebench.cli run \
  --config configs/swebench/rust_tokio_phase1_codex.toml
```

See [`configs/swebench/rust_tokio_phase1_codex.toml`](configs/swebench/rust_tokio_phase1_codex.toml)
for the default Codex baseline.

**OpenCode baseline**:

```bash
./.venv/bin/python -m benchkit.swebench.cli run \
  --config configs/swebench/rust_tokio_phase1_opencode.toml
```

See [`configs/swebench/rust_tokio_phase1_opencode.toml`](configs/swebench/rust_tokio_phase1_opencode.toml)
for the default OpenCode baseline.

Other single-task configs available:

| Config | Task | Condition |
| --- | --- | --- |
| [`ruff_15309_claude.toml`](configs/swebench/ruff_15309_claude.toml) | `astral-sh__ruff-15309` | baseline |
| [`ruff_15309_claude_with_context.toml`](configs/swebench/ruff_15309_claude_with_context.toml) | `astral-sh__ruff-15309` | with_testlens_context |
| [`ruff_15330_claude.toml`](configs/swebench/ruff_15330_claude.toml) | `astral-sh__ruff-15330` | baseline |
| [`ruff_15330_claude_with_context.toml`](configs/swebench/ruff_15330_claude_with_context.toml) | `astral-sh__ruff-15330` | with_testlens_context |

### Bitloops-enabled condition

Use a config with:
- `condition = "with_bitloops"`
- `workspace_timeout_seconds = 1800`
- `bitloops_sandbox_mode = "per_task_daemon"`
- `[agent].extra_args = ["--bitloops-init", "--bitloops-embeddings-runtime", "platform"]`

Canonical multi-repo Claude + Bitloops (Rust selection across repos):

- [`rust_all_repos_claude_with_bitloops.toml`](configs/swebench/rust_all_repos_claude_with_bitloops.toml)

Example Tokio config:
- [`rust_tokio_phase1_claude_with_bitloops.toml`](configs/swebench/rust_tokio_phase1_claude_with_bitloops.toml)
- [`rust_tokio_phase1_codex_with_bitloops.toml`](configs/swebench/rust_tokio_phase1_codex_with_bitloops.toml)

### Key TOML fields

| Field | Purpose |
| --- | --- |
| `include_instance_ids` | Target specific tasks by SWE-bench ID |
| `attempts` | How many times to run each task |
| `condition` | Label for reports (`baseline`, `with_testlens_context`, etc.) |
| `prompt_context` | Extra text appended to the agent's prompt |
| `agent.extra_args` | Wrapper toggles (for Claude/OpenCode + Bitloops use `["--bitloops-init", "--bitloops-embeddings-runtime", "platform"]`) |

### Override attempts from the CLI

```bash
./.venv/bin/python -m benchkit.swebench.cli run \
  --config configs/swebench/ruff_15309_claude.toml \
  --attempts 5
```

## 3. Run all Rust tasks (or a whole repo)

```bash
./.venv/bin/python -m benchkit.swebench.cli run \
  --config configs/swebench/ruff_all_claude.toml
```

See [`configs/swebench/ruff_all_claude.toml`](configs/swebench/ruff_all_claude.toml)
— runs all `astral-sh/ruff` tasks in `datasets/swebench_multilingual.test.ruff.jsonl`.

To run the 4-attempt Ruff benchmark sweep used for reporting, either edit `attempts = 4`
in the config or override attempts from the CLI:

```bash
./.venv/bin/python -m benchkit.swebench.cli run \
  --config configs/swebench/ruff_all_claude.toml \
  --attempts 4
```

That run can be reported to a directory like:
- `reports/appendix/ruff_all_4_attempts`

To target a different scope, edit these fields in your config:

- `include_repos = ["astral-sh/ruff"]` — filter by repository
- `include_instance_ids = []` — leave empty for all tasks matching repo/language
- `language = "rust"` — filter by language (alternative to `include_repos`)
- `max_instances` — cap the number of tasks

## 4. Changing the model

The model is configured in two TOML sections. No code changes needed.

**Claude Opus 4.6 on Bedrock**:

```toml
[model]
name = "opus-4-6"

[model_map.claude_code]
"opus-4-6" = "eu.anthropic.claude-opus-4-6-v1"
```

`[model].name` is the canonical label used in reports and the `model_version` CSV
column. `model_map` maps it to the actual `--model` CLI flag value passed to the
`claude` command.

**Codex default**:

```toml
[model]
name = "gpt-5.4"

[model_map.codex]
"gpt-5.4" = "gpt-5.4"
```

**OpenCode default**:

```toml
[model]
name = "gpt-5"
temperature = 0.0
# seed = 4242

[model_map.opencode]
"gpt-5" = "openai/gpt-5"
```

OpenCode auth and runtime behavior:
- provider credentials live in `~/.local/share/opencode/auth.json`
- committed benchmark OpenCode defaults live in `configs/opencode/opencode.json`
- benchmark config stays in TOML: set `[model].name`, `temperature`, and optional `seed`
- the wrapper injects the committed repo config and benchmark runtime overrides through `OPENCODE_CONFIG_CONTENT`
- benchmark `temperature` and `seed` still come from the TOML runtime overrides, so TOML remains the final source for those knobs

Example provider credential file:

```json
{
  "fireworks": {
    "type": "api",
    "key": "YOUR_API_KEY"
  }
}
```

## 5. Generate reports

After a run completes, generate CSV and Markdown reports:

```bash
./.venv/bin/python -m benchkit.swebench.cli appendix \
  --run-root runs/swebench_multilingual/<date>/<run_id> \
  --output-dir reports/appendix/my_report
```

The run root path is printed at the end of every `run` command.

### Compare multiple runs

Pass multiple `--run-root` flags to combine runs into a single report:

```bash
./.venv/bin/python -m benchkit.swebench.cli appendix \
  --run-root runs/swebench_multilingual/20260317/20260317_151318_1a9dbd \
  --run-root runs/swebench_multilingual/20260317/20260317_153808_af673c \
  --output-dir reports/appendix/test1
```

The aggregated results table is grouped by `agent` + `condition`, so for
Bitloops A/B runs you should see separate rows for `baseline` and `with_bitloops`.

Run the reusable A/B helper script (baseline config + Bitloops config):

```bash
./scripts/swebench/run_ab_compare.sh
```

Optional overrides:

```bash
BASELINE_CONFIG=configs/swebench/rust_tokio_phase1_claude.toml \
EXPERIMENT_CONFIG=configs/swebench/rust_tokio_phase1_claude_with_bitloops.toml \
APPENDIX_DIR=reports/appendix/tokio_claude_bitloops_ab \
RUN_MAX_WORKERS=2 \
./scripts/swebench/run_ab_compare.sh
```

### Output files

The appendix command generates seven files:

| File | Description |
| --- | --- |
| `appendix_minimal_per_task_log.jsonl` | One JSON object per task-attempt with all fields |
| `appendix_minimal_per_task_log.csv` | Same data as a flat CSV (see column reference below) |
| `appendix_minimal_results_table.csv` | Aggregated results grouped by agent/condition |
| `appendix_minimal_results_table.md` | Markdown version of the results table |
| `appendix_per_attempt_breakdown.md` | Detailed per-attempt markdown table |
| `appendix_prompt_tool_breakdown.md` | Prompt text + exact tool usage/sequence per condition |
| `appendix_tool_invocation_log.jsonl` | One JSON object per tool call with raw + curated invocation details |
| `appendix_tool_invocation_breakdown.md` | Human-readable per-call breakdown (grep/read/bash/edit details) |

## 6. Query results with SQLite

Import appendix results plus run metadata into a local SQLite database:

```bash
./.venv/bin/python -m benchkit.swebench.cli db-import \
  --appendix-csv reports/appendix/20attempts/appendix_minimal_per_task_log.csv \
  --run-root runs/swebench_multilingual/20260326/20260326_134738_d4468c \
  --db-path reports/benchmarks.sqlite
```

Then inspect the database:

```bash
sqlite3 reports/benchmarks.sqlite
```

Exit SQLite with:

```sql
.quit
```

Example grouped query by task:

```sql
SELECT
  task_id,
  COUNT(*) AS attempts,
  SUM(CASE WHEN status = 'solved' THEN 1 ELSE 0 END) AS solved,
  AVG(runtime_sec) AS avg_runtime_sec,
  AVG(estimated_cost) AS avg_cost
FROM task_attempts
GROUP BY task_id
ORDER BY task_id;
```

Notes:
- Re-importing the same run does not duplicate rows.
- The database is append-only in practice, keyed by `(run_id, task_id, attempt)`.
- Main tables are `runs` and `task_attempts`.
- Helper views include `run_summary` and `model_condition_summary`.

## 7. Bedrock quota note

For Claude Opus 4.6 on Bedrock, the relevant quota is:
- `Global cross-region model inference tokens per day for Anthropic Claude Opus 4.6 V1`
- Applied account-level quota: `2,592,000`

Operationally, this quota is exhausted at roughly `~30` Opus benchmark attempts in
our runs. For larger sweeps, split runs across days, reduce attempts, or use a
cheaper model.

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
| model_version | run manifest | Resolved model name (e.g. `eu.anthropic.claude-opus-4-6-v1`) |
| condition | run manifest | `baseline`, `with_testlens_context`, etc. |
| status | evaluation harness | `solved` / `unsolved` / `invalid` |
| runtime_sec | trace metadata | Agent wall-clock time in seconds |
| token_input | Claude JSON | Input tokens consumed |
| token_output | Claude JSON | Output tokens generated |
| reasoning_output_tokens | Codex JSON (when available) | Reasoning output tokens reported by agent runtime |
| total_tokens | Codex JSON / derived | Total tokens excluding reasoning output (or `token_input + token_output` fallback) |
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
./.venv/bin/python -m benchkit.swebench.cli export-hf --split test --language rust \
  --output datasets/swebench_multilingual.test.rust_all.jsonl --overwrite

# Run a single task on Bedrock
./.venv/bin/python -m benchkit.swebench.cli run \
  --config configs/swebench/ruff_15309_claude.toml

# Run all ruff tasks with 4 attempts
./.venv/bin/python -m benchkit.swebench.cli run \
  --config configs/swebench/ruff_all_claude.toml --attempts 4

# Generate reports
./.venv/bin/python -m benchkit.swebench.cli appendix \
  --run-root runs/swebench_multilingual/<date>/<run_id> \
  --output-dir reports/appendix/my_report

# Import report into SQLite
./.venv/bin/python -m benchkit.swebench.cli db-import \
  --appendix-csv reports/appendix/20attempts/appendix_minimal_per_task_log.csv \
  --run-root runs/swebench_multilingual/20260326/20260326_134738_d4468c \
  --db-path reports/benchmarks.sqlite

# Query grouped results by task
sqlite3 reports/benchmarks.sqlite "
SELECT
  task_id,
  COUNT(*) AS attempts,
  AVG(runtime_sec) AS avg_runtime_sec,
  AVG(estimated_cost) AS avg_cost
FROM task_attempts
GROUP BY task_id
ORDER BY task_id;
"
```
