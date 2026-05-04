# Run Benchmarks (Ollama)

This page is the Ollama-specific companion to [Run Benchmarks](run-benchmarks.md). SWE-bench scope, `plan` / `run` / `appendix` flow, results layout, and the shared **[SWE-bench config reference](run-benchmarks.md#swe-bench-config-reference)** are the same as in the main doc; here we only spell out what differs when the agent preset is **Ollama**.

## Config and preset

- Benchmark TOML: **`configs/swebench/ollama.toml`**
- Runtime JSON: **`configs/ollama/ollama.json`**
- Preset: **`ollama`** (defaults in [`src/benchkit/common/config.py`](../src/benchkit/common/config.py) under `_config_presets()["ollama"]`)
- Agent process: `python3 -m benchkit.swebench.agents.ollama.wrapper` (JSON on stdin, JSON patch result on stdout), same adapter pattern as Codex/OpenCode wrappers.

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
command -v python3   # launches -m benchkit.swebench.agents.ollama.wrapper
command -v docker
docker info
```

For **`with_bitloops`**, also `command -v bitloops` as in the main doc.

Start Ollama. For **cloud** models used through the local daemon (names ending in `:cloud`, for example `deepseek-v4-flash:cloud`), run **`ollama signin`** once so the daemon can authenticate cloud requests. For local models, sign-in is not required.

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

`model.name` is the normal source of truth for model selection in benchmark runs. In practice:

- Use a **local model** name like `qwen3-coder` to run inference on your own hardware.
- Use a **cloud model** name ending in `:cloud` like `deepseek-v4-flash:cloud` to keep the local Ollama API but offload model inference to Ollama Cloud after `ollama signin`.

The wrapper resolves the actual model id in this order: payload from benchkit → **`OLLAMA_MODEL`** env → runtime JSON `model` (if present) → default string in code.

### Prompt protocol and retrieval

Ollama runs use the retrieval-backed **`swe`** prompt protocol by default. The wrapper now runs a bounded local agent loop over Ollama `/api/chat`: it can inspect files, search the repo, edit files, and run repo-local commands through wrapper-provided tools before the benchmark captures the final workspace diff.

- Default protocol: `run.prompt_protocol = "swe"`
- Default retrieval: `run.retrieval.file_source = "bm25"` and `run.retrieval.k = 10`
- You can still override these in the benchmark TOML if you want to compare prompt shapes intentionally.

### `[model_map.ollama]`

Same idea as OpenCode’s `[model_map.opencode]`: keys are canonical names (matching `model.name`); values are the model names sent to the Ollama API after resolution ([`src/benchkit/swebench/model_mapper.py`](../src/benchkit/swebench/model_mapper.py)).

### Runtime JSON and overrides

- **File:** `configs/ollama/ollama.json`
- Standard route: local Ollama daemon at `http://localhost:11434`
- Purpose: transport/runtime settings such as `base_url`, `timeout_seconds`, `max_num_predict`, and `options` (for example `temperature`, `num_predict`, `seed`)

Useful **environment overrides** (all optional; these are advanced overrides, not the normal setup):

| Variable | Role |
| --- | --- |
| `OLLAMA_BASE_URL` | Overrides `base_url` (default from JSON or `http://localhost:11434`). |
| `OLLAMA_MODEL` | Advanced override; normal benchmark runs should set the model in `ollama.toml` instead. |
| `OLLAMA_TIMEOUT_SECONDS` | Participates in timeout resolution with config and run payload. |
| `OLLAMA_MAX_PREDICT` | Caps / sets max tokens for generation where applicable. |
| `OLLAMA_AUTH_TOKEN` | Optional bearer token for custom authenticated endpoints. |
| `OLLAMA_CONFIG_CONTENT` | Raw JSON object merged over the runtime JSON (handy for CI tweaks). |

Strict / smoke-related knobs used by the wrapper include `BENCHKIT_OLLAMA_STRICT_APPLY` and `BENCHKIT_ALLOW_EMPTY_OLLAMA_PATCH` (see `src/benchkit/swebench/agents/ollama/wrapper.py`).

### Local vs cloud models

There is one supported repo flow: the benchmark talks to your **local Ollama daemon**.

- If `model.name` is a normal local model, inference happens on your machine.
- If `model.name` ends with `:cloud`, inference is offloaded to Ollama Cloud, but the benchmark still talks to `http://localhost:11434`.
- In the `:cloud` case you usually want `ollama signin`, not an API key.

### Using a `.env` file

The benchmark CLI now auto-loads the first `.env` file it finds from:

- your current working directory, or
- the selected config path and its parent directories

That means a repo-root `.env` works for commands like `plan`, `run`, and `export-hf` without a manual `source .env`. Existing shell exports still take precedence over `.env` values.

Example repo-root `.env` for optional overrides:

```bash
OLLAMA_TIMEOUT_SECONDS=1200
OLLAMA_MAX_PREDICT=2048
```

There is also a starter file at `./.env.example`.

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
- `ollama.toml` controls **what** runs; `configs/ollama/ollama.json` controls **how** the wrapper talks to Ollama (unless env vars override).
- Cloud-tagged models (name ending with `:cloud`) get conservative generation defaults in the wrapper when `max_num_predict` is not set; adjust JSON or env if you need different limits.
