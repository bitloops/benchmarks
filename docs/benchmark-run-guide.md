# Benchmark run guide (single task or small slice)

This guide is for running a single task (or a small set of tasks) and quickly finding results.

## 1) Prerequisites and preflight checks

Required:
- Python `>=3.11`
- `git`
- Docker Desktop running
- `claude` CLI installed and authenticated
- `codex` CLI installed and authenticated (for Codex runs)
- `opencode` CLI installed and authenticated (for OpenCode runs)
- `bitloops` CLI installed (only for `with_bitloops` runs)

Install dependencies once:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e '.[hf]'
python -m pip install swebench
```

Preflight checks (run before benchmark runs):

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

## 2) Activate environment

```bash
source .venv/bin/activate
```

## 3) Bedrock authentication (if running via Bedrock)

1. Open AWS SSO start page: [AWS Start Page](https://d-9967478a64.awsapps.com/start)
2. Sign in and select a role with **PowerUser** access.
3. Login from terminal:

```bash
aws login --region eu-central-1
```

Set Bedrock environment:

```bash
export CLAUDE_CODE_USE_BEDROCK=1
export AWS_PROFILE=default
export AWS_REGION=eu-central-1
export AWS_SDK_LOAD_CONFIG=1
export BENCHKIT_REQUIRE_EXACT_TOOLS=1
```

## 4) Export dataset once (if needed)

If you already have `datasets/swebench_multilingual.test.all.jsonl`, skip this step.

```bash
./.venv/bin/python -m benchkit.swebench.cli export-hf \
  --split test \
  --output datasets/swebench_multilingual.test.all.jsonl \
  --overwrite
```

## 5) Canonical Claude + Bitloops config

The primary multi-repo Claude + Bitloops run is defined in:

`configs/swebench/rust_all_repos_claude_with_bitloops.toml`

It matches the current pattern: `with_bitloops`, per-task Bitloops sandbox, platform embeddings, Bedrock model map, and long enough timeouts for index setup.

```toml
[run]
benchmark = "swebench_multilingual"
dataset_path = "datasets/swebench_multilingual.test.all.jsonl"
include_repos = [
  "tokio-rs/tokio",
  "uutils/coreutils",
  "nushell/nushell",
  "tokio-rs/axum",
  "burntsushi/ripgrep",
  "sharkdp/bat",
  "astral-sh/ruff",
]
split = "test"
language = ""
condition = "with_bitloops"
attempts = 1
max_workers = 5
timeout_seconds = 7200
output_root = "runs"
prepare_workspace = true
repo_url_template = "https://github.com/{repo}.git"
git_bin = "git"
workspace_root = "datasets/workspaces/rust_all"
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
python_bin = "python3"
dataset_name = "SWE-bench/SWE-bench_Multilingual"
split = "test"
max_workers = 2
timeout_seconds = 7200
command_template = []
extra_args = []
```

There is also `run.max_instances` (optional), which sets how many tasks will run.

Notes:
- Prefer `evaluation.python_bin = "./.venv/bin/python"` in a local copy if system `python3` is not your venv (avoids evaluator import issues).
- `run.max_instances` is optional and limits selected tasks after filters (`include_repos`, `include_instance_ids`, etc.). Omit it to run all matches; when set it must be `>= 1`.
- `agent.extra_args` is passed through to the wrapper command in order. Use it for Bitloops wrapper flags such as `--bitloops-init`, `--bitloops-sync true|false` (default `true`), and `--bitloops-ingest true|false` (default `false`).

### Narrowing to one task (optional)

Use `include_instance_ids` for exact task targeting. Copy the canonical file (for example to `configs/swebench/single_task_claude_platform_bitloops.toml`) and set:

```toml
include_instance_ids = ["uutils__coreutils-6682"]
include_repos = ["uutils/coreutils"]
max_workers = 1
```

### Codex variant (same task)

For Codex runs, use:

```toml
[agent]
id = "codex"
command = ["python3", "scripts/agents/codex_wrapper.py"]
extra_args = []

[model]
provider = "openai"
name = "gpt-5.4"
temperature = 0.0
max_tokens = 32000

[model_map.codex]
"gpt-5.4" = "gpt-5.4"
```

<<<<<<< Updated upstream:docs/benchmark-run-guide.md
=======
### OpenCode variant (same task)

```toml
[agent]
id = "opencode"
command = ["python3", "scripts/agents/opencode_wrapper.py"]
extra_args = []

[model]
provider = "openai"
name = "gpt-5"
temperature = 0.0
# seed = 4242
max_tokens = 32000

[model_map.opencode]
"gpt-5" = "openai/gpt-5"
```

OpenCode provider credentials are managed separately from benchmark TOML. Keep
them in OpenCode auth storage, typically `~/.local/share/opencode/auth.json`.

>>>>>>> Stashed changes:docs/how-to-run-single-task-and-view-results.md
### Optional: baseline variant

If you want a plain Claude baseline instead, use a separate config such as
`configs/swebench/single_task_claude_baseline.toml` and update these fields:

```toml
[run]
condition = "baseline"
workspace_timeout_seconds = 600

[agent]
extra_args = []
```

The canonical config uses the current Claude + Bitloops setup:
- isolated per-task Bitloops sandboxing
- `bitloops init --agent claude-code --telemetry=false --sync=true --ingest=false --embeddings-runtime platform`
- the same Bedrock model mapping pattern used by the other Claude configs

## 6) Verify selected tasks before run

```bash
./.venv/bin/python -m benchkit.swebench.cli plan \
  --config configs/swebench/rust_all_repos_claude_with_bitloops.toml
```

Check that:
- `Selected instances` is not zero
- sample IDs include the task you want

## 7) Run and generate appendix automatically

```bash
./.venv/bin/python -m benchkit.swebench.cli run \
  --config configs/swebench/rust_all_repos_claude_with_bitloops.toml \
  --appendix-output-dir reports/appendix/rust_all_repos_claude_with_bitloops
```

If you run a single-task copy or the baseline variant instead, use that config path and a distinct `--appendix-output-dir` (for example `reports/appendix/single_task_claude_baseline`).

## 8) Where to find outputs

### Run artifacts

Latest run folder:

```bash
ls -1t runs/swebench_multilingual/$(date +%Y%m%d) | head -n 1
```

Inside run folder:
- `run_manifest.json`
- `summary.json`
- `instances.jsonl`
- `attempts/attempt-01/predictions.jsonl`
- `attempts/attempt-01/trace.jsonl`
- `attempts/attempt-01/evaluation.json`
- `attempts/attempt-01/evaluation.tasks.jsonl` (when evaluator succeeds)

### Appendix outputs

In `reports/appendix/rust_all_repos_claude_with_bitloops/` (when using the canonical config above):
- `appendix_minimal_per_task_log.csv`
- `appendix_minimal_results_table.csv`
- `appendix_per_attempt_breakdown.md`
- `appendix_prompt_tool_breakdown.md`
- `appendix_tool_invocation_breakdown.md`
