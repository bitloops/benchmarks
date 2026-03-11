from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from statistics import median
from typing import Any
import csv
import json
import re

from benchkit.common.io import read_json, read_jsonl, write_jsonl


PER_TASK_FIELDS = [
    "task_id",
    "benchmark",
    "benchmark_version",
    "repo",
    "repo_label",
    "language",
    "agent",
    "model_version",
    "condition",
    "status",
    "runtime_sec",
    "token_input",
    "token_output",
    "estimated_cost",
    "tool_calls",
    "shell_commands",
    "file_reads",
    "search_actions",
    "files_edited",
    "patch_size",
    "first_file_opened",
    "first_file_edited",
    "first_test_command",
    "bitloops_context_tokens",
    "evaluator_result",
]

RESULTS_FIELDS = [
    "agent",
    "condition",
    "benchmark",
    "language",
    "tasks",
    "solved",
    "solve_rate",
    "median_runtime_sec",
    "median_tool_calls",
    "median_file_reads",
    "median_search_actions",
    "median_cost",
]


@dataclass(slots=True)
class AppendixOutputs:
    per_task_jsonl: Path
    per_task_csv: Path
    results_csv: Path
    results_markdown: Path


def generate_appendix_files(run_roots: list[Path], output_dir: Path) -> AppendixOutputs:
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    per_task_rows = _build_per_task_rows(run_roots)
    results_rows = _build_results_rows(per_task_rows)

    per_task_jsonl = output_dir / "appendix_minimal_per_task_log.jsonl"
    per_task_csv = output_dir / "appendix_minimal_per_task_log.csv"
    results_csv = output_dir / "appendix_minimal_results_table.csv"
    results_markdown = output_dir / "appendix_minimal_results_table.md"

    write_jsonl(per_task_jsonl, per_task_rows)
    _write_csv(per_task_csv, PER_TASK_FIELDS, per_task_rows)
    _write_csv(results_csv, RESULTS_FIELDS, results_rows)
    results_markdown.write_text(_render_results_markdown(results_rows), encoding="utf-8")

    return AppendixOutputs(
        per_task_jsonl=per_task_jsonl,
        per_task_csv=per_task_csv,
        results_csv=results_csv,
        results_markdown=results_markdown,
    )


def _build_per_task_rows(run_roots: list[Path]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for run_root in run_roots:
        run_root = run_root.resolve()
        manifest_path = run_root / "run_manifest.json"
        if not manifest_path.exists():
            continue
        manifest = read_json(manifest_path)
        instances = {
            row["instance_id"]: row for row in read_jsonl(run_root / "instances.jsonl")
        }
        attempts_root = run_root / "attempts"
        if not attempts_root.exists():
            continue

        for attempt_dir in sorted(attempts_root.glob("attempt-*")):
            attempt = _parse_attempt_number(attempt_dir.name)
            predictions = {
                row["instance_id"]: row
                for row in read_jsonl(attempt_dir / "predictions.jsonl")
            }
            traces = {
                row["instance_id"]: row for row in read_jsonl(attempt_dir / "trace.jsonl")
            }
            eval_tasks = _load_eval_tasks(attempt_dir / "evaluation.tasks.jsonl")

            all_ids = sorted(set(instances.keys()) | set(predictions.keys()) | set(traces.keys()))
            for instance_id in all_ids:
                instance = instances.get(instance_id, {})
                prediction = predictions.get(instance_id, {})
                trace = traces.get(instance_id, {})
                metadata = trace.get("metadata", {}) if isinstance(trace, dict) else {}
                instance_metadata = (
                    instance.get("metadata", {})
                    if isinstance(instance.get("metadata"), dict)
                    else {}
                )
                eval_row = eval_tasks.get(instance_id)

                status = _derive_task_status(eval_row, trace)
                patch = str(prediction.get("model_patch", ""))
                files_edited = len(re.findall(r"(?m)^diff --git ", patch))
                repo = instance.get("repo")
                repo_label = _pick_string(
                    instance_metadata,
                    ("repo_label", "repo_owner", "owner"),
                ) or _derive_repo_label(str(repo or ""))

                row = {
                    "task_id": instance_id,
                    "benchmark": manifest.get("benchmark"),
                    "benchmark_version": _build_benchmark_version(manifest),
                    "repo": repo,
                    "repo_label": repo_label,
                    "language": instance.get("language", manifest.get("language")),
                    "agent": manifest.get("agent", {}).get("id"),
                    "model_version": manifest.get("model", {}).get("resolved_name")
                    or manifest.get("model", {}).get("canonical_name"),
                    "condition": manifest.get("condition", "baseline"),
                    "status": status,
                    "runtime_sec": _ms_to_sec(_pick_number(metadata, ("elapsed_ms",))),
                    "token_input": _pick_number(metadata, ("token_input", "input_tokens")),
                    "token_output": _pick_number(metadata, ("token_output", "output_tokens")),
                    "estimated_cost": _pick_number(metadata, ("estimated_cost", "cost_usd")),
                    "tool_calls": _pick_number(
                        metadata,
                        ("tool_calls", "total_tool_calls", "tools_count"),
                    ),
                    "shell_commands": _pick_number(
                        metadata,
                        ("shell_commands", "shell_command_count"),
                    ),
                    "file_reads": _pick_number(
                        metadata,
                        ("file_reads", "files_read", "file_open_count"),
                    ),
                    "search_actions": _pick_number(
                        metadata,
                        ("search_actions", "search_count", "grep_count", "find_count"),
                    ),
                    "files_edited": files_edited if files_edited > 0 else None,
                    "patch_size": len(patch) if patch else 0,
                    "first_file_opened": _pick_string(
                        metadata, ("first_file_opened", "first_opened_file")
                    ),
                    "first_file_edited": _pick_string(
                        metadata, ("first_file_edited", "first_edited_file")
                    ),
                    "first_test_command": _pick_string(
                        metadata, ("first_test_command", "test_command_first")
                    ),
                    "bitloops_context_tokens": _pick_number(
                        metadata, ("bitloops_context_tokens",)
                    ),
                    "evaluator_result": json.dumps(eval_row["raw"]) if eval_row else None,
                }
                rows.append(row)
    return rows


def _build_results_rows(per_task_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str, str, str], list[dict[str, Any]]] = {}
    for row in per_task_rows:
        key = (
            str(row.get("agent") or "unknown"),
            str(row.get("condition") or "unknown"),
            str(row.get("benchmark") or "unknown"),
            str(row.get("language") or "unknown"),
        )
        groups.setdefault(key, []).append(row)

    results: list[dict[str, Any]] = []
    for (agent, condition, benchmark, language), rows in sorted(groups.items()):
        tasks = len(rows)
        solved = sum(1 for item in rows if item.get("status") == "solved")
        runtime = _median_of(rows, "runtime_sec")
        tool_calls = _median_of(rows, "tool_calls")
        file_reads = _median_of(rows, "file_reads")
        search_actions = _median_of(rows, "search_actions")
        cost = _median_of(rows, "estimated_cost")

        results.append(
            {
                "agent": agent,
                "condition": condition,
                "benchmark": benchmark,
                "language": language,
                "tasks": tasks,
                "solved": solved,
                "solve_rate": solved / tasks if tasks else 0.0,
                "median_runtime_sec": runtime,
                "median_tool_calls": tool_calls,
                "median_file_reads": file_reads,
                "median_search_actions": search_actions,
                "median_cost": cost,
            }
        )
    return results


def _render_results_markdown(rows: list[dict[str, Any]]) -> str:
    header = (
        "| Agent | Condition | Benchmark | Language | Tasks | Solved | Solve Rate "
        "| Median Runtime (s) | Median Tool Calls | Median File Reads "
        "| Median Search Actions | Median Cost |\n"
    )
    sep = "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |\n"
    lines = [header, sep]
    for row in rows:
        solve_rate = f"{float(row['solve_rate']) * 100:.1f}%"
        lines.append(
            "| "
            + " | ".join(
                [
                    str(row.get("agent", "")),
                    str(row.get("condition", "")),
                    str(row.get("benchmark", "")),
                    str(row.get("language", "")),
                    str(row.get("tasks", "")),
                    str(row.get("solved", "")),
                    solve_rate,
                    _fmt_optional(row.get("median_runtime_sec")),
                    _fmt_optional(row.get("median_tool_calls")),
                    _fmt_optional(row.get("median_file_reads")),
                    _fmt_optional(row.get("median_search_actions")),
                    _fmt_optional(row.get("median_cost")),
                ]
            )
            + " |\n"
        )
    return "".join(lines)


def _parse_attempt_number(name: str) -> int:
    match = re.search(r"(\d+)$", name)
    return int(match.group(1)) if match else 0


def _build_benchmark_version(manifest: dict[str, Any]) -> str:
    dataset = str(manifest.get("dataset_path") or "")
    split = str(manifest.get("split") or "")
    return f"{dataset}|split={split}"


def _derive_task_status(eval_row: dict[str, Any] | None, trace: dict[str, Any]) -> str:
    if eval_row:
        status = str(eval_row.get("status") or "").lower()
        if status in {"solved", "unsolved", "invalid", "timeout"}:
            return status
    trace_status = str(trace.get("status") or "").lower()
    if trace_status == "error":
        return "invalid"
    return "unsolved"


def _load_eval_tasks(path: Path) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    rows = read_jsonl(path)
    output: dict[str, dict[str, Any]] = {}
    for row in rows:
        instance_id = row.get("instance_id")
        if not instance_id:
            continue
        output[str(instance_id)] = row
    return output


def _pick_number(metadata: Any, keys: tuple[str, ...]) -> float | int | None:
    if not isinstance(metadata, dict):
        return None
    for key in keys:
        if key not in metadata:
            continue
        number = _coerce_number(metadata[key])
        if number is not None:
            return number
    return None


def _pick_string(metadata: Any, keys: tuple[str, ...]) -> str | None:
    if not isinstance(metadata, dict):
        return None
    for key in keys:
        if key not in metadata:
            continue
        value = metadata[key]
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _ms_to_sec(value: float | int | None) -> float | None:
    if value is None:
        return None
    return float(value) / 1000.0


def _median_of(rows: list[dict[str, Any]], key: str) -> float | None:
    values = [float(item[key]) for item in rows if isinstance(item.get(key), (int, float))]
    if not values:
        return None
    return float(median(values))


def _write_csv(path: Path, fieldnames: list[str], rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({name: row.get(name) for name in fieldnames})


def _fmt_optional(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float):
        return f"{value:.3f}"
    return str(value)


def _derive_repo_label(repo: str) -> str | None:
    if not repo:
        return None
    owner, _, _name = repo.partition("/")
    return owner.strip() or repo


def _coerce_number(value: Any) -> float | int | None:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            return int(text)
        except ValueError:
            try:
                return float(text)
            except ValueError:
                return None
    return None
