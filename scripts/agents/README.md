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
    "canonical_name": "...",
    "temperature": 0.0,
    "seed": 4242,
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

- `benchkit.swebench.agents.claude_code_wrapper` (module entrypoint)
- `benchkit.swebench.agents.cursor_wrapper` (module entrypoint)
- `benchkit.swebench.agents.codex.wrapper` (module entrypoint)
- `benchkit.swebench.agents.opencode.wrapper` (module entrypoint)
- `benchkit.swebench.agents.ollama.wrapper` (module entrypoint)

All wrappers:

1. Read the benchmark payload from stdin.
2. Build the benchmark task prompt.
3. Call the respective CLI in non-interactive print mode.
4. Parse output and extract a unified diff patch.
5. Print JSON response with `patch` and `metadata`.

`benchkit.swebench.agents.codex.wrapper` follows the same contract and metadata shape.
`benchkit.swebench.agents.opencode.wrapper` follows the same contract and metadata shape.

For Claude Bedrock runs with `BENCHKIT_REQUIRE_EXACT_TOOLS=1`, metadata also includes:
- `tool_invocations_raw` (exact per-call tool-use events)
- `tool_invocations_curated` (normalized per-call details: grep/read/bash/edit + fallback raw input JSON)

Token usage metadata includes:
- `token_input` / `token_output` extracted from the terminal Claude `type="result"` event when using stream-json output (same canonical semantics as non-stream JSON output).
- `token_metrics_source` indicating extraction provenance (`result_usage`, `result_model_usage`, `fallback_scan`, or `fallback_max_candidate`).
- Canonical cross-agent dashboard fields:
  - `input_tokens`
  - `output_tokens`
  - `cache_read_input_tokens`
  - `cache_creation_input_tokens`
  - `total_input_processed_tokens`
  - `total_processed_tokens`
- Canonical token glossary:
  - `input_tokens`: fresh, non-cached input tokens sent to the model.
  - `output_tokens`: generated output tokens, excluding reasoning tokens.
  - `cache_read_input_tokens`: input tokens reused from cache.
  - `cache_creation_input_tokens`: input tokens written into cache.
  - `total_input_processed_tokens`: `input_tokens + cache_read_input_tokens + cache_creation_input_tokens`.
  - `total_processed_tokens`: `total_input_processed_tokens + output_tokens`.
- Agent semantics for canonical token fields:
  - Claude: `input_tokens = token_input`; cache read/write come from the transcript fields directly.
  - Codex: `input_tokens = token_input - cached_input_tokens`; `cache_read_input_tokens = cached_input_tokens`; `cache_creation_input_tokens = 0` unless explicitly reported later.
  - OpenCode: `input_tokens = token_input`; cache read/write come from `tokens.cache.read` and `tokens.cache.write`.
  - Cursor: `input_tokens = token_input`; cache read/write default to `0` when not reported.
- Raw provider-native token fields remain in trace metadata for audit/debugging, but report tables should expose only the canonical cross-agent fields above.

The wrapper uses `payload.model.name` as the concrete CLI model ID. The runner can
map a canonical benchmark model to this value via `model_map` in TOML config.

Optional wrapper argument:

- `--bitloops-init`: runs non-interactive Bitloops setup in the task workspace
  before invoking the agent CLI:
  - resolves the task-local Bitloops sandbox/runtime
  - for isolated per-task daemons, writes the benchmark-generated daemon config and installs it once per daemon config root with `bitloops configure --file <config.toml> --no-start`
  - starts an isolated per-task daemon when sandboxing is enabled
  - if the workspace is on detached `HEAD`, switches to a temporary local branch for sync
- by default runs `bitloops init --agent <agent> --sync=true --ingest=true`
- sets `BITLOOPS_TELEMETRY_OPTOUT=1` for Bitloops setup and the agent command environment
- `--bitloops-embeddings-runtime <local|platform>`: selects the embeddings runtime in benchmark-generated Bitloops config
- `--bitloops-summaries-runtime <local|platform>`: selects the summaries runtime during `bitloops init`
- `--bitloops-summary-embeddings-mode <on|off>`: forwards `--summary-embeddings-mode` during `bitloops init`
- `--bitloops-no-embeddings`: disables embeddings through benchmark-generated repo config
- `--bitloops-no-summaries`: disables summaries through benchmark-generated repo config
- `--bitloops-summary-mode <auto|off|on>`: deprecated benchmark-wrapper control retained for compatibility; prefer `--bitloops-summaries-runtime` and/or `--bitloops-no-summaries`

With no extra args, benchmark wrappers issue:

```bash
bitloops init --agent <agent> --sync=true --ingest=true
```

To opt back into both embeddings and summaries, use:

```toml
[run]
condition = "with_bitloops"
workspace_timeout_seconds = 1800
bitloops_sandbox_mode = "per_task_daemon"

[agent]
extra_args = [
  "--bitloops-init",
  "--bitloops-embeddings-runtime", "platform",
  "--bitloops-summaries-runtime", "platform",
  "--bitloops-summary-embeddings-mode", "on",
]
```

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

## Codex Wrapper

Command shape:

```bash
codex exec --json --model <model> --cd <workspace> --full-auto "<prompt>"
```

Env vars:

- `CODEX_BIN` (default: `codex`)
- `CODEX_MODEL` (fallback model if payload.model.name is empty; default wrapper fallback: `gpt-5.4`)
- `CODEX_EXTRA_ARGS` (extra CLI args, shell-split)
- `CODEX_TIMEOUT_SECONDS` (default: `900`)
- `CODEX_FULL_AUTO` (default: `true`; set false to use explicit sandbox mode below)
- `CODEX_SANDBOX` (default: `workspace-write`; used only when `CODEX_FULL_AUTO=false`)
- `CODEX_SKIP_GIT_REPO_CHECK` (default: `false`)

Codex-specific notes:

- The wrapper loads committed runtime defaults from `configs/codex/codex.json`.
- If `CODEX_CONFIG_CONTENT` is set, it is JSON-merged first, then `configs/codex/codex.json` is merged over it.
- Runtime knobs are shared in one place (`model_reasoning_effort`, `model_verbosity`, `model_reasoning_summary`, timeout/full-auto/sandbox defaults), while benchmark TOML keeps run filtering and model mapping.

Typical non-interactive setting:

```bash
export CODEX_EXTRA_ARGS="--ephemeral"
```

## OpenCode Wrapper

Command shape:

```bash
opencode run --format json --model <provider/model> --agent <agent> --dangerously-skip-permissions "<prompt>"
```

Env vars:

- `OPENCODE_BIN` (default: `opencode`)
- `OPENCODE_MODEL` (fallback model if payload.model.name is empty; default wrapper fallback: `openai/gpt-5`)
- `OPENCODE_AGENT` (default: `build`)
- `OPENCODE_EXTRA_ARGS` (extra CLI args, shell-split)
- `OPENCODE_TIMEOUT_SECONDS` (default: `900`)

OpenCode-specific notes:
- Minimal prompt runs can legitimately finish with an empty patch. Benchkit records those as empty predictions unless the OpenCode JSONL stream contains a top-level error event, in which case the wrapper still fails the run as invalid.

- Provider credentials are expected in OpenCode auth storage, typically `~/.local/share/opencode/auth.json`.
- The wrapper loads committed defaults from `configs/opencode/opencode.json` and passes them via `OPENCODE_CONFIG_CONTENT` (merged with any existing `OPENCODE_CONFIG_CONTENT` from the environment). Benchmark TOML **does not** override OpenCode sampling; edit the JSON file instead.
- Keep `configs/opencode/opencode.json` small: only `agent.build`, `agent.plan`, and the provider model entry should carry benchmark sampling unless a smoke run proves another OpenCode internal agent needs explicit settings.

Typical non-interactive setting:

```bash
export OPENCODE_AGENT="build"
```

## Config Examples

Claude:

```toml
[agent]
id = "claude_code"
command = ["python3", "-m", "benchkit.swebench.agents.claude_code_wrapper"]
```

Cursor:

```toml
[agent]
id = "cursor"
command = ["python3", "-m", "benchkit.swebench.agents.cursor_wrapper"]
```

Codex:

```toml
[agent]
id = "codex"
command = ["python3", "-m", "benchkit.swebench.agents.codex.wrapper"]
```

OpenCode:

```toml
[agent]
id = "opencode"
command = ["python3", "-m", "benchkit.swebench.agents.opencode.wrapper"]
```

Ollama:

```toml
[agent]
id = "ollama"
command = ["python3", "-m", "benchkit.swebench.agents.ollama.wrapper"]
```
