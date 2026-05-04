# Ollama Agent Loop Design

## Goal

Upgrade the SWE-bench Ollama wrapper from a patch-only generator into a multi-turn coding agent that behaves more like the existing Codex and OpenCode wrappers in this repo.

## Problem

The current Ollama wrapper is optimized around a single `/api/chat` response that must contain a valid unified diff patch. When that patch is malformed, targets the wrong files, or omits necessary investigation steps, the wrapper falls back to an empty patch and the run is marked unsolved. This differs from the Codex and OpenCode paths, which let the agent inspect the repo, run commands, edit files, and then capture the final workspace diff.

## Desired Behavior

The Ollama wrapper should:

- run a multi-turn agent loop against `/api/chat`
- expose a bounded local toolset to the model
- execute model-requested tools locally and feed results back into the conversation
- prefer the actual workspace diff as the benchmark output
- keep patch extraction only as a fallback path
- fail clearly when the loop terminates without useful edits

## Architecture

The wrapper will keep the existing benchmark input/output contract:

- input: JSON payload on stdin
- output: JSON with `patch` and metadata on stdout

Internally, the wrapper will change from a one-shot patch request to a loop:

1. Build the initial prompt with the shared `render_task_prompt(...)` helper.
2. Build an Ollama chat request that includes tool schemas.
3. Call `/api/chat`.
4. If the response contains tool calls, execute them locally, append tool results as chat messages, and continue the loop.
5. If the response contains no tool calls, stop the loop.
6. Capture the workspace diff.
7. If a workspace diff exists, emit it as the result.
8. If no workspace diff exists, optionally fall back to extracting a textual patch from the final response.
9. If neither exists, fail with explicit diagnostics instead of silently downgrading to an empty “success”.

This matches the repo’s practical agent contract more closely: the benchmark result should come from concrete workspace edits rather than a single text diff.

## Toolset

The first Ollama toolset will be intentionally small:

- `Read`: read a whole small file or a bounded line window from a file
- `Grep`: search the workspace for text patterns
- `Glob`: list matching files or directories
- `Bash`: run bounded repo-local commands such as `rg`, `git diff`, `cargo test`, or other verification commands
- `Edit`: apply targeted text edits inside workspace files

These tools are enough for the agent to inspect code, validate assumptions, modify files, and run tests without introducing a large or risky surface area.

## Safety and Limits

The loop will include:

- a maximum turn count
- per-tool output truncation
- command timeouts
- workspace-root confinement for read/edit operations
- a clear failure mode when the model loops without producing edits

The wrapper should avoid treating a missing diff as success. Empty output can remain available behind a targeted opt-in only if existing tests require it.

## Compatibility

The benchmark-facing contract should remain stable:

- no changes to the adapter payload format
- no changes to run manifests beyond richer metadata
- no changes to the evaluator path

The new behavior should stay local to the Ollama wrapper and reuse shared helpers where practical.

## Testing Strategy

Add regression coverage for:

- tool schema and multi-turn chat request construction
- tool-call execution and conversation continuation
- workspace diff being preferred over text patch extraction
- clear failure when the loop ends without edits
- preservation of patch-extraction fallback for non-tool responses

## Success Criteria

The wrapper is successful when Ollama runs behave like agent runs rather than patch-only runs:

- the model can inspect files and run repo-local checks through tools
- successful runs return `workspace_git_diff`-style output
- invalid text-only patches are no longer the main execution path
- common Ollama failures become diagnosable instead of silently collapsing into empty patches
