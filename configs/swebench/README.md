# SWE-bench Configs

Use these configs for normal benchmark work:

- `codex.toml`
- `opencode.toml`
- `opencode_ollama.toml`

Ollama benchmark configs:

- `opencode_ollama.toml` for the OpenCode-via-Ollama route
  This runs the normal OpenCode agent runtime and injects Ollama as a custom provider, matching the
  `ollama launch opencode` architecture. Switch between local and cloud inference by changing
  `[model].name`: use a normal model name for local inference, or a `:cloud` model name for cloud
  inference after `ollama signin` / model pull setup.

Both support the same modes:

```bash
--mode baseline
--mode with_bitloops
```

Keep the top-level configs small. Tune task selection, parallelism, timeouts,
model name, and evaluation settings here. Shared boilerplate comes from the
`codex` and `opencode` config presets in `benchkit.common.config`.

Runtime behavior comes from per-agent JSON:

- `configs/codex/codex.json` for Codex
- `configs/opencode/opencode.json` for OpenCode
- `configs/opencode/ollama.json` for the Ollama daemon base URL used by the OpenCode overlay

Full field-by-field documentation: [docs/run-benchmarks.md](../docs/run-benchmarks.md#swe-bench-config-reference).

## OpenCode models

Switching the LLM (for example from the default DeepSeek deployment to a Qwen deployment) requires **three** places to stay in sync:

1. **`opencode.toml`**
   - Set `[model]` `name` to your canonical label (used in reports and as the lookup key for `model_map`).
   - Under `[model_map.opencode]`, map that label to the **exact model id** the OpenCode CLI expects (often a Fireworks deployment path such as `fireworks-ai/accounts/.../deployments/...`). The subtable must be `opencode` to match `agent.id`. See [`src/benchkit/swebench/model_mapper.py`](../../src/benchkit/swebench/model_mapper.py) and [`scripts/agents/README.md`](../../scripts/agents/README.md).

2. **`configs/opencode/opencode.json`**
   - Set top-level `model` and `small_model` to the same deployment string you use in `model_map`.
   - Add or update the matching entry under `provider.fireworks-ai.models` (key = deployment path after the provider prefix, as in the committed DeepSeek example). Without this, OpenCode may not resolve the model at runtime.

3. **Credentials**
   - Ensure your Fireworks/OpenCode auth can access the new deployment.

For `opencode_ollama.toml`, map the canonical label under `[model_map.opencode]` to the OpenCode model id
format `ollama/<model>`, for example `ollama/deepseek-v4-pro:cloud`.

There is no fixed list of model names in this repo: use the deployment ids from your Fireworks dashboard
(or provider) when substituting Qwen for DeepSeek.

Legacy configs are archived under:

- `archive/legacy-current/` for configs that used to be top-level
- `archive/` for older historical experiments
