# Bitloops Benchmarks

Small benchmark harness for running SWE-bench Multilingual tasks with Codex or
OpenCode, either with or without Bitloops.

Start here: [docs/run-benchmarks.md](/Users/petros/Desktop/work/benchmarks/docs/run-benchmarks.md).

Main configs:

- `configs/swebench/codex.toml`
- `configs/swebench/opencode.toml`

Both configs use the same mode flag:

```bash
./.venv/bin/python -m benchkit.swebench.cli plan \
  --config configs/swebench/codex.toml \
  --mode baseline

./.venv/bin/python -m benchkit.swebench.cli run \
  --config configs/swebench/opencode.toml \
  --mode with_bitloops
```

Legacy configs, scripts, and notes are archived under `configs/swebench/archive/`,
`scripts/swebench/archive/`, and `docs/archive/`.
