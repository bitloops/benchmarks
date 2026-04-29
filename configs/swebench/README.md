# SWE-bench Configs

Use these two configs for normal benchmark work:

- `codex.toml`
- `opencode.toml`

Both support the same modes:

```bash
--mode baseline
--mode with_bitloops
```

Keep the top-level configs small. Tune task selection, parallelism, timeouts,
model name, and evaluation settings here. Shared boilerplate comes from the
`codex` and `opencode` config presets in `benchkit.common.config`.

Legacy configs are archived under:

- `archive/legacy-current/` for configs that used to be top-level
- `archive/` for older historical experiments
