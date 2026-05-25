# Run Benchmarks (Ollama)

This page is the Ollama-specific companion to [Run Benchmarks](run-benchmarks.md). SWE-bench scope, `plan` / `run` / `appendix` flow, results layout, and the shared **[SWE-bench config reference](run-benchmarks.md#swe-bench-config-reference)** are the same as in the main doc; here we only spell out what differs when the benchmark uses **OpenCode with Ollama as the provider**.

## Config and preset

- Benchmark TOML: **`configs/swebench/opencode_ollama.toml`** for local inference
- Benchmark TOML: **`configs/swebench/opencode_ollama_cloud.toml`** for Ollama Cloud-backed inference
- OpenCode runtime JSON: **`configs/opencode/opencode.json`**
- Ollama daemon JSON: **`configs/opencode/ollama.json`**
- Preset: **`opencode`** with `[model] provider = "ollama"`
- Agent process: `python3 -m benchkit.swebench.agents.opencode.wrapper` (JSON on stdin, JSON patch result on stdout)

This matches the official integration direction documented by Ollama and OpenCode: `ollama launch opencode` configures OpenCode through `OPENCODE_CONFIG_CONTENT`, and OpenCode deep-merges that provider config with its normal config sources ([Ollama integration docs](https://docs.ollama.com/integrations/opencode), [OpenCode provider docs](https://opencode.ai/docs/providers/)).

Modes match the other agents:

- **`baseline`**: normal run
- **`with_bitloops`**: same wrapper with Bitloops setup enabled (see main doc and `docs/bitloops-init-status-guide.md` if needed)

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
command -v opencode  # launches the OpenCode agent runtime
command -v python3   # launches -m benchkit.swebench.agents.opencode.wrapper
command -v docker
docker info
```

For **`with_bitloops`**, also `command -v bitloops` as in the main doc.

Start Ollama. For **cloud** models used through the local daemon (names ending in `:cloud`, for example `deepseek-v4-flash:cloud`), run **`ollama signin`** once so the daemon can authenticate cloud requests, and make sure the model metadata has been pulled locally (for example `ollama pull deepseek-v4-pro:cloud`) so OpenCode can see it through the local Ollama-compatible endpoint. For local models, sign-in is not required.

## 2. Pick mode

Baseline:

```bash
CONFIG=configs/swebench/opencode_ollama.toml
MODE=baseline
```

With Bitloops:

```bash
CONFIG=configs/swebench/opencode_ollama.toml
MODE=with_bitloops
```

## 3. Dataset and tasks

Identical to [Run Benchmarks §3–4](run-benchmarks.md#3-create-dataset-if-missing): default dataset path in the selected Ollama config, `export-hf` if missing, edit `[run]` / `include_instance_ids` / `include_repos` / `max_instances` in the config you plan to run.

## Ollama-specific TOML fields

Everything in the shared [config reference](run-benchmarks.md#swe-bench-config-reference) applies. Ollama-specific pieces:

### `[model]`

`model.name` is the normal source of truth for model selection in benchmark runs. In practice:

- Use a **local model** name like `qwen3-coder` to run inference on your own hardware.
- Use a **cloud model** name ending in `:cloud` like `deepseek-v4-flash:cloud` to keep the local Ollama API but offload model inference to Ollama Cloud after `ollama signin`.

Both Ollama benchmark configs use `provider = "ollama"` so the OpenCode wrapper knows to inject an Ollama provider overlay. The resolved model id passed to OpenCode is `ollama/<model>`, for example `ollama/qwen2.5-coder:14b` or `ollama/deepseek-v4-pro:cloud`.

### Prompt protocol

Ollama benchmark runs now use the same OpenCode agent loop as the other OpenCode-backed runs. The main behavioral difference is only the provider/model choice.

- The supported prompt protocol is `run.prompt_protocol = "minimal"`.
- If you omit it, benchkit defaults to `minimal`.
- Older `swe` / retrieval-specific prompt settings are no longer used in this flow.

### `[model_map.opencode]`

Keys are canonical names (matching `model.name`); values are the exact model ids sent to OpenCode after resolution, usually in `ollama/<model>` form ([`src/benchkit/swebench/model_mapper.py`](../src/benchkit/swebench/model_mapper.py)).

### Runtime JSON and overrides

- **OpenCode file:** `configs/opencode/opencode.json`
- **Ollama file:** `configs/opencode/ollama.json`
- Standard route: local Ollama daemon at `http://localhost:11434`, exposed to OpenCode as an OpenAI-compatible endpoint at `http://localhost:11434/v1`
- Purpose:
  - `configs/opencode/opencode.json` keeps the normal OpenCode agent defaults
  - `configs/opencode/ollama.json` provides the base URL that the wrapper converts into the OpenCode Ollama provider overlay

Useful **environment overrides** (all optional; these are advanced overrides, not the normal setup):

| Variable | Role |
| --- | --- |
| `OLLAMA_BASE_URL` | Overrides `base_url` (default from JSON or `http://localhost:11434`). |
| `OLLAMA_CONFIG_CONTENT` | Raw JSON object merged over the Ollama runtime JSON before the wrapper builds the OpenCode provider overlay. |
| `OPENCODE_CONFIG_CONTENT` | Extra OpenCode JSON merged before the repo’s `configs/opencode/opencode.json`, then merged again with the Ollama provider overlay. |
| `OPENCODE_MODEL` | Advanced override; normal benchmark runs should set the model in `opencode_ollama.toml` instead. |
| `OPENCODE_TIMEOUT_SECONDS` | Participates in timeout resolution with config and run payload. |

### Local vs cloud models

There is one supported repo flow: the benchmark launches **OpenCode**, and OpenCode talks to your **local Ollama daemon**.

- If `model.name` is a normal local model, inference happens on your machine.
- If `model.name` ends with `:cloud`, inference is offloaded to Ollama Cloud, but the benchmark still talks to the local Ollama-compatible endpoint.
- In the `:cloud` case you usually want `ollama signin` and local model metadata pulled once, not a direct provider API key in benchkit.
- In this repo, `opencode_ollama.toml` is the local default and `opencode_ollama_cloud.toml` is the explicit cloud variant so benchmark runs do not silently switch execution mode.

### Using a `.env` file

The benchmark CLI now auto-loads the first `.env` file it finds from:

- your current working directory, or
- the selected config path and its parent directories

That means a repo-root `.env` works for commands like `plan`, `run`, and `export-hf` without a manual `source .env`. Existing shell exports still take precedence over `.env` values.

Example repo-root `.env` for optional overrides:

```bash
OLLAMA_BASE_URL=http://localhost:11434
OPENCODE_TIMEOUT_SECONDS=1200
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
- The selected Ollama TOML controls **what** runs; `configs/opencode/opencode.json` controls the OpenCode runtime defaults; `configs/opencode/ollama.json` controls the Ollama daemon endpoint that is injected into OpenCode.
- Both `configs/swebench/opencode_ollama.toml` and `configs/swebench/opencode_ollama_cloud.toml` use the OpenCode agent path so Ollama runs are comparable to the other agent runs.
