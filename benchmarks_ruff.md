# Benchmark: astral-sh/ruff (all 7 tasks)

Agent: Claude Code (`claude-opus-4-6`) via `--print` (single-shot, no tool use)

## Tasks

| # | Instance ID | Problem | FAIL_TO_PASS | PASS_TO_PASS |
|---|-------------|---------|-------------|-------------|
| 1 | astral-sh__ruff-15309 | F523 fix leaves empty `.format()` | 1 | 4 |
| 2 | astral-sh__ruff-15330 | ERA001 false positive in inline script metadata | 1 | 12 |
| 3 | astral-sh__ruff-15356 | False positive E252, double output | 1 | 133 |
| 4 | astral-sh__ruff-15394 | PIE800 fix introduced a syntax error | 1 | 8 |
| 5 | astral-sh__ruff-15443 | PTH123/S102 check `builtin` instead of `builtins` | 1 | 58 |
| 6 | astral-sh__ruff-15543 | UP028 fix fails on unparenthesized tuple | 1 | 124 |
| 7 | astral-sh__ruff-15626 | SIM201/SIM202 fixes should be marked unsafe | 2 | 34 |

## How to run

```bash
# 1 attempt (default)
python3 -m benchkit.swebench.cli run --config configs/swebench/ruff_all_baseline.toml

# Override to N attempts
python3 -m benchkit.swebench.cli run --config configs/swebench/ruff_all_baseline.toml --attempts 3

# Generate report
python3 -m benchkit.swebench.cli appendix \
  --run-root runs/swebench_multilingual/<date>/<run_id> \
  --output-dir reports/appendix/ruff_all_baseline
```

## Results

### Baseline (1 attempt)

_Pending..._
