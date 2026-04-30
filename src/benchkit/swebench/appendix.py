from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from statistics import mean, median, stdev, variance
from typing import Any
import csv
import json
import re
import time

from benchkit.common.io import read_json, read_jsonl, write_jsonl

DEBUG_LOG_PATH = Path("/Users/petros/Desktop/work/benchmarks/.cursor/debug-1440e1.log")
DEBUG_SESSION_ID = "1440e1"


def _debug_log(*, hypothesis_id: str, location: str, message: str, data: dict[str, Any]) -> None:
    payload = {
        "sessionId": DEBUG_SESSION_ID,
        "runId": "appendix",
        "hypothesisId": hypothesis_id,
        "location": location,
        "message": message,
        "data": data,
        "timestamp": int(time.time() * 1000),
    }
    try:
        DEBUG_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with DEBUG_LOG_PATH.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
    except OSError:
        pass


PER_TASK_FIELDS = [
    "task_id",
    "attempt",
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
    "input_tokens",
    "output_tokens",
    "cache_read_input_tokens",
    "cache_creation_input_tokens",
    "total_input_processed_tokens",
    "total_processed_tokens",
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
    "mean_runtime_sec",
    "variance_runtime_sec",
    "stddev_runtime_sec",
    "mean_cost",
    "variance_cost",
    "stddev_cost",
]


@dataclass(slots=True)
class AppendixOutputs:
    per_task_jsonl: Path
    per_task_csv: Path
    results_csv: Path
    results_markdown: Path
    per_attempt_markdown: Path
    prompt_tool_markdown: Path
    tool_invocation_jsonl: Path
    tool_invocation_markdown: Path


def generate_appendix_files(run_roots: list[Path], output_dir: Path) -> AppendixOutputs:
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    per_task_rows = _build_per_task_rows(run_roots)
    tool_invocation_rows = _build_tool_invocation_rows(per_task_rows)
    results_rows = _build_results_rows(per_task_rows)

    per_task_jsonl = output_dir / "appendix_minimal_per_task_log.jsonl"
    per_task_csv = output_dir / "appendix_minimal_per_task_log.csv"
    results_csv = output_dir / "appendix_minimal_results_table.csv"
    results_markdown = output_dir / "appendix_minimal_results_table.md"
    per_attempt_markdown = output_dir / "appendix_per_attempt_breakdown.md"
    prompt_tool_markdown = output_dir / "appendix_prompt_tool_breakdown.md"
    tool_invocation_jsonl = output_dir / "appendix_tool_invocation_log.jsonl"
    tool_invocation_markdown = output_dir / "appendix_tool_invocation_breakdown.md"

    write_jsonl(per_task_jsonl, per_task_rows)
    _write_csv(per_task_csv, PER_TASK_FIELDS, per_task_rows)
    _write_csv(results_csv, RESULTS_FIELDS, results_rows)
    results_markdown.write_text(_render_results_markdown(results_rows), encoding="utf-8")
    per_attempt_markdown.write_text(
        _render_per_attempt_markdown(per_task_rows, results_rows), encoding="utf-8"
    )
    prompt_tool_markdown.write_text(
        _render_prompt_tool_markdown(per_task_rows), encoding="utf-8"
    )
    write_jsonl(tool_invocation_jsonl, tool_invocation_rows)
    tool_invocation_markdown.write_text(
        _render_tool_invocation_markdown(tool_invocation_rows), encoding="utf-8"
    )

    return AppendixOutputs(
        per_task_jsonl=per_task_jsonl,
        per_task_csv=per_task_csv,
        results_csv=results_csv,
        results_markdown=results_markdown,
        per_attempt_markdown=per_attempt_markdown,
        prompt_tool_markdown=prompt_tool_markdown,
        tool_invocation_jsonl=tool_invocation_jsonl,
        tool_invocation_markdown=tool_invocation_markdown,
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
                agent_id = _pick_string(manifest.get("agent", {}), ("id",))
                model_version = (
                    manifest.get("model", {}).get("resolved_name")
                    or manifest.get("model", {}).get("canonical_name")
                )
                token_input = _pick_number(metadata, ("token_input", "input_tokens"))
                token_output = _pick_number(metadata, ("token_output", "output_tokens"))
                reasoning_output_tokens = _pick_number(
                    metadata,
                    ("reasoning_output_tokens", "reasoningOutputTokens"),
                )
                total_tokens = _pick_number(
                    metadata,
                    ("total_tokens", "totalTokens"),
                )
                if total_tokens is None and token_input is not None and token_output is not None:
                    total_tokens = int(float(token_input) + float(token_output))
                cached_input_tokens = _pick_number(
                    metadata,
                    ("cached_input_tokens", "cachedInputTokens"),
                )
                cached_output_tokens = _pick_number(
                    metadata,
                    ("cached_output_tokens", "cachedOutputTokens"),
                )
                token_input_uncached = _pick_number(
                    metadata,
                    ("token_input_uncached",),
                )
                if token_input_uncached is None:
                    token_input_uncached = _derive_uncached_tokens(
                        total_tokens=token_input,
                        cached_tokens=cached_input_tokens,
                    )
                token_output_uncached = _pick_number(
                    metadata,
                    ("token_output_uncached",),
                )
                if token_output_uncached is None:
                    token_output_uncached = _derive_uncached_tokens(
                        total_tokens=token_output,
                        cached_tokens=cached_output_tokens,
                    )
                estimated_cost = _pick_number(metadata, ("estimated_cost", "cost_usd"))
                if estimated_cost is None:
                    estimated_cost = _compute_openai_cost_usd(
                        model_version=model_version,
                        token_input=token_input,
                        token_output=token_output,
                        cached_input_tokens=cached_input_tokens,
                    )
                canonical_token_metrics = _resolve_canonical_token_metrics(
                    metadata=metadata,
                    agent_id=agent_id,
                    token_input=token_input,
                    token_output=token_output,
                    cached_input_tokens=cached_input_tokens,
                    token_input_uncached=token_input_uncached,
                )

                row = {
                    "task_id": instance_id,
                    "attempt": attempt,
                    "benchmark": manifest.get("benchmark"),
                    "benchmark_version": _build_benchmark_version(manifest),
                    "repo": repo,
                    "repo_label": repo_label,
                    "language": instance.get("language", manifest.get("language")),
                    "agent": agent_id,
                    "model_version": model_version,
                    "condition": manifest.get("condition", "baseline"),
                    "status": status,
                    "runtime_sec": _ms_to_sec(_pick_number(metadata, ("elapsed_ms",))),
                    "input_tokens": canonical_token_metrics.get("input_tokens"),
                    "output_tokens": canonical_token_metrics.get("output_tokens"),
                    "cache_read_input_tokens": canonical_token_metrics.get(
                        "cache_read_input_tokens"
                    ),
                    "cache_creation_input_tokens": canonical_token_metrics.get(
                        "cache_creation_input_tokens"
                    ),
                    "total_input_processed_tokens": canonical_token_metrics.get(
                        "total_input_processed_tokens"
                    ),
                    "total_processed_tokens": canonical_token_metrics.get(
                        "total_processed_tokens"
                    ),
                    "estimated_cost": estimated_cost,
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
                    "prompt_text": _extract_prompt_text(metadata),
                    "agent_command": _extract_command(metadata),
                    "tool_usage_breakdown": _extract_tool_usage_breakdown(metadata),
                    "tool_invocation_counts": _extract_tool_invocation_counts(metadata),
                    "tool_invocation_sequence": _extract_tool_invocation_sequence(metadata),
                    "tool_invocations_raw": _extract_tool_invocations_raw(metadata),
                    "tool_invocations_curated": _extract_tool_invocations_curated(metadata),
                    "bitloops_commands": _extract_bitloops_commands(metadata),
                }
                # region agent log
                _debug_log(
                    hypothesis_id="H3",
                    location="src/benchkit/swebench/appendix.py:_build_per_task_rows",
                    message="appendix row metrics",
                    data={
                        "task_id": instance_id,
                        "condition": row["condition"],
                        "model_version": row["model_version"],
                        "metadata_metrics": {
                            "token_input": metadata.get("token_input"),
                            "token_output": metadata.get("token_output"),
                            "reasoning_output_tokens": metadata.get("reasoning_output_tokens"),
                            "total_tokens": metadata.get("total_tokens"),
                            "cached_input_tokens": metadata.get("cached_input_tokens"),
                            "cached_output_tokens": metadata.get("cached_output_tokens"),
                            "estimated_cost": metadata.get("estimated_cost"),
                            "cache_creation_input_tokens": metadata.get("cache_creation_input_tokens"),
                            "cache_read_input_tokens": metadata.get("cache_read_input_tokens"),
                            "cache_creation_ephemeral_5m_input_tokens": metadata.get(
                                "cache_creation_ephemeral_5m_input_tokens"
                            ),
                            "cache_creation_ephemeral_1h_input_tokens": metadata.get(
                                "cache_creation_ephemeral_1h_input_tokens"
                            ),
                        },
                        "computed_metrics": {
                            "runtime_sec": row["runtime_sec"],
                            "token_input_uncached": token_input_uncached,
                            "total_tokens": total_tokens,
                            "estimated_cost": row["estimated_cost"],
                        },
                    },
                )
                # endregion
                rows.append(row)
    return rows


def _derive_uncached_tokens(
    total_tokens: int | float | None,
    cached_tokens: int | float | None,
) -> int | None:
    if total_tokens is None:
        return None
    if cached_tokens is None:
        return int(float(total_tokens))
    return max(0, int(float(total_tokens) - float(cached_tokens)))


def _sum_token_values(*values: int | float | None) -> int | None:
    present = [int(float(value)) for value in values if value is not None]
    if not present:
        return None
    return int(sum(present))


def _resolve_token_usage_semantics(metadata: Any, agent_id: str | None) -> str:
    explicit = _pick_string(metadata, ("token_usage_semantics",))
    if explicit:
        return explicit

    wrapper = _pick_string(metadata, ("wrapper",))
    candidates = [value.strip().lower() for value in (agent_id, wrapper) if isinstance(value, str)]

    if any("codex" in value for value in candidates):
        return "codex_turn_completed"
    if _pick_string(metadata, ("token_metrics_source",)) == "opencode_step_finish_sum":
        return "opencode_step_finish"
    if any("opencode" in value for value in candidates):
        return "opencode_message_updated"
    if any("claude" in value for value in candidates):
        return "claude_result_usage"
    if any("cursor" in value for value in candidates):
        return "cursor_result_usage"
    return "generic"


def _resolve_canonical_token_metrics(
    *,
    metadata: Any,
    agent_id: str | None,
    token_input: int | float | None,
    token_output: int | float | None,
    cached_input_tokens: int | float | None,
    token_input_uncached: int | float | None,
) -> dict[str, int | None]:
    input_tokens = _pick_number(metadata, ("input_tokens",))
    output_tokens = _pick_number(metadata, ("output_tokens",))
    cache_read_input_tokens = _pick_number(metadata, ("cache_read_input_tokens",))
    cache_creation_input_tokens = _pick_number(metadata, ("cache_creation_input_tokens",))
    total_input_processed_tokens = _pick_number(metadata, ("total_input_processed_tokens",))
    total_processed_tokens = _pick_number(metadata, ("total_processed_tokens",))

    semantics = _resolve_token_usage_semantics(metadata, agent_id)
    if semantics == "codex_turn_completed":
        if input_tokens is None:
            input_tokens = token_input_uncached
        if input_tokens is None:
            input_tokens = _derive_uncached_tokens(token_input, cached_input_tokens)
        if cache_read_input_tokens is None:
            cache_read_input_tokens = cached_input_tokens
        if cache_read_input_tokens is None and (token_input is not None or token_output is not None):
            cache_read_input_tokens = 0
        if cache_creation_input_tokens is None and (
            token_input is not None or token_output is not None
        ):
            cache_creation_input_tokens = 0
    else:
        if input_tokens is None:
            input_tokens = token_input
        if cache_read_input_tokens is None and (token_input is not None or token_output is not None):
            cache_read_input_tokens = 0
        if cache_creation_input_tokens is None and (
            token_input is not None or token_output is not None
        ):
            cache_creation_input_tokens = 0

    if output_tokens is None:
        output_tokens = token_output
    if total_input_processed_tokens is None:
        total_input_processed_tokens = _sum_token_values(
            input_tokens,
            cache_read_input_tokens,
            cache_creation_input_tokens,
        )
    if total_processed_tokens is None:
        total_processed_tokens = _sum_token_values(
            total_input_processed_tokens,
            output_tokens,
        )

    return {
        "input_tokens": int(float(input_tokens)) if input_tokens is not None else None,
        "output_tokens": int(float(output_tokens)) if output_tokens is not None else None,
        "cache_read_input_tokens": (
            int(float(cache_read_input_tokens)) if cache_read_input_tokens is not None else None
        ),
        "cache_creation_input_tokens": (
            int(float(cache_creation_input_tokens))
            if cache_creation_input_tokens is not None
            else None
        ),
        "total_input_processed_tokens": (
            int(float(total_input_processed_tokens))
            if total_input_processed_tokens is not None
            else None
        ),
        "total_processed_tokens": (
            int(float(total_processed_tokens)) if total_processed_tokens is not None else None
        ),
    }


def _compute_openai_cost_usd(
    *,
    model_version: Any,
    token_input: int | float | None,
    token_output: int | float | None,
    cached_input_tokens: int | float | None,
) -> float | None:
    pricing = _openai_text_pricing(model_version)
    if pricing is None:
        return None
    if token_input is None or token_output is None:
        return None

    uncached_input_tokens = _derive_uncached_tokens(token_input, cached_input_tokens)
    if uncached_input_tokens is None:
        return None
    cached = int(float(cached_input_tokens)) if cached_input_tokens is not None else 0

    return (
        (float(uncached_input_tokens) * pricing["input"])
        + (float(cached) * pricing["cached_input"])
        + (float(token_output) * pricing["output"])
    )


def _openai_text_pricing(model_version: Any) -> dict[str, float] | None:
    if not isinstance(model_version, str):
        return None
    normalized = model_version.strip().lower()
    if not normalized:
        return None

    per_million = 1_000_000.0

    if _matches_model_prefix(
        normalized,
        prefixes=(
            "gpt-5.4-mini",
            "gpt-5-4-mini",
        ),
    ):
        return {
            "input": 0.75 / per_million,
            "cached_input": 0.075 / per_million,
            "output": 4.5 / per_million,
        }
    if _matches_model_prefix(
        normalized,
        prefixes=(
            "gpt-5.4-nano",
            "gpt-5-4-nano",
        ),
    ):
        return {
            "input": 0.20 / per_million,
            "cached_input": 0.02 / per_million,
            "output": 1.25 / per_million,
        }
    if _matches_model_prefix(
        normalized,
        prefixes=(
            "gpt-5.4",
            "gpt-5-4",
        ),
    ):
        return {
            "input": 2.50 / per_million,
            "cached_input": 0.25 / per_million,
            "output": 15.0 / per_million,
        }
    if _matches_model_prefix(
        normalized,
        prefixes=(
            "gpt-5.2",
            "gpt-5-2",
        ),
    ):
        return {
            "input": 1.75 / per_million,
            "cached_input": 0.175 / per_million,
            "output": 14.0 / per_million,
        }
    if _matches_model_prefix(
        normalized,
        prefixes=(
            "gpt-5.1",
            "gpt-5-1",
            "gpt-5",
        ),
    ):
        return {
            "input": 1.25 / per_million,
            "cached_input": 0.125 / per_million,
            "output": 10.0 / per_million,
        }
    return None


def _matches_model_prefix(normalized_model: str, *, prefixes: tuple[str, ...]) -> bool:
    for prefix in prefixes:
        if normalized_model == prefix:
            return True
        if normalized_model.startswith(prefix + "-"):
            return True
    return False


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
        mean_runtime = _mean_of(rows, "runtime_sec")
        variance_runtime = _variance_of(rows, "runtime_sec")
        stddev_runtime = _stddev_of(rows, "runtime_sec")
        mean_cost = _mean_of(rows, "estimated_cost")
        variance_cost = _variance_of(rows, "estimated_cost")
        stddev_cost = _stddev_of(rows, "estimated_cost")

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
                "mean_runtime_sec": mean_runtime,
                "variance_runtime_sec": variance_runtime,
                "stddev_runtime_sec": stddev_runtime,
                "mean_cost": mean_cost,
                "variance_cost": variance_cost,
                "stddev_cost": stddev_cost,
            }
        )
    return results


def _render_results_markdown(rows: list[dict[str, Any]]) -> str:
    header = (
        "| Agent | Condition | Benchmark | Language | Tasks | Solved | Solve Rate "
        "| Median Runtime (s) | Median Tool Calls | Median File Reads "
        "| Median Search Actions | Median Cost | Mean Runtime (s) | Variance Runtime (s^2) "
        "| Stddev Runtime (s) | Mean Cost | Variance Cost | Stddev Cost |\n"
    )
    sep = (
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | "
        "--- | --- | --- | --- | --- | --- |\n"
    )
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
                    _fmt_optional(row.get("mean_runtime_sec")),
                    _fmt_optional(row.get("variance_runtime_sec")),
                    _fmt_optional(row.get("stddev_runtime_sec")),
                    _fmt_optional(row.get("mean_cost")),
                    _fmt_optional(row.get("variance_cost")),
                    _fmt_optional(row.get("stddev_cost")),
                ]
            )
            + " |\n"
        )
    return "".join(lines)


def _render_per_attempt_markdown(
    per_task_rows: list[dict[str, Any]],
    results_rows: list[dict[str, Any]],
) -> str:
    lines: list[str] = ["# Per-Attempt Breakdown\n\n"]

    header = (
        "| Attempt | Task ID | Status | Runtime (s) | Input Tokens "
        "| Output Tokens | Cost ($) | Tool Calls | Files Edited | Patch Size |\n"
    )
    sep = "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |\n"
    lines.append(header)
    lines.append(sep)
    for row in per_task_rows:
        lines.append(
            "| "
            + " | ".join(
                [
                    str(row.get("attempt", "")),
                    str(row.get("task_id", "")),
                    str(row.get("status", "")),
                    _fmt_optional(row.get("runtime_sec")),
                    _fmt_optional(row.get("input_tokens")),
                    _fmt_optional(row.get("output_tokens")),
                    _fmt_optional(row.get("estimated_cost")),
                    _fmt_optional(row.get("tool_calls")),
                    _fmt_optional(row.get("files_edited")),
                    str(row.get("patch_size", "")),
                ]
            )
            + " |\n"
        )

    lines.append("\n## Summary\n\n")
    for result in results_rows:
        total = result.get("tasks", 0)
        solved = result.get("solved", 0)
        rate = f"{float(result['solve_rate']) * 100:.1f}%" if total else "N/A"
        lines.append(f"- **Solve rate**: {solved}/{total} ({rate})\n")
        lines.append(f"- **Median runtime**: {_fmt_optional(result.get('median_runtime_sec'))}s\n")
        lines.append(f"- **Median cost**: ${_fmt_optional(result.get('median_cost'))}\n")
        lines.append(f"- **Runtime variance**: {_fmt_optional(result.get('variance_runtime_sec'))}\n")
        lines.append(f"- **Cost variance**: {_fmt_optional(result.get('variance_cost'))}\n")

    return "".join(lines)


def _render_prompt_tool_markdown(per_task_rows: list[dict[str, Any]]) -> str:
    lines: list[str] = ["# Prompt And Tool Usage Breakdown\n\n"]
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in per_task_rows:
        condition = str(row.get("condition") or "unknown")
        grouped.setdefault(condition, []).append(row)

    for condition in sorted(grouped.keys()):
        lines.append(f"## Condition: {condition}\n\n")
        for row in grouped[condition]:
            task_id = str(row.get("task_id") or "unknown")
            attempt = row.get("attempt")
            lines.append(f"### Task `{task_id}` (attempt {attempt})\n\n")

            prompt_text = str(row.get("prompt_text") or "").strip()
            lines.append("#### Prompt\n\n")
            if prompt_text:
                lines.append("```text\n")
                lines.append(prompt_text)
                if not prompt_text.endswith("\n"):
                    lines.append("\n")
                lines.append("```\n\n")
            else:
                lines.append("_Prompt not captured in trace metadata._\n\n")

            lines.append("#### Tool Usage\n\n")
            tool_usage = row.get("tool_invocation_counts") or row.get("tool_usage_breakdown")
            if isinstance(tool_usage, dict) and tool_usage:
                for key in sorted(tool_usage.keys()):
                    value = tool_usage.get(key)
                    lines.append(f"- `{key}`: {value}\n")
            else:
                lines.append("_No detailed tool breakdown captured._\n")
            lines.append("\n")
            lines.append(
                "- Detailed per-call provenance: `appendix_tool_invocation_breakdown.md`\n\n"
            )

            tool_sequence = row.get("tool_invocation_sequence")
            if isinstance(tool_sequence, list) and tool_sequence:
                lines.append("#### Tool Sequence (High Level)\n\n")
                sequence_preview = [str(name) for name in tool_sequence[:20]]
                suffix = " -> ... (truncated)" if len(tool_sequence) > 20 else ""
                lines.append("- " + " -> ".join(f"`{name}`" for name in sequence_preview) + suffix + "\n")
                lines.append(
                    f"- Full per-call details: `appendix_tool_invocation_breakdown.md` "
                    f"(total calls: {len(tool_sequence)})\n\n"
                )

            lines.append("#### Commands\n\n")
            agent_command = row.get("agent_command")
            if isinstance(agent_command, str) and agent_command:
                lines.append(f"- Agent command: `{agent_command}`\n")
            else:
                lines.append("- Agent command: _not captured_\n")

            bitloops_commands = row.get("bitloops_commands")
            if isinstance(bitloops_commands, list) and bitloops_commands:
                for command in bitloops_commands:
                    if isinstance(command, str) and command:
                        lines.append(f"- Bitloops command: `{command}`\n")
            lines.append("\n")

    return "".join(lines)


def _build_tool_invocation_rows(per_task_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for task_row in per_task_rows:
        curated = task_row.get("tool_invocations_curated")
        raw = task_row.get("tool_invocations_raw")
        if not isinstance(curated, list) or not isinstance(raw, list):
            continue
        raw_by_index: dict[int, dict[str, Any]] = {}
        for raw_item in raw:
            if not isinstance(raw_item, dict):
                continue
            index = _coerce_int(raw_item.get("call_index"))
            if index is None:
                continue
            raw_by_index[index] = raw_item

        for curated_item in curated:
            if not isinstance(curated_item, dict):
                continue
            call_index = _coerce_int(curated_item.get("call_index"))
            if call_index is None:
                continue
            tool_name = str(curated_item.get("tool") or "").strip()
            if not tool_name:
                continue
            raw_item = raw_by_index.get(call_index, {})
            row = {
                "task_id": task_row.get("task_id"),
                "attempt": task_row.get("attempt"),
                "condition": task_row.get("condition"),
                "benchmark": task_row.get("benchmark"),
                "agent": task_row.get("agent"),
                "model_version": task_row.get("model_version"),
                "call_index": call_index,
                "tool": tool_name,
                "tool_use_id": curated_item.get("tool_use_id"),
                "curated": deepcopy_json_safe(curated_item),
                "raw": deepcopy_json_safe(raw_item),
            }
            rows.append(row)

    rows.sort(
        key=lambda item: (
            str(item.get("condition") or ""),
            str(item.get("task_id") or ""),
            _coerce_int(item.get("attempt")) or 0,
            _coerce_int(item.get("call_index")) or 0,
        )
    )
    return rows


def _render_tool_invocation_markdown(tool_rows: list[dict[str, Any]]) -> str:
    lines: list[str] = ["# Tool Invocation Breakdown\n\n"]
    if not tool_rows:
        lines.append("_No tool invocation records captured._\n")
        return "".join(lines)

    grouped: dict[tuple[str, str, str, str, int], list[dict[str, Any]]] = {}
    for row in tool_rows:
        key = (
            str(row.get("condition") or "unknown"),
            str(row.get("agent") or "unknown"),
            str(row.get("model_version") or "unknown"),
            str(row.get("task_id") or "unknown"),
            _coerce_int(row.get("attempt")) or 0,
        )
        grouped.setdefault(key, []).append(row)

    for (condition, agent, model_version, task_id, attempt), rows in sorted(grouped.items()):
        lines.append(f"## Condition: {condition}\n\n")
        lines.append(f"### Agent `{agent}` | Model `{model_version}`\n\n")
        lines.append(f"### Task `{task_id}` (attempt {attempt})\n\n")
        lines.append("| Call | Tool | Details |\n")
        lines.append("| --- | --- | --- |\n")
        for row in sorted(rows, key=lambda item: _coerce_int(item.get("call_index")) or 0):
            details = _format_curated_tool_details(row.get("curated"))
            lines.append(
                f"| {row.get('call_index')} | {row.get('tool')} | {details} |\n"
            )
        lines.append("\n")

    return "".join(lines)


def _format_curated_tool_details(curated: Any) -> str:
    if not isinstance(curated, dict):
        return ""
    tool_name = str(curated.get("tool") or "").strip().lower()
    if tool_name == "grep":
        return _detail_join(
            [
                _kv("query", curated.get("query")),
                _kv("path", curated.get("path")),
                _kv("include", curated.get("include")),
                _kv("flags", curated.get("flags")),
            ]
        )
    if tool_name == "read":
        return _detail_join(
            [
                _kv("path", curated.get("path")),
                _kv("offset", curated.get("offset")),
                _kv("limit", curated.get("limit")),
            ]
        )
    if tool_name == "bash":
        return _detail_join(
            [
                _kv("command", curated.get("command")),
                _kv("cwd", curated.get("cwd")),
                _kv("timeout", curated.get("timeout")),
            ]
        )
    if tool_name == "edit":
        return _detail_join(
            [
                _kv("path", curated.get("path")),
                _kv("old_chars", curated.get("old_chars")),
                _kv("new_chars", curated.get("new_chars")),
                _kv("replace_all", curated.get("replace_all")),
            ]
        )
    return _detail_join([_kv("raw", curated.get("raw_input_json"))])


def _kv(key: str, value: Any) -> str:
    if value in (None, "", [], {}):
        return ""
    if isinstance(value, (dict, list)):
        rendered = json.dumps(value, ensure_ascii=False, sort_keys=True)
    else:
        rendered = str(value)
    rendered = rendered.replace("|", "\\|")
    return f"`{key}`={rendered}"


def _detail_join(parts: list[str]) -> str:
    cleaned = [part for part in parts if part]
    return ", ".join(cleaned)


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


def _extract_prompt_text(metadata: Any) -> str | None:
    prompt = _pick_string(metadata, ("prompt_text", "prompt", "task_prompt"))
    if prompt:
        return prompt
    command = metadata.get("command") if isinstance(metadata, dict) else None
    if not isinstance(command, list) or not command:
        return None
    last = command[-1]
    if not isinstance(last, str):
        return None
    return last.strip() or None


def _extract_command(metadata: Any) -> str | None:
    command = metadata.get("command") if isinstance(metadata, dict) else None
    if not isinstance(command, list) or not command:
        return None

    rendered: list[str] = []
    last_index = len(command) - 1
    for index, item in enumerate(command):
        if not isinstance(item, str):
            continue
        if index == last_index:
            rendered.append("<PROMPT>")
        else:
            rendered.append(item)
    return " ".join(rendered).strip() or None


def _extract_tool_usage_breakdown(metadata: Any) -> dict[str, int] | None:
    if not isinstance(metadata, dict):
        return None
    breakdown_raw = metadata.get("tool_usage_breakdown")
    if isinstance(breakdown_raw, dict):
        normalized = _normalize_numeric_dict(breakdown_raw)
        if normalized:
            return normalized

    fallback = _normalize_numeric_dict(
        {
            "tool_calls_total": metadata.get("tool_calls"),
            "shell_commands": metadata.get("shell_commands"),
            "file_reads": metadata.get("file_reads"),
            "search_actions": metadata.get("search_actions"),
        }
    )
    return fallback or None


def _extract_tool_invocation_counts(metadata: Any) -> dict[str, int] | None:
    if not isinstance(metadata, dict):
        return None
    counts = metadata.get("tool_invocation_counts")
    if not isinstance(counts, dict):
        return None
    normalized = _normalize_numeric_dict(counts)
    return normalized or None


def _extract_tool_invocation_sequence(metadata: Any) -> list[str] | None:
    if not isinstance(metadata, dict):
        return None
    sequence = metadata.get("tool_invocation_sequence")
    if not isinstance(sequence, list):
        return None
    normalized = [str(item).strip() for item in sequence if isinstance(item, str) and item.strip()]
    return normalized or None


def _extract_tool_invocations_raw(metadata: Any) -> list[dict[str, Any]] | None:
    if not isinstance(metadata, dict):
        return None
    rows = metadata.get("tool_invocations_raw")
    if not isinstance(rows, list):
        return None
    output: list[dict[str, Any]] = []
    for item in rows:
        if not isinstance(item, dict):
            continue
        index = _coerce_int(item.get("call_index"))
        tool = str(item.get("tool") or "").strip()
        if index is None or not tool:
            continue
        output.append(deepcopy_json_safe(item))
    return output or None


def _extract_tool_invocations_curated(metadata: Any) -> list[dict[str, Any]] | None:
    if not isinstance(metadata, dict):
        return None
    rows = metadata.get("tool_invocations_curated")
    if not isinstance(rows, list):
        return None
    output: list[dict[str, Any]] = []
    for item in rows:
        if not isinstance(item, dict):
            continue
        index = _coerce_int(item.get("call_index"))
        tool = str(item.get("tool") or "").strip()
        if index is None or not tool:
            continue
        output.append(deepcopy_json_safe(item))
    return output or None


def _extract_bitloops_commands(metadata: Any) -> list[str]:
    if not isinstance(metadata, dict):
        return []
    commands: list[str] = []
    for key in (
        "bitloops_status_command",
        "bitloops_start_command",
        "bitloops_bootstrap_command",
        "bitloops_init_command",
    ):
        value = metadata.get(key)
        if not isinstance(value, list):
            continue
        parts = [str(part).strip() for part in value if isinstance(part, str) and part.strip()]
        if parts:
            commands.append(" ".join(parts))
    return commands


def _normalize_numeric_dict(payload: dict[str, Any]) -> dict[str, int]:
    normalized: dict[str, int] = {}
    for key, value in payload.items():
        number = _coerce_number(value)
        if number is None:
            continue
        normalized[str(key)] = int(float(number))
    return normalized


def _ms_to_sec(value: float | int | None) -> float | None:
    if value is None:
        return None
    return float(value) / 1000.0


def _median_of(rows: list[dict[str, Any]], key: str) -> float | None:
    values = [float(item[key]) for item in rows if isinstance(item.get(key), (int, float))]
    if not values:
        return None
    return float(median(values))


def _mean_of(rows: list[dict[str, Any]], key: str) -> float | None:
    values = [float(item[key]) for item in rows if isinstance(item.get(key), (int, float))]
    if not values:
        return None
    return float(mean(values))


def _variance_of(rows: list[dict[str, Any]], key: str) -> float | None:
    values = [float(item[key]) for item in rows if isinstance(item.get(key), (int, float))]
    if len(values) < 2:
        return None
    return float(variance(values))


def _stddev_of(rows: list[dict[str, Any]], key: str) -> float | None:
    values = [float(item[key]) for item in rows if isinstance(item.get(key), (int, float))]
    if len(values) < 2:
        return None
    return float(stdev(values))


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


def _coerce_int(value: Any) -> int | None:
    number = _coerce_number(value)
    if number is None:
        return None
    return int(float(number))


def deepcopy_json_safe(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False))
