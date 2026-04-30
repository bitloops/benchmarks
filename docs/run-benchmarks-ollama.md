# Run Benchmarks (Ollama)

This page is the Ollama-specific companion to [Run Benchmarks](run-benchmarks.md). SWE-bench scope, `plan` / `run` / `appendix` flow, results layout, and the shared **[SWE-bench config reference](run-benchmarks.md#swe-bench-config-reference)** are the same as in the main doc; here we only spell out what differs when the agent preset is **Ollama**.

## Config and preset

- Benchmark TOML: **`configs/swebench/ollama.toml`**
- Preset: **`ollama`** (defaults in [`src/benchkit/common/config.py`](../src/benchkit/common/config.py) under `_config_presets()["ollama"]`)
- Agent process: `python3 scripts/agents/ollama_wrapper.py` (JSON on stdin, JSON patch result on stdout), same adapter pattern as Codex/OpenCode wrappers.

Modes match the other agents:

- **`baseline`**: normal run
- **`with_bitloops`**: same wrapper with Bitloops init flags (see main doc and `docs/bitloops-init-status-guide.md` if needed)

## 1. Install

Use the same venv and package install as [Run Benchmarks §1](run-benchmarks.md#1-install):

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e '.[hf]'
python -m pip install swebench
```

Additional prerequisites:

```bash
command -v ollama    # Ollama CLI; daemon must be reachable for chat API
command -v python3   # runs scripts/agents/ollama_wrapper.py
command -v docker
docker info
```

For **`with_bitloops`**, also `command -v bitloops` as in the main doc.

Start Ollama (or point the wrapper at your API base URL; see [Runtime JSON and overrides](#runtime-json-and-overrides)). Pull or otherwise ensure the model you configure is available to that server.

## 2. Pick mode

Baseline:

```bash
CONFIG=configs/swebench/ollama.toml
MODE=baseline
```

With Bitloops:

```bash
CONFIG=configs/swebench/ollama.toml
MODE=with_bitloops
```

## 3. Dataset and tasks

Identical to [Run Benchmarks §3–4](run-benchmarks.md#3-create-dataset-if-missing): default dataset path in `ollama.toml`, `export-hf` if missing, edit `[run]` / `include_instance_ids` / `include_repos` / `max_instances` in **`configs/swebench/ollama.toml`**.

## Ollama-specific TOML fields

Everything in the shared [config reference](run-benchmarks.md#swe-bench-config-reference) applies. Ollama-specific pieces:

### `[model]`

`model.name` is the canonical label for the run. The wrapper resolves the actual model id in this order: payload from benchkit → **`OLLAMA_MODEL`** env → `model` in **`configs/ollama/ollama.json`** → default string in code.

### `[model_map.ollama]`

Same idea as OpenCode’s `[model_map.opencode]`: keys are canonical names (matching `model.name`); values are the model names sent to the Ollama API after resolution ([`src/benchkit/swebench/model_mapper.py`](../src/benchkit/swebench/model_mapper.py)).

### Runtime JSON and overrides

- **File:** `configs/ollama/ollama.json` — `base_url`, `model`, `timeout_seconds`, `max_num_predict`, `options` (e.g. `temperature`, `num_predict`, `seed`), merged with optional inline JSON from **`OLLAMA_CONFIG_CONTENT`** (see `scripts/agents/ollama_wrapper.py`).

Useful **environment overrides** (all optional; see wrapper for full behavior):

| Variable | Role |
| --- | --- |
| `OLLAMA_BASE_URL` | Overrides `base_url` (default from JSON or `http://localhost:11434`). |
| `OLLAMA_MODEL` | Overrides resolved model name. |
| `OLLAMA_TIMEOUT_SECONDS` | Participates in timeout resolution with config and run payload. |
| `OLLAMA_MAX_PREDICT` | Caps / sets max tokens for generation where applicable. |
| `OLLAMA_AUTH_TOKEN` | Bearer token for APIs that require it (e.g. some cloud endpoints). |
| `OLLAMA_CONFIG_CONTENT` | Raw JSON object merged over the repo file (handy for CI secrets-free tweaks). |

Strict / smoke-related knobs used by the wrapper include `BENCHKIT_OLLAMA_STRICT_APPLY` and `BENCHKIT_ALLOW_EMPTY_OLLAMA_PATCH` (see `scripts/agents/ollama_wrapper.py`).

## 4. Preview and run

Same CLI as the main doc; only `CONFIG` / `MODE` change.

Preview:

```bash
./.venv/bin/python -m benchkit.swebench.cli plan \
  --config "$CONFIG" \
  --mode "$MODE"
```

Run (with appendix folder under `reports/appendix/`, named like `ollama_baseline_<timestamp>` or `ollama_bitloops_<timestamp>`):

```bash
./.venv/bin/python -m benchkit.swebench.cli run \
  --config "$CONFIG" \
  --mode "$MODE" \
  --appendix
```

Appendix-only generation and `--max-workers` overrides behave as in [Run Benchmarks §6](run-benchmarks.md#6-run).

## 5. Find results

Same layout as [Run Benchmarks §7](run-benchmarks.md#7-find-results): `runs/swebench_multilingual/...`, `summary.json`, `attempts/attempt-01/…`, etc.

## Notes

- For Codex/OpenCode, use [Run Benchmarks](run-benchmarks.md).
- TOML controls **what** runs; **`configs/ollama/ollama.json`** controls **how** the wrapper talks to Ollama (unless env vars override).
- Cloud-tagged models (name ending with `:cloud`) get conservative generation defaults in the wrapper when `max_num_predict` is not set; adjust JSON or env if you need different limits.
