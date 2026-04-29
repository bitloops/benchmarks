# SWE-bench Agent Benchmarking Approach

## Goal

Use SWE-bench Multilingual as the first benchmark suite, but evaluate **agents** (Claude Code, Cursor, OpenCode, Codex, and enhanced variants) instead of directly evaluating model APIs.

## Why This Structure

1. Keep SWE-bench logic stable and reusable.
2. Isolate agent-specific behavior behind adapters.
3. Preserve reproducibility via strict run metadata and artifacts.
4. Make it easy to add additional benchmark suites later.

## Architecture

### 1) Benchmark Adapter Layer

- `SWEbenchAdapter` handles instance loading, filtering, and output format compatibility with SWE-bench evaluation expectations.
- Future adapters (e.g., HumanEval, custom internal suites) should implement the same runner contract.

### 2) Agent Adapter Layer

- `AgentAdapter` contract receives a benchmark instance plus run context.
- It returns:
  - `patch` (candidate fix),
  - optional adapter metadata (latency, token usage, failure reason).
- Implementations:
  - `ClaudeCodeAdapter`
  - `CursorAdapter`
  - `OpencodeAdapter`
  - `CodexAdapter`
  - `NoopAgentAdapter` (dry-run/testing)

### 3) Runner and Artifacts

Each run creates:

- `run_manifest.json` with benchmark, agent, model, dataset, and timing metadata (OpenCode runs also include an `opencode` summary of `configs/opencode/opencode.json`).
- `instances.jsonl` with exact evaluated instance list.
- `attempts/attempt-<n>/predictions.jsonl`
- `attempts/attempt-<n>/trace.jsonl`
- `attempts/attempt-<n>/evaluation.json`
- `attempts/attempt-<n>/evaluation.tasks.jsonl` (when parseable)
- `summary.json`

This enables single-run and repeated-run analysis.

## Reproducibility Rules

1. Fix model configuration across agents for fair comparison.
2. Store dataset path + revision in every run.
3. Run multiple attempts explicitly (`attempts > 1`) to measure variance.
4. Do not merge outputs from different configs into one summary.

## Model Mapping

Use a canonical model name in `[model].name` (e.g., `opus-4-6`) and map it per
agent in `[model_map.<agent_id>]`. This keeps the benchmark definition stable
while allowing agent-specific CLI model identifiers.

Example:

```toml
[model]
provider = "anthropic"
name = "opus-4-6"

[model_map.claude_code]
"opus-4-6" = "eu.anthropic.claude-opus-4-6-v1"

[model_map.cursor]
"opus-4-6" = "opus-4.6"

[model_map.opencode]
"gpt-5" = "openai/gpt-5"

[model_map.codex]
"gpt-5.4" = "gpt-5.4"
```

`plan` and `run` validate this mapping strictly for known Anthropics families
(`opus`, `sonnet`, `haiku`) and fail fast with a suggested fix if the agent
model id is incompatible.

## Rust-First Rollout Plan

### Phase 1 (Now)

- Local scaffold with Rust filtering and artifact persistence.
- Dry-run and mock-agent support.

### Phase 2

- Add real Claude Code and Cursor wrappers using the JSON stdin/stdout contract.
- Generate SWE-bench-compatible `predictions.jsonl` files.

### Phase 3

- Direct SWE-bench harness invocation is available via `[evaluation]` config.
- Harness output parsing and appendix report generation are available.

### Phase 4

- Expand from Rust to all languages.
- Add additional benchmark suites through the same runner interface.

## One-Command Phase 1 Execution

Use:

```bash
./scripts/swebench/phase1_tokio_run.sh
```

This script runs export -> config validation -> Claude baseline -> Cursor
baseline -> OpenCode baseline -> appendix generation.

## Current Agent Wrapper Contract

Runner sends JSON on stdin:

```json
{
  "instance_id": "example__repo-123",
  "repo": "org/repo",
  "base_commit": "abc123",
  "problem_statement": "...",
  "language": "rust",
  "model": {
    "provider": "anthropic",
    "name": "eu.anthropic.claude-opus-4-6-v1",
    "canonical_name": "opus-4-6",
    "temperature": 0.0,
    "seed": 4242,
    "max_tokens": 32000
  }
}
```

The runner always sends `model.temperature`, `model.max_tokens`, and `model.seed` from the benchmark TOML for manifests and non-OpenCode agents. **OpenCode** does not apply those fields to the CLI; configure sampling in `configs/opencode/opencode.json` instead.

Wrapper must output JSON to stdout:

```json
{
  "patch": "diff --git ...",
  "metadata": {
    "notes": "optional fields"
  }
}
```

## Next Implementation Targets

1. Add richer parsed evaluation metrics extraction into reports.
2. Add run comparison report generation for agent-vs-agent summaries.
3. Add Multi-SWE-bench adapter under the same runner interfaces.
