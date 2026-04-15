# Benchmark: astral-sh__ruff-15309

## Configs

**Baseline** (no extra context):

```bash
python3 -m benchkit.swebench.cli run --config configs/swebench/ruff_15309_claude.toml
```

**With TestLens context** (injected hint):

```bash
python3 -m benchkit.swebench.cli run --config configs/swebench/ruff_15309_claude_with_context.toml
```

The only differences between the two configs:

- `condition = "baseline"` vs `condition = "with_testlens_context"` (for labeling in reports)
- `prompt_context` — the TestLens hint gets appended to the prompt as an `Additional context:` section

After both runs, generate the appendix for each and compare the per-attempt breakdowns (solve rate, cost, tokens). The `condition` column in the CSV makes it easy to distinguish them.

---

## Results (v1 — raw diff mode, broken)

> These runs used the old `--print` one-shot mode that asked the LLM to output a raw
> unified diff. Patches were syntactically malformed 100% of the time ("Patch Apply
> Failed"), so all results show `invalid` or `unsolved`.

### Baseline (raw diff mode)

| Attempt | Task ID | Status | Runtime (s) | Input Tokens | Output Tokens | Cost ($) | Tool Calls | Files Edited | Patch Size |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | astral-sh__ruff-15309 | invalid | 342.074 | 40 | 14,578 | 1.249 | 0 | 3 | 3,932 |
| 2 | astral-sh__ruff-15309 | unsolved | 414.092 | 32 | 16,315 | 1.496 | 0 | — | 0 |
| 3 | astral-sh__ruff-15309 | invalid | 480.255 | 56 | 23,596 | 1.991 | 0 | 3 | 3,702 |

**Summary**: 0/3 solved (0.0%), median runtime 414s, median cost $1.50

### With TestLens context (raw diff mode)

| Attempt | Task ID | Status | Runtime (s) | Input Tokens | Output Tokens | Cost ($) | Tool Calls | Files Edited | Patch Size |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | astral-sh__ruff-15309 | invalid | 474.274 | 28 | 12,810 | 0.807 | 0 | 3 | 4,295 |
| 2 | astral-sh__ruff-15309 | invalid | 428.288 | 69 | 20,925 | 1.930 | 0 | 3 | 3,763 |
| 3 | astral-sh__ruff-15309 | invalid | 245.026 | 40 | 11,939 | 0.991 | 0 | 5 | 7,001 |

**Summary**: 0/3 solved (0.0%), median runtime 428s, median cost $0.99

---

## Results (v2 — agentic mode, fixed)

> These runs use the fixed agentic wrapper: Claude gets full tool access
> (`--dangerously-skip-permissions`), edits files in the workspace, and the patch is
> captured via `git diff`. Workspace is reset (`git reset --hard` + `git clean -fd`)
> between attempts.

### Baseline (agentic mode)

| Attempt | Task ID | Status | Runtime (s) | Output Tokens | Cost ($) |
| --- | --- | --- | --- | --- | --- |
| 1 | astral-sh__ruff-15309 | solved | 269 | 7,488 | 0.936 |
| 2 | astral-sh__ruff-15309 | solved | 309 | 9,363 | 1.128 |
| 3 | astral-sh__ruff-15309 | solved | 245 | 7,023 | 0.850 |

**Summary**: 3/3 solved (100%), median runtime 269s, median cost $0.94

### With TestLens context (agentic mode)

| Attempt | Task ID | Status | Runtime (s) | Output Tokens | Cost ($) |
| --- | --- | --- | --- | --- | --- |
| 1 | astral-sh__ruff-15309 | solved | 217 | 6,945 | 0.821 |
| 2 | astral-sh__ruff-15309 | solved | 157 | 5,182 | 0.510 |
| 3 | astral-sh__ruff-15309 | solved | 213 | 6,893 | 0.691 |

**Summary**: 3/3 solved (100%), median runtime 213s, median cost $0.69

### Comparison: agentic baseline vs with TestLens context

| Metric | Baseline | With TestLens Context | Delta |
| --- | --- | --- | --- |
| Solve rate | 3/3 (100%) | 3/3 (100%) | — |
| Median runtime | 269s | 213s | **-21%** |
| Median cost | $0.94 | $0.69 | **-27%** |
| Median output tokens | 7,488 | 6,893 | **-8%** |

The TestLens context doesn't change the solve rate on this task (both are 100%), but it
meaningfully reduces cost (-27%) and runtime (-21%) — the agent needs fewer exploration
steps when it knows exactly which files and functions to target.
