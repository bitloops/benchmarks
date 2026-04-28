---
name: baseline-run-row
description: Generate a paste-ready TSV row for the benchmark baseline_runs spreadsheet from appendix output, optionally uploading raw agent JSONL transcripts to Drive. Use when asked to create a baseline_runs, Excel, spreadsheet, or TSV row from reports/appendix output.
---

# Baseline Run Row

Use this skill to generate one TSV row for the `baseline_runs` Excel/Google Sheets tab from a benchmark report path.

## Inputs

- Required: `report_path`, either an appendix report directory or a `run_summary.csv` / `run_summary.jsonl` file.
- Optional: `run_id`, required when the report summary contains multiple run rows.
- Optional: `drive_folder_url`, Google Drive folder URL where raw agent transcript JSONL files should be uploaded.
- Optional: `target`, repeated `(instance_id, drive_folder_url)` pairs when each SWE task has its own spreadsheet/json_logs folder.
- Optional: `attempt`, attempt filter for transcript upload, e.g. `1`, `01`, or `attempt-01`. Repeat for multiple attempts.
- Optional: `instance_id`, benchmark instance filter for transcript upload. Repeat for multiple task instances.
- Optional: `analysis`, free text for `analysis (from AI and or query or script)`.
- Optional: `analysis_file`, a UTF-8 text/Markdown file to use as the analysis cell.
- Optional: `ai_agent_model`, override for `ai_agent_and_model_used_for_analysis`.
- Optional: `developer_comment`, free text for `developer comment on analysis (optional)`.
- Optional: `include_header`, whether to print the target header before the row.

## Procedure

1. Verify the report path exists.
2. If the summary has multiple rows and `run_id` is missing, ask which `run_id` to use.
3. Decide the row target:
   - For the common case of one `instance_id` with many attempts, generate one TSV row for that `instance_id`, aggregating all attempts unless the user supplies `attempt`.
   - If the run contains multiple `instance_id`s and each has its own spreadsheet/json_logs folder, require one target pair per sheet: `(instance_id, drive_folder_url)`.
   - If the user gives multiple Drive folders without clear `instance_id` pairing, ask for the mapping before uploading.
   - If the user explicitly asks for per-attempt rows, generate one TSV snippet per `(instance_id, attempt)`.
4. If the user asked to upload transcripts and a needed `drive_folder_url` is missing, ask for it.
5. If `drive_folder_url` is supplied, upload transcripts first:

```bash
python3 .agents/skills/baseline-run-row/scripts/upload_trace_jsonl_to_drive.py <report_path> --drive-folder-url <drive_folder_url>
```

Add `--run-id`, `--attempt`, and `--instance-id` only when those inputs are supplied. For multiple target pairs, run the helper once per pair with that pair's `--instance-id` and `--drive-folder-url`. When an `instance_id` has multiple attempts and no `attempt` filter, upload all resolved raw transcripts for that instance; the helper prints semicolon-separated Drive URLs.

The upload helper resolves `metadata.raw_stdout_path` from local `trace_jsonl_paths` first, usually under `attempts/attempt-*/agent_raw/`. It falls back to the local trace JSONL only when a raw transcript path is unavailable. The helper prints the uploaded Drive file URL, or semicolon-separated URLs for multiple transcripts.

If upload fails because `gcloud` is missing or lacks Drive scope, ask the developer to run:

```bash
gcloud auth login --enable-gdrive-access --force
```

Then retry the upload.

6. Generate the TSV row:

```bash
python3 .agents/skills/baseline-run-row/scripts/generate_baseline_run_row.py <report_path>
```

7. Add flags only when the user supplied those values:

```bash
--run-id <run_id>
--instance-id <instance_id>
--attempt <attempt>
--analysis "<analysis text>"
--analysis-file <analysis_file>
--ai-agent-model "<agent/model text>"
--developer-comment "<comment text>"
--log-jsonl-link "<uploaded Drive file URL>"
--include-header
```

For multiple target pairs, run the row generator once per pair using that pair's uploaded Drive URL(s), then return separate fenced `tsv` snippets labeled by `instance_id`.

8. Return the script output inside a fenced code block marked `tsv`. Preserve tab separators inside the code block.

## Target Columns

The TSV is emitted in this exact order:

```text
run_id	run_datetime	engineer	agent	model	bitloops_cli_commit_sha	log_jsonl_link	runtime_sec	input_tokens	output_tokens	cache_read_input_tokens	cache_creation_input_tokens	derived_total_input_processed_tokens	derived_total_processed_tokens	result	internal_tool_calls	ai_agent_and_model_used_for_analysis	analysis (from AI and or query or script)	developer comment on analysis (optional)
```

## Notes

- The script reads `run_summary.csv` first, falling back to `run_summary.jsonl`.
- If the report has multiple run rows, use `--run-id <run_id>`.
- If `--instance-id` or `--attempt` is supplied, the row metrics are filtered from `appendix_minimal_per_task_log.csv` / `.jsonl` instead of using run-wide aggregate metrics.
- If transcript upload resolves multiple files, the spreadsheet `log_jsonl_link` cell receives semicolon-separated Drive URLs.
- Empty optional cells are preserved as empty TSV fields so spreadsheet paste alignment stays intact.
- The upload helper uses `GOOGLE_OAUTH_ACCESS_TOKEN` or `gcloud auth print-access-token`. If Google reports insufficient Drive scope, authenticate with Drive access and rerun the helper.
