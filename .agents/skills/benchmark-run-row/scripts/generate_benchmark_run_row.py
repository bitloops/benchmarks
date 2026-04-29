#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Any


BASELINE_TARGET_COLUMNS = [
    "run_id",
    "run_datetime",
    "engineer",
    "agent",
    "model",
    "bitloops_cli_commit_sha",
    "log_jsonl_link",
    "runtime_sec",
    "input_tokens",
    "output_tokens",
    "cache_read_input_tokens",
    "cache_creation_input_tokens",
    "derived_total_input_processed_tokens",
    "derived_total_processed_tokens",
    "result",
    "internal_tool_calls",
    "ai_agent_and_model_used_for_analysis",
    "analysis (from AI and or query or script)",
    "developer comment on analysis (optional)",
]

BITLOOPS_TARGET_COLUMNS = [
    "run_id",
    "run_datetime",
    "engineer",
    "agent",
    "model",
    "bitloops_cli_commit_sha",
    "log_jsonl_link",
    "runtime_sec",
    "input_tokens",
    "output_tokens",
    "cache_read_input_tokens",
    "cache_creation_input_tokens",
    "derived_total_input_processed_tokens",
    "derived_total_processed_tokens",
    "result",
    "devql_calls_num",
    "internal_tool_calls",
    "analysis (from AI and or query or script)",
    "developer comment on analysis",
    "next_action",
]

TARGET_COLUMNS = BASELINE_TARGET_COLUMNS


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate a benchmark results TSV row from a benchmark report directory."
    )
    parser.add_argument(
        "report_path",
        type=Path,
        help="Appendix report directory, run_summary.csv, or run_summary.jsonl.",
    )
    parser.add_argument(
        "--run-id",
        help="Select a run_id when the report summary contains multiple rows.",
    )
    parser.add_argument(
        "--instance-id",
        dest="instance_ids",
        action="append",
        help="Filter row metrics to one benchmark instance_id. Repeat for multiple instances.",
    )
    parser.add_argument(
        "--attempt",
        dest="attempts",
        action="append",
        help="Filter row metrics to one attempt, e.g. 1, 01, or attempt-01. Repeat for multiple attempts.",
    )
    parser.add_argument(
        "--analysis",
        default="",
        help="Text for the analysis column.",
    )
    parser.add_argument(
        "--analysis-file",
        type=Path,
        help="UTF-8 text/Markdown file to use for the analysis column.",
    )
    parser.add_argument(
        "--ai-agent-model",
        default=None,
        help="Override ai_agent_and_model_used_for_analysis.",
    )
    parser.add_argument(
        "--developer-comment",
        default="",
        help="Text for the developer comment column.",
    )
    parser.add_argument(
        "--next-action",
        default="",
        help="Text for the Bitloops next_action column.",
    )
    parser.add_argument(
        "--log-jsonl-link",
        default=None,
        help="Override the log_jsonl_link column, for example with an uploaded Drive URL.",
    )
    parser.add_argument(
        "--include-header",
        action="store_true",
        help="Print the target column header before the TSV row.",
    )
    args = parser.parse_args()

    try:
        source = resolve_summary_source(args.report_path)
        rows = load_summary_rows(source)
        row = select_row(rows, args.run_id)
        row = apply_per_task_metrics(
            row=row,
            summary_source=source,
            instance_ids=args.instance_ids,
            attempts=args.attempts,
        )
        output_row = build_tsv_row(
            row,
            analysis=read_analysis(args.analysis, args.analysis_file),
            ai_agent_model=args.ai_agent_model,
            developer_comment=args.developer_comment,
            next_action=args.next_action,
            log_jsonl_link=args.log_jsonl_link,
        )
        target_columns = target_columns_for(row)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.include_header:
        print("\t".join(target_columns))
    print("\t".join(output_row))
    return 0


def resolve_summary_source(path: Path) -> Path:
    path = path.expanduser()
    if path.is_dir():
        csv_path = path / "run_summary.csv"
        if csv_path.exists():
            return csv_path
        jsonl_path = path / "run_summary.jsonl"
        if jsonl_path.exists():
            return jsonl_path
        raise ValueError(f"no run_summary.csv or run_summary.jsonl found in {path}")
    if path.is_file():
        if path.name not in {"run_summary.csv", "run_summary.jsonl"}:
            raise ValueError(f"expected run_summary.csv or run_summary.jsonl, got {path.name}")
        return path
    raise ValueError(f"report path does not exist: {path}")


def load_summary_rows(path: Path) -> list[dict[str, Any]]:
    if path.suffix == ".csv":
        with path.open("r", encoding="utf-8", newline="") as handle:
            return [dict(row) for row in csv.DictReader(handle)]
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            cleaned = line.strip()
            if cleaned:
                rows.append(json.loads(cleaned))
    return rows


def select_row(rows: list[dict[str, Any]], run_id: str | None) -> dict[str, Any]:
    if not rows:
        raise ValueError("run summary is empty")
    if run_id:
        matches = [row for row in rows if clean(row.get("run_id")) == run_id]
        if not matches:
            raise ValueError(f"run_id not found in report: {run_id}")
        if len(matches) > 1:
            raise ValueError(f"multiple rows found for run_id: {run_id}")
        return matches[0]
    if len(rows) > 1:
        ids = ", ".join(clean(row.get("run_id")) or "<blank>" for row in rows)
        raise ValueError(f"multiple rows found; pass --run-id. Available run_ids: {ids}")
    return rows[0]


def apply_per_task_metrics(
    *,
    row: dict[str, Any],
    summary_source: Path,
    instance_ids: list[str] | None,
    attempts: list[str] | None,
) -> dict[str, Any]:
    wanted_instance_ids = normalize_instance_ids(instance_ids)
    wanted_attempts = normalize_attempts(attempts)
    per_task_source = resolve_per_task_source(summary_source.parent)
    per_task_rows = load_summary_rows(per_task_source)
    selected_rows = [
        task_row
        for task_row in per_task_rows
        if matches_instance(task_row, wanted_instance_ids)
        and matches_attempt(task_row, wanted_attempts)
    ]
    if not selected_rows:
        raise ValueError(
            "no appendix_minimal_per_task_log rows matched "
            f"instance_id={sorted(wanted_instance_ids) if wanted_instance_ids else '<any>'} "
            f"attempt={sorted(wanted_attempts) if wanted_attempts else '<any>'}"
        )

    filtered = dict(row)
    filtered["runtime_total_sec"] = format_number(sum_numeric(selected_rows, "runtime_sec"))
    filtered["input_tokens_total"] = format_number(sum_numeric(selected_rows, "token_input"))
    filtered["output_tokens_total"] = format_number(sum_numeric(selected_rows, "token_output"))
    filtered["cache_read_input_tokens_total"] = format_number(
        sum_numeric(selected_rows, "cache_read_input_tokens")
    )
    filtered["cache_creation_input_tokens_total"] = format_number(
        sum_numeric(selected_rows, "cache_creation_input_tokens")
    )
    derived_input = sum(
        numeric_value(filtered.get(key)) or 0
        for key in (
            "input_tokens_total",
            "cache_read_input_tokens_total",
            "cache_creation_input_tokens_total",
        )
    )
    output_tokens = numeric_value(filtered.get("output_tokens_total")) or 0
    filtered["derived_total_input_processed_tokens"] = format_number(derived_input)
    filtered["derived_total_processed_tokens"] = format_number(derived_input + output_tokens)
    filtered["result"] = derive_result(selected_rows)
    devql_calls = count_devql_calls(summary_source.parent, selected_rows)
    tool_calls = sum_numeric(selected_rows, "tool_calls")
    filtered["devql_calls_num"] = format_number(devql_calls)
    if is_bitloops_condition(filtered):
        tool_calls = max(0, tool_calls - devql_calls)
    filtered["internal_tool_calls"] = format_number(tool_calls)
    return filtered


def resolve_per_task_source(report_dir: Path) -> Path:
    csv_path = report_dir / "appendix_minimal_per_task_log.csv"
    if csv_path.exists():
        return csv_path
    jsonl_path = report_dir / "appendix_minimal_per_task_log.jsonl"
    if jsonl_path.exists():
        return jsonl_path
    raise ValueError(f"no appendix_minimal_per_task_log.csv or .jsonl found in {report_dir}")


def normalize_instance_ids(values: list[str] | None) -> set[str]:
    return {str(value or "").strip() for value in values or [] if str(value or "").strip()}


def normalize_attempts(values: list[str] | None) -> set[str]:
    output: set[str] = set()
    for value in values or []:
        text = str(value or "").strip()
        if not text:
            continue
        if text.startswith("attempt-"):
            text = text.removeprefix("attempt-")
        output.add(str(int(text)) if text.isdigit() else text)
    return output


def matches_instance(row: dict[str, Any], wanted_instance_ids: set[str]) -> bool:
    if not wanted_instance_ids:
        return True
    return clean(row.get("task_id")) in wanted_instance_ids


def matches_attempt(row: dict[str, Any], wanted_attempts: set[str]) -> bool:
    if not wanted_attempts:
        return True
    return normalize_attempt_value(row.get("attempt")) in wanted_attempts


def normalize_attempt_value(value: Any) -> str:
    attempt = clean(value)
    if attempt.startswith("attempt-"):
        attempt = attempt.removeprefix("attempt-")
    if attempt.isdigit():
        attempt = str(int(attempt))
    return attempt


def sum_numeric(rows: list[dict[str, Any]], key: str) -> int | float:
    total: int | float = 0
    for row in rows:
        value = numeric_value(row.get(key))
        if value is not None:
            total += value
    return total


def numeric_value(value: Any) -> int | float | None:
    cleaned = clean(value)
    if not cleaned:
        return None
    try:
        number = float(cleaned)
    except ValueError:
        return None
    if number.is_integer():
        return int(number)
    return number


def format_number(value: int | float) -> str:
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def derive_result(rows: list[dict[str, Any]]) -> str:
    statuses = [clean(row.get("status")).lower() for row in rows]
    if any(status == "solved" for status in statuses):
        return "solved"
    if statuses and all(status == "invalid" for status in statuses):
        return "invalid"
    if any(status == "unsolved" for status in statuses):
        return "unsolved"
    return statuses[0] if statuses else ""


def count_devql_calls(report_dir: Path, selected_rows: list[dict[str, Any]]) -> int:
    selected = {
        (clean(row.get("task_id")), normalize_attempt_value(row.get("attempt")))
        for row in selected_rows
    }
    tool_log = report_dir / "appendix_tool_invocation_log.jsonl"
    if tool_log.exists():
        total = 0
        with tool_log.open("r", encoding="utf-8") as handle:
            for line in handle:
                cleaned = line.strip()
                if not cleaned:
                    continue
                record = json.loads(cleaned)
                key = (clean(record.get("task_id")), normalize_attempt_value(record.get("attempt")))
                if key in selected and is_devql_invocation(record):
                    total += 1
        return total

    total = 0
    for row in selected_rows:
        invocations = row.get("tool_invocations_curated") or []
        if isinstance(invocations, str):
            try:
                invocations = json.loads(invocations)
            except json.JSONDecodeError:
                invocations = []
        if isinstance(invocations, list):
            total += sum(1 for invocation in invocations if is_devql_invocation(invocation))
    return total


def is_devql_invocation(invocation: dict[str, Any]) -> bool:
    return "bitloops devql" in invocation_command(invocation)


def invocation_command(invocation: dict[str, Any]) -> str:
    candidates = [
        invocation,
        invocation.get("curated") if isinstance(invocation.get("curated"), dict) else {},
        invocation.get("input") if isinstance(invocation.get("input"), dict) else {},
    ]
    raw = invocation.get("raw")
    if isinstance(raw, dict):
        candidates.append(raw.get("input") if isinstance(raw.get("input"), dict) else {})
        raw_event = raw.get("raw_event")
        if isinstance(raw_event, dict):
            state = raw_event.get("state")
            if isinstance(state, dict) and isinstance(state.get("input"), dict):
                candidates.append(state["input"])
    for candidate in candidates:
        command = clean(candidate.get("command"))
        if command:
            return command
    return ""


def is_bitloops_condition(row: dict[str, Any]) -> bool:
    return clean(row.get("condition")).lower() in {"bitloops", "with_bitloops"}


def target_columns_for(row: dict[str, Any]) -> list[str]:
    if is_bitloops_condition(row):
        return BITLOOPS_TARGET_COLUMNS
    return BASELINE_TARGET_COLUMNS


def build_tsv_row(
    row: dict[str, Any],
    *,
    analysis: str,
    ai_agent_model: str | None,
    developer_comment: str,
    next_action: str,
    log_jsonl_link: str | None,
) -> list[str]:
    target_columns = target_columns_for(row)
    values = {
        "run_id": clean(row.get("run_id")),
        "run_datetime": clean(row.get("run_datetime")),
        "engineer": clean(row.get("engineer")),
        "agent": clean(row.get("agent")),
        "model": clean(row.get("model_canonical")) or clean(row.get("model_resolved")),
        "bitloops_cli_commit_sha": clean(row.get("bitloops_cli_commit_sha")),
        "log_jsonl_link": clean(log_jsonl_link) or clean(row.get("log_jsonl_link")),
        "runtime_sec": clean(row.get("runtime_total_sec")) or clean(row.get("runtime_mean_sec")),
        "input_tokens": clean(row.get("input_tokens_total")),
        "output_tokens": clean(row.get("output_tokens_total")),
        "cache_read_input_tokens": clean(row.get("cache_read_input_tokens_total")),
        "cache_creation_input_tokens": clean(row.get("cache_creation_input_tokens_total")),
        "derived_total_input_processed_tokens": clean(
            row.get("derived_total_input_processed_tokens")
        ),
        "derived_total_processed_tokens": clean(row.get("derived_total_processed_tokens")),
        "result": clean(row.get("result")),
        "devql_calls_num": clean(row.get("devql_calls_num")),
        "internal_tool_calls": clean(row.get("internal_tool_calls")),
        "ai_agent_and_model_used_for_analysis": clean(ai_agent_model)
        or clean(row.get("ai_agent_and_model_used_for_analysis")),
        "analysis (from AI and or query or script)": sanitize_cell(analysis),
        "developer comment on analysis (optional)": sanitize_cell(developer_comment),
        "developer comment on analysis": sanitize_cell(developer_comment),
        "next_action": sanitize_cell(next_action),
    }
    return [values[column] or "" for column in target_columns]


def read_analysis(analysis: str, analysis_file: Path | None) -> str:
    if analysis and analysis_file:
        raise ValueError("pass either --analysis or --analysis-file, not both")
    if analysis_file is None:
        return analysis
    path = analysis_file.expanduser()
    if not path.is_file():
        raise ValueError(f"analysis file does not exist: {path}")
    return path.read_text(encoding="utf-8").strip()


def clean(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        return ";".join(clean(item) for item in value if clean(item))
    return str(value).strip()


def sanitize_cell(value: str) -> str:
    return " ".join(value.replace("\t", " ").splitlines()).strip()


if __name__ == "__main__":
    raise SystemExit(main())
