# Benchmark Report Guide

Use the reporting module to generate the existing appendix outputs plus a
canonical run-level summary in one pass.

```bash
./.venv/bin/python -m benchkit.swebench.reports \
  --run-root runs/swebench_multilingual/<date>/<run_id> \
  --output-dir reports/appendix/my_report
```

Repeat `--run-root` to bundle multiple runs into the same output directory.

Generated files include:

- `run_summary.jsonl`
- `run_summary.csv`
- `appendix_minimal_per_task_log.jsonl`
- `appendix_minimal_per_task_log.csv`
- `appendix_minimal_results_table.csv`
- `appendix_minimal_results_table.md`
- `appendix_per_attempt_breakdown.md`
- `appendix_prompt_tool_breakdown.md`
- `appendix_tool_invocation_log.jsonl`
- `appendix_tool_invocation_breakdown.md`

Notes:

- `run_summary.*` is the canonical one-row-per-run report.
- `engineer` comes from run artifacts when present, otherwise falls back to
  `BENCHKIT_ENGINEER`, then `USER`.
- `agent_cli_version` prefers trace metadata when available and otherwise probes
  the agent command recorded in trace metadata at report generation time.
