# Benchmark: astral-sh__ruff-15330

**Problem**: ERA001 false positive inside inline script metadata with trailing additional comment

## Configs

```bash
# Baseline
python3 -m benchkit.swebench.cli run --config configs/swebench/ruff_15330_claude.toml

# With TestLens context
python3 -m benchkit.swebench.cli run --config configs/swebench/ruff_15330_claude_with_context.toml
```

## Results

### Baseline (no extra context)

| Attempt | Task ID | Status | Runtime (s) | Input Tokens | Output Tokens | Cost ($) | Tool Calls | Files Edited | Patch Size |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | astral-sh__ruff-15330 | invalid | 347.672 | 17 | 19,424 | 1.016 | 0 | 3 | 3,578 |

**Summary**: 0/1 solved (0.0%), runtime 348s, cost $1.02

### With TestLens context

_Pending..._

### Comparison

_Pending — will be filled after the TestLens context run completes._
