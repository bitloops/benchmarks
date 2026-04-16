# How To Run One Specific SWE-bench Task And View Results

This guide is for running a single task (or a small set of tasks) and quickly finding results.

## 1) Prerequisites and preflight checks

Required:
- Python `>=3.11`
- `git`
- Docker Desktop running
- `claude` CLI installed and authenticated
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
aws sso login --profile default
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

## 5) Create a single-task config

Use `include_instance_ids` for exact task targeting.

Example file: `configs/swebench/single_task_claude.toml`

```toml
[run]
benchmark = "swebench_multilingual"
dataset_path = "datasets/swebench_multilingual.test.all.jsonl"
split = "test"
language = ""
condition = "baseline"
include_repos = ["uutils/coreutils"]
include_instance_ids = ["uutils__coreutils-6682"]
attempts = 1
max_workers = 1
timeout_seconds = 1200
output_root = "runs"
prepare_workspace = true
repo_url_template = "https://github.com/{repo}.git"
git_bin = "git"
workspace_root = "datasets/workspaces/single_task"
workspace_timeout_seconds = 600

[agent]
id = "claude_code"
command = ["python3", "scripts/agents/claude_code_wrapper.py"]
extra_args = []

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
max_workers = 6
timeout_seconds = 7200
command_template = []
extra_args = []
```

Notes:
- Keep `evaluation.python_bin = "./.venv/bin/python"` to avoid evaluator failures with system Python.

### Optional: run the same task with Bitloops

If you want `with_bitloops` instead of baseline, update only these fields:

```toml
[run]
condition = "with_bitloops"

[agent]
extra_args = ["--bitloops-init"]
```

## 6) Verify selected tasks before run

```bash
./.venv/bin/python -m benchkit.swebench.cli plan \
  --config configs/swebench/single_task_claude.toml
```

Check that:
- `Selected instances` is not zero
- sample IDs include the task you want

## 7) Run and generate appendix automatically

```bash
./.venv/bin/python -m benchkit.swebench.cli run \
  --config configs/swebench/single_task_claude.toml \
  --appendix-output-dir reports/appendix/single_task
```

If you run a Bitloops variant config, use the same command with that config path.

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

In `reports/appendix/single_task/`:
- `appendix_minimal_per_task_log.csv`
- `appendix_minimal_results_table.csv`
- `appendix_per_attempt_breakdown.md`
- `appendix_prompt_tool_breakdown.md`
- `appendix_tool_invocation_breakdown.md`
