# SWE-bench configs

This directory keeps only current, runnable benchmark configs at the top level.
Historical one-off experiments are preserved under `archive/`.

## Active configs

- `rust_canary.toml` - mock-agent smoke test.
- `rust_all_repos_claude_with_bitloops.toml` - canonical multi-repo Claude + Bitloops slice (`datasets/swebench_multilingual.test.rust_all.jsonl`, 43 tasks).
- `rust_opencode.toml` - current OpenCode config with `baseline` and `with_bitloops` modes.
- `rust_opencode_baseline.toml` - legacy one-off OpenCode baseline config.
- `rust_tokio_phase1_claude.toml` - Tokio Phase 1 Claude baseline.
- `rust_tokio_phase1_claude_with_bitloops.toml` - Tokio Phase 1 Claude + Bitloops.
- `rust_tokio_phase1_codex.toml` - Tokio Phase 1 Codex baseline.
- `rust_tokio_phase1_codex_with_bitloops.toml` - Tokio Phase 1 Codex + Bitloops.
- `rust_tokio_phase1_cursor.toml` - Tokio Phase 1 Cursor baseline, kept for `scripts/swebench/phase1_tokio_run.sh`.
- `rust_tokio_phase1_opencode.toml` - Tokio Phase 1 OpenCode baseline, kept for `scripts/swebench/phase1_tokio_run.sh`.

## Archived configs

The files in `archive/` are kept as runnable TOML references for old Ruff,
prefetch, generic agent, and one-off Claude/Coreutils experiments.
