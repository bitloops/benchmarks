# Ollama Agent Loop Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Upgrade the SWE-bench Ollama wrapper so it behaves like a multi-turn coding agent and returns workspace edits as the primary benchmark output.

**Architecture:** Keep the existing Ollama adapter input/output contract, but replace the patch-only execution path with a `/api/chat` tool loop. The wrapper will expose a small local toolset, execute model-requested tools, and capture the final workspace diff with the existing shared benchmark flow.

**Tech Stack:** Python, Ollama `/api/chat`, benchkit SWE-bench wrappers, unittest

---

## File Structure

- Modify: `src/benchkit/swebench/agents/ollama/wrapper.py`
  - Add multi-turn tool-loop orchestration, tool schema generation, tool dispatch, and workspace-diff-first completion.
- Modify: `tests/test_ollama_wrapper.py`
  - Add focused regression tests for tool loop behavior and diff preference.
- Possibly modify: `docs/run-benchmarks-ollama.md`
  - Document that the wrapper now runs in an agent loop with local tools.

### Task 1: Add failing tests for the Ollama tool loop

**Files:**
- Modify: `tests/test_ollama_wrapper.py`
- Test: `tests/test_ollama_wrapper.py`

- [ ] **Step 1: Write a failing test for tool schema injection**

Add a test that builds an Ollama chat request body and asserts the request includes a `tools` entry for the supported local toolset instead of only a patch-only prompt.

- [ ] **Step 2: Run test to verify it fails**

Run: `./.venv/bin/python -m unittest tests.test_ollama_wrapper.OllamaWrapperTests.test_build_chat_request_body_includes_tool_schemas`
Expected: FAIL because the request body currently does not include tool schemas.

- [ ] **Step 3: Write a failing test for multi-turn tool execution**

Add a test that simulates:
- initial Ollama response with a `Read` tool call
- tool execution result returned to the conversation
- final Ollama response with no more tool calls

Assert that the wrapper continues the loop and records tool events.

- [ ] **Step 4: Run test to verify it fails**

Run: `./.venv/bin/python -m unittest tests.test_ollama_wrapper.OllamaWrapperTests.test_run_agent_loop_executes_tool_calls_until_completion`
Expected: FAIL because the wrapper currently performs only one chat call plus repair calls, not a general tool loop.

- [ ] **Step 5: Write a failing test for workspace diff preference**

Add a test that simulates:
- a final assistant message containing no valid patch text
- a workspace edit made through tools

Assert that the wrapper returns the workspace diff rather than failing patch extraction.

- [ ] **Step 6: Run test to verify it fails**

Run: `./.venv/bin/python -m unittest tests.test_ollama_wrapper.OllamaWrapperTests.test_agent_loop_prefers_workspace_diff_over_text_patch`
Expected: FAIL because the wrapper currently centers on extracted patch text.

### Task 2: Implement tool schemas and local tool execution

**Files:**
- Modify: `src/benchkit/swebench/agents/ollama/wrapper.py`
- Test: `tests/test_ollama_wrapper.py`

- [ ] **Step 1: Add local tool schema helpers**

Implement helpers that define the `Read`, `Grep`, `Glob`, `Bash`, and `Edit` tool schemas in Ollama’s expected format.

- [ ] **Step 2: Implement local tool dispatch**

Add functions that:
- validate tool names and arguments
- confine file operations to the workspace
- execute bounded commands for `Bash`
- return serialized tool results for the next chat turn

- [ ] **Step 3: Run focused tests**

Run: `./.venv/bin/python -m unittest tests.test_ollama_wrapper.OllamaWrapperTests.test_build_chat_request_body_includes_tool_schemas`
Expected: PASS

### Task 3: Replace the patch-only flow with a multi-turn chat loop

**Files:**
- Modify: `src/benchkit/swebench/agents/ollama/wrapper.py`
- Test: `tests/test_ollama_wrapper.py`

- [ ] **Step 1: Implement a bounded Ollama agent loop**

Replace the current “single response + repair” 중심 flow with a loop that:
- sends messages plus tool schemas
- executes returned tool calls
- appends tool results
- stops when no more tool calls are returned or the max turn limit is reached

- [ ] **Step 2: Preserve patch extraction only as fallback**

After the loop finishes:
- capture workspace diff first
- if diff exists, use it
- otherwise attempt textual patch extraction as a fallback
- otherwise fail clearly

- [ ] **Step 3: Run focused tests**

Run: `./.venv/bin/python -m unittest tests.test_ollama_wrapper.OllamaWrapperTests.test_run_agent_loop_executes_tool_calls_until_completion tests.test_ollama_wrapper.OllamaWrapperTests.test_agent_loop_prefers_workspace_diff_over_text_patch`
Expected: PASS

### Task 4: Keep diagnostics and metadata useful

**Files:**
- Modify: `src/benchkit/swebench/agents/ollama/wrapper.py`
- Test: `tests/test_ollama_wrapper.py`

- [ ] **Step 1: Update metadata capture for the new loop**

Record:
- tool invocation sequence
- tool counts
- loop turn count
- whether completion came from workspace diff or patch fallback

- [ ] **Step 2: Add clear failure behavior for no-edit runs**

If the loop exits without a workspace diff and without a valid patch fallback, emit a fatal error with enough context to diagnose the failure.

- [ ] **Step 3: Run focused tests**

Run: `./.venv/bin/python -m unittest tests.test_ollama_wrapper`
Expected: PASS

### Task 5: Verify integration behavior

**Files:**
- Modify: `docs/run-benchmarks-ollama.md` (if wording is now stale)
- Test: `tests/test_ollama_wrapper.py`

- [ ] **Step 1: Update docs if needed**

Document that the Ollama wrapper now uses a local tool-driven agent loop rather than expecting a one-shot patch response.

- [ ] **Step 2: Run the focused wrapper and common prompt tests**

Run: `./.venv/bin/python -m unittest tests.test_ollama_wrapper tests.test_agent_wrappers_common`
Expected: PASS

- [ ] **Step 3: Run any directly impacted config/metadata tests**

Run: `./.venv/bin/python -m unittest tests.test_ollama_config_metadata`
Expected: PASS
