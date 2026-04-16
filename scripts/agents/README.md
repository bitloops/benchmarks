# Agent Wrapper Scripts

The SWE-bench runner expects each agent wrapper command to:

1. Read one JSON payload from stdin.
2. Return one JSON response to stdout.

Input shape:

```json
{
  "instance_id": "...",
  "repo": "...",
  "base_commit": "...",
  "problem_statement": "...",
  "language": "rust",
  "model": {
    "provider": "...",
    "name": "...",
    "temperature": 0.0,
    "max_tokens": 32000
  }
}
```

Output shape:

```json
{
  "patch": "diff --git ...",
  "metadata": {
    "latency_ms": 1234
  }
}
```

Use `mock_agent.py` as a minimal reference implementation.

## Implemented Wrappers

- `claude_code_wrapper.py`
- `cursor_wrapper.py`

Both wrappers:

1. Read the benchmark payload from stdin.
2. Build a strict patch-only prompt.
3. Call the respective CLI in non-interactive print mode.
4. Parse output and extract a unified diff patch.
5. Print JSON response with `patch` and `metadata`.

For Claude Bedrock runs with `BENCHKIT_REQUIRE_EXACT_TOOLS=1`, metadata also includes:
- `tool_invocations_raw` (exact per-call tool-use events)
- `tool_invocations_curated` (normalized per-call details: grep/read/bash/edit + fallback raw input JSON)

Token usage metadata includes:
- `token_input` / `token_output` extracted from the terminal Claude `type="result"` event when using stream-json output (same canonical semantics as non-stream JSON output).
- `token_metrics_source` indicating extraction provenance (`result_usage`, `result_model_usage`, `fallback_scan`, or `fallback_max_candidate`).

The wrapper uses `payload.model.name` as the concrete CLI model ID. The runner can
map a canonical benchmark model to this value via `model_map` in TOML config.

Optional wrapper argument:

- `--bitloops-init`: runs non-interactive Bitloops setup in the task workspace
  before invoking the agent CLI:
  - checks daemon status
  - starts daemon (`bitloops start --detached`)
  - if needed, bootstraps daemon config (`bitloops start --create-default-config --telemetry=false --detached`)
  - if the workspace is on detached `HEAD`, switches to a temporary local branch for sync
  - runs `bitloops init --agent <agent> --telemetry=false --install-default-daemon --sync=true --ingest=false`

## Claude Wrapper

Command shape:

```bash
claude --print --output-format stream-json --include-partial-messages --model <model> "<prompt>"
```

Env vars:

- `CLAUDE_BIN` (default: `claude`)
- `CLAUDE_MODEL` (fallback model if payload.model.name is empty)
- `CLAUDE_EXTRA_ARGS` (extra CLI args, shell-split)
- `CLAUDE_TIMEOUT_SECONDS` (default: `900`)
- `CLAUDE_OUTPUT_FORMAT` (default: `stream-json`)
- `CLAUDE_INCLUDE_PARTIAL_MESSAGES` (default: enabled for `stream-json`)
- `CLAUDE_STREAM_JSON_VERBOSE` (default: enabled; auto-adds `--verbose` for `stream-json`)
- `BENCHKIT_REQUIRE_EXACT_TOOLS` (default: enabled when `CLAUDE_CODE_USE_BEDROCK=1`; fails run if per-tool events are missing)

Typical non-interactive setting:

```bash
export CLAUDE_EXTRA_ARGS="--permission-mode bypassPermissions"
```

## Cursor Wrapper

Command shape:

```bash
cursor-agent --print --output-format json --workspace <path> --model <model> --trust "<prompt>"
```

Env vars:

- `CURSOR_AGENT_BIN` (default: `cursor-agent`)
- `CURSOR_MODEL` (fallback model if payload.model.name is empty)
- `CURSOR_TRUST_FLAG` (default: `--trust`; set empty to disable)
- `CURSOR_EXTRA_ARGS` (extra CLI args, shell-split)
- `CURSOR_TIMEOUT_SECONDS` (default: `900`)

Typical non-interactive setting:

```bash
export CURSOR_EXTRA_ARGS="--force --trust"
```

## Config Examples

Claude:

```toml
[agent]
id = "claude_code"
command = ["python3", "scripts/agents/claude_code_wrapper.py"]
```

Cursor:

```toml
[agent]
id = "cursor"
command = ["python3", "scripts/agents/cursor_wrapper.py"]
```
