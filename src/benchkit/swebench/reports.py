from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from statistics import mean, median
from typing import Any
import argparse
import csv
import os
import shlex
import subprocess

from benchkit.common.io import read_json, read_jsonl, write_jsonl
from benchkit.swebench.appendix import (
    AppendixOutputs,
    _build_per_task_rows,
    generate_appendix_files,
)


RUN_SUMMARY_FIELDS = [
    "run_id",
    "run_datetime",
    "started_at_utc",
    "finished_at_utc",
    "engineer",
    "benchmark",
    "dataset_path",
    "split",
    "language",
    "condition",
    "run_root",
    "result",
    "bitloops_cli_commit_sha",
    "log_jsonl_link",
    "internal_tool_calls",
    "ai_agent_and_model_used_for_analysis",
    "agent",
    "agent_cli_version",
    "model_canonical",
    "model_resolved",
    "primary_session_id",
    "session_id_count",
    "session_ids",
    "bitloops_enabled",
    "bitloops_sandbox_mode",
    "workspace_isolation_mode",
    "bitloops_sync",
    "bitloops_ingest",
    "bitloops_embeddings_runtime",
    "bitloops_no_embeddings",
    "bitloops_summary_mode",
    "bitloops_embedding_mode",
    "evaluation_enabled",
    "attempts",
    "max_workers",
    "total_instances",
    "total_agent_calls",
    "successful_agent_calls",
    "failed_agent_calls",
    "task_attempt_rows",
    "unique_tasks",
    "solved_task_attempts",
    "unsolved_task_attempts",
    "invalid_task_attempts",
    "attempt_solve_rate",
    "tasks_solved_at_least_once",
    "task_solve_rate_at_least_once",
    "runtime_total_sec",
    "runtime_mean_sec",
    "runtime_median_sec",
    "input_tokens_total",
    "output_tokens_total",
    "cache_creation_input_tokens_total",
    "cache_read_input_tokens_total",
    "derived_total_input_processed_tokens",
    "derived_total_processed_tokens",
    "estimated_cost_total",
    "tool_calls_total",
    "tool_calls_mean",
    "files_edited_total",
    "patch_size_total",
    "trace_jsonl_paths",
    "prediction_jsonl_paths",
    "evaluation_report_paths",
]


@dataclass(slots=True)
class RunSummaryOutputs:
    run_summary_jsonl: Path
    run_summary_csv: Path


@dataclass(slots=True)
class ReportOutputs:
    appendix: AppendixOutputs
    run_summary: RunSummaryOutputs


def generate_run_summary_files(run_roots: list[Path], output_dir: Path) -> RunSummaryOutputs:
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    rows = [
        _build_run_summary_row(run_root.resolve())
        for run_root in run_roots
        if (run_root / "run_manifest.json").exists()
    ]

    run_summary_jsonl = output_dir / "run_summary.jsonl"
    run_summary_csv = output_dir / "run_summary.csv"

    write_jsonl(run_summary_jsonl, rows)
    _write_csv(run_summary_csv, RUN_SUMMARY_FIELDS, rows)

    return RunSummaryOutputs(
        run_summary_jsonl=run_summary_jsonl,
        run_summary_csv=run_summary_csv,
    )


def generate_report_files(run_roots: list[Path], output_dir: Path) -> ReportOutputs:
    appendix_outputs = generate_appendix_files(run_roots=run_roots, output_dir=output_dir)
    run_summary_outputs = generate_run_summary_files(run_roots=run_roots, output_dir=output_dir)
    return ReportOutputs(
        appendix=appendix_outputs,
        run_summary=run_summary_outputs,
    )


def _build_run_summary_row(run_root: Path) -> dict[str, Any]:
    manifest = read_json(run_root / "run_manifest.json")
    summary_path = run_root / "summary.json"
    summary = read_json(summary_path) if summary_path.exists() else {}
    per_task_rows = _build_per_task_rows([run_root])
    metadata_rows = _load_trace_metadata_rows(run_root)
    trace_jsonl_paths = _attempt_artifact_paths(run_root, "trace.jsonl")
    prediction_jsonl_paths = _attempt_artifact_paths(run_root, "predictions.jsonl")

    run_id = str(manifest.get("run_id") or summary.get("run_id") or run_root.name)
    unique_task_ids = sorted(
        {
            str(row.get("task_id"))
            for row in per_task_rows
            if str(row.get("task_id") or "").strip()
        }
    )
    solved_task_ids = {
        str(row.get("task_id"))
        for row in per_task_rows
        if row.get("status") == "solved" and str(row.get("task_id") or "").strip()
    }
    solved_task_attempts = sum(1 for row in per_task_rows if row.get("status") == "solved")
    unsolved_task_attempts = sum(1 for row in per_task_rows if row.get("status") == "unsolved")
    invalid_task_attempts = sum(1 for row in per_task_rows if row.get("status") == "invalid")
    task_attempt_rows = len(per_task_rows)
    tasks_solved_at_least_once = len(solved_task_ids)

    trace_statuses = [str(item.get("status") or "").strip().lower() for item in metadata_rows]
    successful_agent_calls = _coalesce_int(
        summary.get("successful_agent_calls"),
        sum(1 for status in trace_statuses if status == "ok"),
    )
    failed_agent_calls = _coalesce_int(
        summary.get("failed_agent_calls"),
        sum(1 for status in trace_statuses if status and status != "ok"),
    )
    total_agent_calls = _coalesce_int(
        summary.get("total_agent_calls"),
        task_attempt_rows or len(metadata_rows),
    )

    input_tokens_total = _sum_int(per_task_rows, "token_input")
    output_tokens_total = _sum_int(per_task_rows, "token_output")
    cache_creation_input_tokens_total = _sum_int(per_task_rows, "cache_creation_input_tokens")
    cache_read_input_tokens_total = _sum_int(per_task_rows, "cache_read_input_tokens")
    derived_total_input_processed_tokens = _sum_present(
        input_tokens_total,
        cache_creation_input_tokens_total,
        cache_read_input_tokens_total,
    )
    derived_total_processed_tokens = _sum_present(
        derived_total_input_processed_tokens,
        output_tokens_total,
    )

    session_ids = _extract_session_ids(metadata_rows)
    primary_session_id = session_ids[0] if session_ids else None

    started_at_utc = _first_clean_text(
        summary.get("started_at_utc"),
        manifest.get("started_at_utc"),
    )
    finished_at_utc = _first_clean_text(summary.get("finished_at_utc"))
    run_datetime = started_at_utc

    model_manifest = manifest.get("model") if isinstance(manifest.get("model"), dict) else {}
    model_resolution = (
        summary.get("model_resolution") if isinstance(summary.get("model_resolution"), dict) else {}
    )

    evaluation = summary.get("evaluation") if isinstance(summary.get("evaluation"), dict) else {}
    evaluation_attempts = (
        evaluation.get("attempts") if isinstance(evaluation.get("attempts"), list) else []
    )
    total_instances = _coalesce_int(
        summary.get("total_instances"),
        manifest.get("total_instances"),
        len(unique_task_ids),
    )
    tool_calls_total = _sum_int(per_task_rows, "tool_calls")

    return {
        "run_id": run_id,
        "run_datetime": run_datetime,
        "started_at_utc": started_at_utc,
        "finished_at_utc": finished_at_utc,
        "engineer": _resolve_engineer(manifest, summary),
        "benchmark": _first_clean_text(manifest.get("benchmark"), summary.get("benchmark")),
        "dataset_path": _first_clean_text(
            manifest.get("dataset_path"),
            summary.get("dataset_path"),
        ),
        "split": _first_clean_text(manifest.get("split"), summary.get("split")),
        "language": _first_clean_text(
            summary.get("language"),
            manifest.get("language"),
            _first_row_value(per_task_rows, "language"),
        ),
        "condition": _first_clean_text(
            manifest.get("condition"),
            summary.get("condition"),
            _first_row_value(per_task_rows, "condition"),
        ),
        "run_root": _first_clean_text(summary.get("run_root")) or _display_path(run_root),
        "result": _derive_run_result(
            total_instances=total_instances,
            tasks_solved_at_least_once=tasks_solved_at_least_once,
            solved_task_attempts=solved_task_attempts,
            task_attempt_rows=task_attempt_rows,
            invalid_task_attempts=invalid_task_attempts,
        ),
        "bitloops_cli_commit_sha": _first_clean_text(
            summary.get("bitloops_cli_commit_sha"),
            manifest.get("bitloops_cli_commit_sha"),
            _first_metadata_value(metadata_rows, "bitloops_cli_commit_sha"),
            _first_metadata_value(metadata_rows, "bitloops_commit_sha"),
            _first_metadata_value(metadata_rows, "commit_sha"),
        ),
        "log_jsonl_link": _compat_artifact_link(trace_jsonl_paths),
        "internal_tool_calls": _coalesce_int(
            summary.get("internal_tool_calls"),
            tool_calls_total,
        ),
        "ai_agent_and_model_used_for_analysis": _resolve_analysis_agent_model(
            manifest=manifest,
            summary=summary,
            metadata_rows=metadata_rows,
        ),
        "agent": _first_clean_text(
            _nested_value(manifest, "agent", "id"),
            _first_row_value(per_task_rows, "agent"),
        ),
        "agent_cli_version": _resolve_agent_cli_version(metadata_rows),
        "model_canonical": _first_clean_text(
            model_manifest.get("canonical_name"),
            model_resolution.get("canonical_name"),
        ),
        "model_resolved": _first_clean_text(
            model_manifest.get("resolved_name"),
            model_resolution.get("resolved_name"),
            _first_row_value(per_task_rows, "model_version"),
        ),
        "primary_session_id": primary_session_id,
        "session_id_count": len(session_ids),
        "session_ids": session_ids,
        "bitloops_enabled": _coalesce_bool(
            summary.get("bitloops_enabled"),
            manifest.get("bitloops_enabled"),
        ),
        "bitloops_sandbox_mode": _first_clean_text(
            summary.get("bitloops_sandbox_mode"),
            manifest.get("bitloops_sandbox_mode"),
        ),
        "workspace_isolation_mode": _first_clean_text(
            summary.get("workspace_isolation_mode"),
            _nested_value(manifest, "workspace", "isolation_mode"),
        ),
        "bitloops_sync": _first_bool(metadata_rows, "bitloops_sync"),
        "bitloops_ingest": _first_bool(metadata_rows, "bitloops_ingest"),
        "bitloops_embeddings_runtime": _first_clean_text(
            _first_metadata_value(metadata_rows, "bitloops_embeddings_runtime")
        ),
        "bitloops_no_embeddings": _first_bool(metadata_rows, "bitloops_no_embeddings"),
        "bitloops_summary_mode": _first_clean_text(
            _first_metadata_value(metadata_rows, "bitloops_summary_mode")
        ),
        "bitloops_embedding_mode": _first_clean_text(
            _first_metadata_value(metadata_rows, "bitloops_embedding_mode")
        ),
        "evaluation_enabled": _coalesce_bool(
            evaluation.get("enabled"),
            _nested_value(manifest, "evaluation", "enabled"),
        ),
        "attempts": _coalesce_int(summary.get("attempts"), manifest.get("attempts")),
        "max_workers": _coalesce_int(summary.get("max_workers"), manifest.get("max_workers")),
        "total_instances": total_instances,
        "total_agent_calls": total_agent_calls,
        "successful_agent_calls": successful_agent_calls,
        "failed_agent_calls": failed_agent_calls,
        "task_attempt_rows": task_attempt_rows,
        "unique_tasks": len(unique_task_ids),
        "solved_task_attempts": solved_task_attempts,
        "unsolved_task_attempts": unsolved_task_attempts,
        "invalid_task_attempts": invalid_task_attempts,
        "attempt_solve_rate": (
            solved_task_attempts / task_attempt_rows if task_attempt_rows else None
        ),
        "tasks_solved_at_least_once": tasks_solved_at_least_once,
        "task_solve_rate_at_least_once": (
            tasks_solved_at_least_once / len(unique_task_ids) if unique_task_ids else None
        ),
        "runtime_total_sec": _sum_float(per_task_rows, "runtime_sec"),
        "runtime_mean_sec": _mean_float(per_task_rows, "runtime_sec"),
        "runtime_median_sec": _median_float(per_task_rows, "runtime_sec"),
        "input_tokens_total": input_tokens_total,
        "output_tokens_total": output_tokens_total,
        "cache_creation_input_tokens_total": cache_creation_input_tokens_total,
        "cache_read_input_tokens_total": cache_read_input_tokens_total,
        "derived_total_input_processed_tokens": derived_total_input_processed_tokens,
        "derived_total_processed_tokens": derived_total_processed_tokens,
        "estimated_cost_total": _sum_float(per_task_rows, "estimated_cost"),
        "tool_calls_total": tool_calls_total,
        "tool_calls_mean": _mean_float(per_task_rows, "tool_calls"),
        "files_edited_total": _sum_int(per_task_rows, "files_edited"),
        "patch_size_total": _sum_int(per_task_rows, "patch_size"),
        "trace_jsonl_paths": trace_jsonl_paths,
        "prediction_jsonl_paths": prediction_jsonl_paths,
        "evaluation_report_paths": _evaluation_report_paths(run_root, evaluation_attempts),
    }


def _resolve_agent_cli_version(metadata_rows: list[dict[str, Any]]) -> str | None:
    explicit = [
        version
        for version in (
            _clean_text(item.get("agent_cli_version"))
            for item in metadata_rows
            if isinstance(item, dict)
        )
        if version
    ]
    if explicit:
        return ";".join(sorted(dict.fromkeys(explicit)))

    for item in metadata_rows:
        if not isinstance(item, dict):
            continue
        command = item.get("command")
        if not isinstance(command, list):
            continue
        version = _probe_command_version(command)
        if version:
            return version
    return None


def _probe_command_version(command: list[str]) -> str | None:
    if not command:
        return None
    binary = str(command[0] or "").strip()
    if not binary:
        return None

    for version_command in ([binary, "--version"], [binary, "version"]):
        try:
            completed = subprocess.run(
                version_command,
                text=True,
                encoding="utf-8",
                errors="replace",
                capture_output=True,
                timeout=10,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            continue
        if completed.returncode != 0:
            continue
        line = _first_nonempty_line(completed.stdout, completed.stderr)
        if line:
            return _normalize_version_label(binary, line)
    return None


def _normalize_version_label(binary: str, text: str) -> str:
    cleaned = " ".join(text.strip().split())
    if not cleaned:
        return cleaned
    if cleaned[0].isdigit():
        return f"{Path(binary).name} {cleaned}"
    return cleaned


def _first_nonempty_line(*chunks: str) -> str | None:
    for chunk in chunks:
        for line in chunk.splitlines():
            cleaned = line.strip()
            if cleaned:
                return cleaned
    return None


def _derive_run_result(
    *,
    total_instances: int | None,
    tasks_solved_at_least_once: int,
    solved_task_attempts: int,
    task_attempt_rows: int,
    invalid_task_attempts: int,
) -> str | None:
    if total_instances and tasks_solved_at_least_once >= total_instances:
        return "solved"
    if solved_task_attempts > 0:
        return "partially_solved"
    if task_attempt_rows > 0 and invalid_task_attempts >= task_attempt_rows:
        return "invalid"
    if task_attempt_rows > 0:
        return "unsolved"
    return None


def _compat_artifact_link(paths: list[str]) -> str | None:
    if not paths:
        return None
    if len(paths) == 1:
        return paths[0]
    return ";".join(paths)


def _resolve_analysis_agent_model(
    *,
    manifest: dict[str, Any],
    summary: dict[str, Any],
    metadata_rows: list[dict[str, Any]],
) -> str | None:
    summary_value = _clean_text(summary.get("ai_agent_and_model_used_for_analysis"))
    if summary_value:
        return summary_value

    manifest_value = _clean_text(manifest.get("ai_agent_and_model_used_for_analysis"))
    if manifest_value:
        return manifest_value

    metadata_value = _clean_text(
        _first_metadata_value(metadata_rows, "ai_agent_and_model_used_for_analysis")
    )
    if metadata_value:
        return metadata_value

    analysis_agent = _first_clean_text(
        summary.get("analysis_agent"),
        manifest.get("analysis_agent"),
        _first_metadata_value(metadata_rows, "analysis_agent"),
    )
    analysis_model = _first_clean_text(
        summary.get("analysis_model"),
        manifest.get("analysis_model"),
        _first_metadata_value(metadata_rows, "analysis_model"),
    )
    if analysis_agent and analysis_model:
        return f"{analysis_agent} ({analysis_model})"
    return _first_clean_text(analysis_agent, analysis_model)


def _load_trace_metadata_rows(run_root: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    attempts_root = run_root / "attempts"
    if not attempts_root.exists():
        return rows

    for attempt_dir in sorted(attempts_root.glob("attempt-*")):
        trace_path = attempt_dir / "trace.jsonl"
        if not trace_path.exists():
            continue
        for row in read_jsonl(trace_path):
            if not isinstance(row, dict):
                continue
            metadata = row.get("metadata")
            if not isinstance(metadata, dict):
                metadata = {}
            rows.append(
                {
                    "status": row.get("status"),
                    **metadata,
                }
            )
    return rows


def _extract_session_ids(metadata_rows: list[dict[str, Any]]) -> list[str]:
    values: list[str] = []
    for item in metadata_rows:
        if not isinstance(item, dict):
            continue
        direct_values = (
            item.get("current_init_session_id"),
            item.get("init_session_id"),
            item.get("currentInitSessionId"),
            item.get("initSessionId"),
        )
        for value in direct_values:
            cleaned = _clean_text(value)
            if cleaned:
                values.append(cleaned)
        session = item.get("session")
        if isinstance(session, dict):
            for key in ("initSessionId", "init_session_id"):
                cleaned = _clean_text(session.get(key))
                if cleaned:
                    values.append(cleaned)

        shortcut = item.get("bitloops_init_status_shortcut")
        if isinstance(shortcut, dict):
            for key in ("current_init_session_id", "currentInitSessionId"):
                cleaned = _clean_text(shortcut.get(key))
                if cleaned:
                    values.append(cleaned)
            shortcut_session = shortcut.get("session")
            if isinstance(shortcut_session, dict):
                for key in ("initSessionId", "init_session_id"):
                    cleaned = _clean_text(shortcut_session.get(key))
                    if cleaned:
                        values.append(cleaned)

    return sorted(dict.fromkeys(values))


def _attempt_artifact_paths(run_root: Path, filename: str) -> list[str]:
    attempts_root = run_root / "attempts"
    if not attempts_root.exists():
        return []
    output: list[str] = []
    for attempt_dir in sorted(attempts_root.glob("attempt-*")):
        candidate = attempt_dir / filename
        if candidate.exists():
            output.append(_display_path(candidate))
    return output


def _evaluation_report_paths(run_root: Path, summary_attempts: list[Any]) -> list[str]:
    output: list[str] = []
    for item in summary_attempts:
        if not isinstance(item, dict):
            continue
        report_path = _clean_text(item.get("report_path"))
        if report_path:
            output.append(report_path)
    if output:
        return output

    attempts_root = run_root / "attempts"
    if not attempts_root.exists():
        return []
    for attempt_dir in sorted(attempts_root.glob("attempt-*")):
        candidate = attempt_dir / "evaluation.json"
        if candidate.exists():
            output.append(_display_path(candidate))
    return output


def _display_path(path: Path) -> str:
    resolved = path.resolve()
    cwd = Path.cwd().resolve()
    try:
        return str(resolved.relative_to(cwd))
    except ValueError:
        return str(resolved)


def _sum_present(*values: int | None) -> int | None:
    present = [value for value in values if value is not None]
    if not present:
        return None
    return int(sum(present))


def _sum_int(rows: list[dict[str, Any]], key: str) -> int | None:
    values = [_coerce_int(row.get(key)) for row in rows]
    present = [value for value in values if value is not None]
    if not present:
        return None
    return int(sum(present))


def _sum_float(rows: list[dict[str, Any]], key: str) -> float | None:
    values = [_coerce_float(row.get(key)) for row in rows]
    present = [value for value in values if value is not None]
    if not present:
        return None
    return float(sum(present))


def _mean_float(rows: list[dict[str, Any]], key: str) -> float | None:
    values = [_coerce_float(row.get(key)) for row in rows]
    present = [value for value in values if value is not None]
    if not present:
        return None
    return float(mean(present))


def _median_float(rows: list[dict[str, Any]], key: str) -> float | None:
    values = [_coerce_float(row.get(key)) for row in rows]
    present = [value for value in values if value is not None]
    if not present:
        return None
    return float(median(present))


def _first_row_value(rows: list[dict[str, Any]], key: str) -> Any:
    for row in rows:
        if key in row and row.get(key) is not None:
            return row.get(key)
    return None


def _first_metadata_value(rows: list[dict[str, Any]], key: str) -> Any:
    for row in rows:
        if not isinstance(row, dict):
            continue
        if key in row and row.get(key) is not None:
            return row.get(key)
    return None


def _first_bool(rows: list[dict[str, Any]], key: str) -> bool | None:
    for row in rows:
        if not isinstance(row, dict):
            continue
        value = row.get(key)
        if isinstance(value, bool):
            return value
    return None


def _coalesce_bool(*values: Any) -> bool | None:
    for value in values:
        if isinstance(value, bool):
            return value
    return None


def _coalesce_int(*values: Any) -> int | None:
    for value in values:
        coerced = _coerce_int(value)
        if coerced is not None:
            return coerced
    return None


def _nested_value(payload: dict[str, Any], *keys: str) -> Any:
    current: Any = payload
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _first_clean_text(*values: Any) -> str | None:
    for value in values:
        cleaned = _clean_text(value)
        if cleaned:
            return cleaned
    return None


def _clean_text(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        value = str(value)
    cleaned = value.strip()
    return cleaned or None


def _coerce_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str) and value.strip():
        try:
            return int(float(value.strip()))
        except ValueError:
            return None
    return None


def _coerce_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return float(int(value))
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str) and value.strip():
        try:
            return float(value.strip())
        except ValueError:
            return None
    return None


def _resolve_engineer(manifest: dict[str, Any], summary: dict[str, Any]) -> str | None:
    for value in (
        summary.get("engineer"),
        manifest.get("engineer"),
        os.environ.get("BENCHKIT_ENGINEER"),
        os.environ.get("USER"),
    ):
        cleaned = _clean_text(value)
        if cleaned:
            return cleaned
    return None


def _write_csv(path: Path, fieldnames: list[str], rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({name: _csv_value(row.get(name)) for name in fieldnames})


def _csv_value(value: Any) -> Any:
    if isinstance(value, list):
        return ";".join(str(item) for item in value)
    if isinstance(value, dict):
        return str(value)
    return value


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Generate the standard appendix outputs plus a canonical run-level summary. "
            "If engineer is not present in run artifacts, BENCHKIT_ENGINEER or USER is used."
        )
    )
    parser.add_argument(
        "--run-root",
        action="append",
        required=True,
        type=Path,
        help="Repeatable run root path (contains run_manifest.json)",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("reports/appendix"),
        help="Directory for generated report files",
    )
    args = parser.parse_args()

    outputs = generate_report_files(run_roots=args.run_root, output_dir=args.output_dir)
    print(f"Run Summary JSONL: {outputs.run_summary.run_summary_jsonl}")
    print(f"Run Summary CSV: {outputs.run_summary.run_summary_csv}")
    print(f"Per-task JSONL: {outputs.appendix.per_task_jsonl}")
    print(f"Per-task CSV: {outputs.appendix.per_task_csv}")
    print(f"Results CSV: {outputs.appendix.results_csv}")
    print(f"Results Markdown: {outputs.appendix.results_markdown}")
    print(f"Per-Attempt Breakdown: {outputs.appendix.per_attempt_markdown}")
    print(f"Prompt/Tool Breakdown: {outputs.appendix.prompt_tool_markdown}")
    print(f"Tool Invocation Log: {outputs.appendix.tool_invocation_jsonl}")
    print(f"Tool Invocation Breakdown: {outputs.appendix.tool_invocation_markdown}")


if __name__ == "__main__":
    main()
