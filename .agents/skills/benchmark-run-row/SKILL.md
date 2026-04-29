---
name: benchmark-run-row
description: Generate paste-ready TSV rows for benchmark result spreadsheets from appendix output, optionally uploading raw agent JSONL transcripts to Drive. Use when asked to create benchmark, baseline, Bitloops, Excel, spreadsheet, or TSV rows from reports/appendix output.
---

# Benchmark Run Row

Use this skill to generate TSV row(s) for benchmark result Excel/Google Sheets tabs from a benchmark report path. It supports two output schemas: baseline rows and Bitloops rows. Treat `condition=with_bitloops` as a Bitloops run.

## Inputs

- Required: `report_path`, either an appendix report directory or a `run_summary.csv` / `run_summary.jsonl` file next to `appendix_minimal_per_task_log.csv` / `.jsonl`.
- Optional: `run_id`, required when the report summary contains multiple run rows.
- Optional: `drive_folder_url`, Google Drive folder URL where raw agent transcript JSONL files and the appendix report folder should be uploaded.
- Optional: `target`, repeated `(instance_id, drive_folder_url)` pairs when each SWE task has its own spreadsheet/json_logs folder.
- Optional: `attempt`, attempt filter for transcript upload and row generation, e.g. `1`, `01`, or `attempt-01`. Repeat for multiple attempts. If omitted, generate one TSV row per attempt found in the per-task log.
- Optional: `instance_id`, benchmark instance filter for transcript upload. Repeat for multiple task instances.
- Optional: `analysis`, free text for `analysis (from AI and or query or script)`.
- Optional: `analysis_file`, a UTF-8 text/Markdown file to use as the analysis cell.
- Optional: `ai_agent_model`, override for `ai_agent_and_model_used_for_analysis` on baseline rows.
- Optional: `developer_comment`, free text for `developer comment on analysis (optional)`.
- Optional: `next_action`, free text for the Bitloops `next_action` cell.
- Optional: `run_id_report_folder`, Google Drive folder URL for the uploaded appendix report folder.
- Optional: `include_header`, whether to print the target header before the row.

## Procedure

1. Verify the report path exists.
2. If the summary has multiple rows and `run_id` is missing, ask which `run_id` to use.
3. Decide the row target:
   - Default to one TSV row per `(instance_id, attempt)` found in `appendix_minimal_per_task_log.csv` / `.jsonl`.
   - For the common case of one `instance_id` with many attempts, generate multiple TSV rows, one per attempt. Do not aggregate attempts unless the user explicitly asks for an aggregate row.
   - If the user supplies one or more `attempt` filters, generate rows only for those attempts, still one row per attempt.
   - If the run contains multiple `instance_id`s and each has its own spreadsheet/json_logs folder, require one target pair per sheet: `(instance_id, drive_folder_url)`.
   - If the user gives multiple Drive folders without clear `instance_id` pairing, ask for the mapping before uploading.
   - If the user explicitly asks for aggregate rows, generate one TSV snippet per requested aggregate scope, and say that the metrics are aggregated.
4. If the user asked to upload transcripts or the appendix report folder and a needed `drive_folder_url` is missing, ask for it.
5. If `drive_folder_url` is supplied, upload the appendix report folder once:

```bash
python3 .agents/skills/benchmark-run-row/scripts/upload_report_folder_to_drive.py <report_path> --drive-folder-url <drive_folder_url>
```

Add `--run-id` when that filter applies. The helper creates one child Drive folder named by `run_id`, uploads only the appendix report files (`run_summary.*` and `appendix_*`), and prints the child folder URL. Use that URL as `--run-id-report-folder` for every TSV row from the run.

6. If `drive_folder_url` is supplied, upload transcripts:

```bash
python3 .agents/skills/benchmark-run-row/scripts/upload_trace_jsonl_to_drive.py <report_path> --drive-folder-url <drive_folder_url>
```

Add `--run-id`, `--attempt`, and `--instance-id` only when those filters apply. For per-attempt rows, run the helper once per `(instance_id, attempt)` with that row's `--attempt` so the spreadsheet row receives the matching single Drive URL. For multiple target pairs, run the helper once per pair and attempt with that pair's `--instance-id` and `--drive-folder-url`.

Only upload multiple transcripts in one helper call when the user explicitly asks for an aggregate row. In that case, the helper prints semicolon-separated Drive URLs for the aggregate row's `log_jsonl_link` cell.

The upload helper resolves `metadata.raw_stdout_path` from local `trace_jsonl_paths` first, usually under `attempts/attempt-*/agent_raw/`. It falls back to the local trace JSONL only when a raw transcript path is unavailable. The helper prints the uploaded Drive file URL, or semicolon-separated URLs for multiple transcripts.

If upload fails because `gcloud` is missing or lacks Drive scope, ask the developer to run:

```bash
gcloud auth login --enable-gdrive-access --force
```

Then retry the upload.

7. Generate the TSV row(s). The generator always reads `appendix_minimal_per_task_log.csv` / `.jsonl` for metrics and uses `run_summary.csv` / `.jsonl` only for run metadata and trace path context:

```bash
python3 .agents/skills/benchmark-run-row/scripts/generate_benchmark_run_row.py <report_path>
```

8. Add flags only when the target row needs those values:

```bash
--run-id <run_id>
--instance-id <instance_id>
--attempt <attempt>
--analysis "<analysis text>"
--analysis-file <analysis_file>
--ai-agent-model "<agent/model text>"
--developer-comment "<comment text>"
--next-action "<next action text>"
--log-jsonl-link "<uploaded Drive file URL>"
--run-id-report-folder "<uploaded Drive folder URL>"
--include-header
```

Because the generator aggregates all matched per-task rows, pass `--attempt <attempt>` for every default per-attempt row. For multiple target pairs, run the row generator once per `(instance_id, attempt)` using that attempt's uploaded Drive URL and the shared report folder URL, then return one fenced `tsv` snippet per `instance_id` or one combined snippet if all rows go to the same sheet.

For Bitloops rows, the generator counts actual `bitloops devql ...` Bash commands from `appendix_tool_invocation_log.jsonl` (falling back to embedded per-task tool invocations when needed). It writes that count to `devql_calls_num` and subtracts it from `internal_tool_calls`, so `internal_tool_calls` is comparable to baseline tool calls.

9. Return the script output inside a fenced code block marked `tsv`. Preserve tab separators inside the code block.

## Target Columns

Baseline rows use this order:

```text
run_id	run_datetime	engineer	agent	model	bitloops_cli_commit_sha	log_jsonl_link	runtime_sec	input_tokens	output_tokens	cache_read_input_tokens	cache_creation_input_tokens	derived_total_input_processed_tokens	derived_total_processed_tokens	result	internal_tool_calls	ai_agent_and_model_used_for_analysis	analysis (from AI and or query or script)	developer comment on analysis (optional)	run_id_report_folder
```

Bitloops rows use this order:

```text
run_id	run_datetime	engineer	agent	model	bitloops_cli_commit_sha	log_jsonl_link	runtime_sec	input_tokens	output_tokens	cache_read_input_tokens	cache_creation_input_tokens	derived_total_input_processed_tokens	derived_total_processed_tokens	result	devql_calls_num	internal_tool_calls	analysis (from AI and or query or script)	developer comment on analysis	next_action	run_id_report_folder
```

## Notes

- The script reads `run_summary.csv` first, falling back to `run_summary.jsonl`, for run metadata only.
- Metrics always come from `appendix_minimal_per_task_log.csv` / `.jsonl`: runtime, tokens, cache tokens, derived totals, result, and internal tool calls.
- The output schema is selected from the run summary `condition`: `baseline` keeps the baseline schema; `bitloops` and `with_bitloops` use the Bitloops schema.
- If the report has multiple run rows, use `--run-id <run_id>`.
- If `--instance-id` or `--attempt` is supplied, filter the per-task rows before computing metrics. Without filters, the generator aggregates all per-task rows in the selected run, so omit `--attempt` only for an explicitly requested aggregate row.
- If transcript upload resolves multiple files for an explicitly requested aggregate row, the spreadsheet `log_jsonl_link` cell receives semicolon-separated Drive URLs.
- If `--run-id-report-folder` is omitted, the generator uses `run_id_report_folder` from `run_summary.csv` / `.jsonl` when present, otherwise the cell is empty.
- Empty optional cells are preserved as empty TSV fields so spreadsheet paste alignment stays intact.
- The upload helper uses `GOOGLE_OAUTH_ACCESS_TOKEN` or `gcloud auth print-access-token`. If Google reports insufficient Drive scope, authenticate with Drive access and rerun the helper.
