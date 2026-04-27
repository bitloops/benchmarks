# Benchmark run guide (single task or small slice)

This guide is for running a single task or a small filtered set of tasks and
quickly finding the results.

## 1) Prerequisites and preflight checks

Required:
- Python `>=3.11`
- `git`
- Docker Desktop running
- `claude` CLI installed and authenticated for Claude runs
- `codex` CLI installed and authenticated for Codex runs
- `opencode` CLI installed and authenticated for OpenCode runs
- `bitloops` CLI installed for `with_bitloops` runs

Install dependencies once:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e '.[hf]'
python -m pip install swebench
```

Preflight checks:

```bash
python -c "import datasets, swebench; print('python deps ok')"
command -v claude
command -v codex
command -v opencode
command -v bitloops
docker info
claude auth status
```

If `docker info` fails, start Docker Desktop first.

If dataset export is rate-limited, set:

```bash
export HF_TOKEN=your_huggingface_token
```

## 2) Activate the environment

```bash
source .venv/bin/activate
```

## 3) Agent authentication

### Claude via Bedrock

Bedrock auth is only needed for Claude runs that use Bedrock model IDs. That is
the case for the Claude configs currently checked into this repo, because their
`[model_map.claude_code]` values look like
`eu.anthropic.claude-opus-4-6-v1`.

If you are running one of those Claude configs, set up AWS auth first:

1. Open AWS SSO start page: [AWS Start Page](https://d-9967478a64.awsapps.com/start)
2. Sign in and select a role with PowerUser access.
3. Login from the terminal:

```bash
aws login --region eu-central-1
```

Then export:

```bash
export CLAUDE_CODE_USE_BEDROCK=1
export AWS_PROFILE=default
export AWS_REGION=eu-central-1
export AWS_SDK_LOAD_CONFIG=1
export BENCHKIT_REQUIRE_EXACT_TOOLS=1
```

Notes:
- Bedrock auth is not required for Codex or OpenCode runs.
- Bedrock auth is not required just because you are using Bitloops, embeddings,
  or summaries.
- If you switch Claude to a non-Bedrock model ID and authenticate `claude`
  normally, you can run without the AWS exports above.

### Codex

Authenticate the `codex` CLI normally. No Bedrock or AWS setup is needed.

### OpenCode

Authenticate OpenCode normally. Provider credentials typically live in
`~/.local/share/opencode/auth.json`. No Bedrock or AWS setup is needed unless
your chosen model provider separately requires it.

## 4) Export the dataset once (if needed)

If you already have `datasets/swebench_multilingual.test.all.jsonl`, skip this
step.

```bash
./.venv/bin/python -m benchkit.swebench.cli export-hf \
  --split test \
  --output datasets/swebench_multilingual.test.all.jsonl \
  --overwrite
```

## 5) Start from a benchmark TOML

The canonical multi-repo Claude + Bitloops config is:

`configs/swebench/rust_all_repos_claude_with_bitloops.toml`

It uses:
- `condition = "with_bitloops"`
- per-task Bitloops sandboxing
- platform embeddings runtime
- the current Claude Bedrock model map

There are also narrower starting points, such as:
- `configs/swebench/rust_opencode.toml` (Use this for OpenCode)
- `configs/swebench/rust_tokio_phase1_claude_with_bitloops.toml`
- `configs/swebench/rust_tokio_phase1_codex_with_bitloops.toml`

Copy one of those to a local working config before editing it.

## 6) Standard TOML patterns

### Base Bitloops shape

Use this as the baseline shape for a Bitloops-enabled run:

```toml
[run]
benchmark = "swebench_multilingual"
dataset_path = "datasets/swebench_multilingual.test.all.jsonl"
split = "test"
language = "rust"
condition = "with_bitloops"
include_repos = ["tokio-rs/tokio"]
include_instance_ids = []
max_instances = 5
attempts = 1
max_workers = 2
timeout_seconds = 7200
output_root = "runs"
prepare_workspace = true
repo_url_template = "https://github.com/{repo}.git"
git_bin = "git"
workspace_timeout_seconds = 1800
bitloops_sandbox_mode = "per_task_daemon"

[agent]
id = "claude_code"
command = ["python3", "scripts/agents/claude_code_wrapper.py"]
extra_args = ["--bitloops-init", "--bitloops-embeddings-runtime", "platform"]

[model]
provider = "anthropic"
name = "opus-4-6"
temperature = 0.0
max_tokens = 32000

[model_map.claude_code]
"opus-4-6" = "eu.anthropic.claude-opus-4-6-v1"

[evaluation]
enabled = true
python_bin = "./.venv/bin/python"
dataset_name = "SWE-bench/SWE-bench_Multilingual"
split = "test"
max_workers = 2
timeout_seconds = 7200
command_template = []
extra_args = []
```

### Run exactly 5 tasks

Set:

```toml
max_instances = 5
```

Important:
- `max_instances` limits how many tasks run after repo/language/task filters are
  applied.
- `max_workers` is only parallelism. It does not change how many tasks are
  selected.
- Use the `plan` command before running so you can confirm that exactly five
  instances were selected.

### Run without ingest

Ingest defaults to `true`, so either of these is fine:

Set:

```toml
extra_args = ["--bitloops-init", "--bitloops-ingest", "false"]
```

### Run with platform embeddings

Set:

```toml
extra_args = [
  "--bitloops-init",
  "--bitloops-embeddings-runtime", "platform",
]
```

### Common wrapper flags

These wrapper flags are currently supported by Claude, Codex, and OpenCode
Bitloops runs:

| Flag | Values | Purpose |
| --- | --- | --- |
| `--bitloops-init` | none | Run Bitloops setup before the agent starts |
| `--bitloops-sync` | `true`, `false` | Control whether init queues sync |
| `--bitloops-ingest` | `true`, `false` | Control whether init queues ingest |
| `--bitloops-embeddings-runtime` | `local`, `platform` | Select embeddings runtime for init |
| `--bitloops-no-embeddings` | none | Disable embeddings setup during init |
| `--bitloops-summary-mode` | `auto`, `off` | Override summary mode in task-local config |
| `--bitloops-embedding-mode` | `off`, `deterministic`, `refresh_on_upgrade`, `semantic_aware_once` | Override embedding mode in task-local config |

Use benchmark TOML `extra_args` to request those overrides. You do not need to
manually create the repo-local `config.toml` for benchmark runs.

## 7) Narrow to one task (optional)

Use `include_instance_ids` for exact task targeting:

```toml
include_instance_ids = ["uutils__coreutils-6682"]
include_repos = ["uutils/coreutils"]
max_instances = 1
max_workers = 1
```

## 8) Verify selected tasks before the run

```bash
./.venv/bin/python -m benchkit.swebench.cli plan \
  --config configs/swebench/rust_opencode.toml
```

Check that:
- `Selected instances` is the count you expect
- sample IDs include the tasks you want

If you want five tasks, this command is the fastest way to verify that
`max_instances = 5` plus your filters produced the right slice.

## 9) Run the benchmark

```bash
./.venv/bin/python -m benchkit.swebench.cli run \
  --config configs/swebench/rust_opencode.toml \
  --appendix-output-dir reports/appendix/rust_opencode
```

If you copied the config to a local file, use that path instead.

## 10) Where to find outputs

### Run artifacts

Latest run folder:

```bash
ls -1t runs/swebench_multilingual/$(date +%Y%m%d) | head -n 1
```

Inside the run folder:
- `run_manifest.json`
- `summary.json`
- `instances.jsonl`
- `attempts/attempt-01/predictions.jsonl`
- `attempts/attempt-01/trace.jsonl`
- `attempts/attempt-01/evaluation.json`
- `attempts/attempt-01/evaluation.tasks.jsonl` when evaluator succeeds

### Appendix outputs

Inside your appendix directory:
- `appendix_minimal_per_task_log.csv`
- `appendix_minimal_results_table.csv`
- `appendix_per_attempt_breakdown.md`
- `appendix_prompt_tool_breakdown.md`
- `appendix_tool_invocation_breakdown.md`
