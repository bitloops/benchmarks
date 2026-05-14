# Bitloops Benchmarks

Small benchmark harness for running SWE-bench tasks with Codex, Ollama, or
OpenCode, either with or without Bitloops.

Supported benchmark profiles:

- `swebench_multilingual` (legacy Rust-focused path)
- `swebench_pro` (JS/TS-first path)

Start here: [docs/run-benchmarks.md](/Users/petros/Desktop/work/benchmarks/docs/run-benchmarks.md).

Main configs:

- `configs/swebench/codex.toml`
- `configs/swebench/codex_pro.toml`
- `configs/swebench/opencode.toml`
- `configs/swebench/opencode_ollama.toml`
- `configs/swebench/opencode_pro.toml.disabled`

Quick Pro run path:

```bash
./.venv/bin/python -m benchkit.swebench.cli plan \
  --config configs/swebench/codex_pro.toml \
  --mode baseline

./.venv/bin/python -m benchkit.swebench.cli run \
  --config configs/swebench/codex_pro.toml \
  --mode baseline
```

Legacy multilingual path:

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
