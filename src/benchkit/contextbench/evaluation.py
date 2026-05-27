from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any
import json

from benchkit.common.io import read_jsonl, write_json, write_jsonl
from benchkit.swebench.dataset import load_instances
from benchkit.swebench.types import BenchmarkInstance

from .trajectory import build_contextbench_traj_data


def build_contextbench_prediction_jsonl(
    *,
    benchmark: str,
    dataset_path: Path,
    prediction_path: Path,
    trace_path: Path,
    output_path: Path,
) -> int:
    predictions = read_jsonl(prediction_path)
    traces = read_jsonl(trace_path) if trace_path.exists() else []
    trace_by_instance = {
        str(row.get("instance_id")): row for row in traces if row.get("instance_id")
    }
    instance_lookup = _instance_lookup(dataset_path=dataset_path, benchmark=benchmark)

    output_rows: list[dict[str, Any]] = []
    for row in predictions:
        instance_id = str(row.get("instance_id") or "").strip()
        if not instance_id:
            continue
        instance = instance_lookup.get(instance_id)
        if instance is None:
            continue

        trace_row = trace_by_instance.get(instance_id, {})
        metadata = trace_row.get("metadata", {})
        if not isinstance(metadata, dict):
            metadata = {}

        eval_instance_id = _contextbench_eval_instance_id(
            benchmark_instance_id=instance_id,
            instance=instance,
        )
        entry = {
            "instance_id": eval_instance_id,
            "benchkit_instance_id": instance_id,
            "repo_url": _prediction_repo_url(instance=instance, metadata=metadata),
            "commit": instance.base_commit,
            "model_patch": str(row.get("model_patch") or ""),
            "traj_data": build_contextbench_traj_data(
                tool_invocations_curated=metadata.get("tool_invocations_curated"),
                tool_invocations_raw=metadata.get("tool_invocations_raw"),
            ),
        }
        output_rows.append(entry)

    write_jsonl(output_path, output_rows)
    return len(output_rows)


def build_contextbench_gold_jsonl(
    *,
    dataset_path: Path,
    output_path: Path,
) -> int:
    rows = read_jsonl(dataset_path)
    output_rows: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        inst_id = str(
            row.get("original_inst_id") or row.get("inst_id") or row.get("instance_id") or ""
        ).strip()
        if not inst_id:
            continue

        init_ctx = row.get("init_ctx")
        add_ctx = row.get("add_ctx")
        if not isinstance(init_ctx, list):
            init_ctx = []
        if not isinstance(add_ctx, list):
            add_ctx = []

        gold_ctx = row.get("gold_ctx")
        if not isinstance(gold_ctx, list):
            gold_ctx = _parse_gold_context(row.get("gold_context"))

        repo = str(row.get("repo") or "").strip()
        repo_url = str(row.get("repo_url") or "").strip()
        if not repo_url and repo:
            repo_url = f"https://github.com/{repo}.git"

        output_rows.append(
            {
                "inst_id": inst_id,
                "original_inst_id": str(row.get("original_inst_id") or "").strip() or None,
                "repo": repo,
                "repo_url": repo_url,
                "commit": str(row.get("commit") or row.get("base_commit") or "").strip(),
                "init_ctx": init_ctx,
                "add_ctx": add_ctx,
                "gold_ctx": gold_ctx,
                "patch": str(row.get("patch") or ""),
                "test_patch": str(row.get("test_patch") or ""),
                "source": str(row.get("source") or ""),
                "language": str(row.get("language") or ""),
            }
        )

    write_jsonl(output_path, output_rows)
    return len(output_rows)


def parse_contextbench_results_jsonl(
    *,
    result_path: Path,
    parsed_path: Path,
    tasks_path: Path,
    prediction_path: Path | None = None,
) -> dict[str, Any]:
    if not result_path.exists():
        write_json(parsed_path, {})
        return {
            "task_count": 0,
            "solved_count": 0,
            "unsolved_count": 0,
            "parsed_path": parsed_path,
            "tasks_path": None,
        }

    rows = read_jsonl(result_path)
    prediction_map = _prediction_instance_id_map(prediction_path)
    tasks: list[dict[str, Any]] = []
    for row in rows:
        evaluator_instance_id = str(row.get("instance_id") or "").strip()
        if not evaluator_instance_id:
            continue
        instance_id = prediction_map.get(evaluator_instance_id, evaluator_instance_id)
        status = "invalid" if row.get("error") else "solved"
        task = {
            "instance_id": instance_id,
            "status": status,
            "final_file_coverage": _nested_number(row, ("final", "file", "coverage")),
            "final_file_precision": _nested_number(row, ("final", "file", "precision")),
            "final_symbol_coverage": _nested_number(row, ("final", "symbol", "coverage")),
            "final_symbol_precision": _nested_number(row, ("final", "symbol", "precision")),
            "final_span_coverage": _nested_number(row, ("final", "span", "coverage")),
            "final_span_precision": _nested_number(row, ("final", "span", "precision")),
            "final_line_coverage": _nested_number(row, ("final", "line", "coverage")),
            "final_line_precision": _nested_number(row, ("final", "line", "precision")),
            "traj_auc_file": _nested_number(row, ("trajectory", "auc_coverage", "file")),
            "traj_auc_symbol": _nested_number(
                row, ("trajectory", "auc_coverage", "symbol")
            ),
            "traj_auc_span": _nested_number(row, ("trajectory", "auc_coverage", "span")),
            "traj_auc_line": _nested_number(row, ("trajectory", "auc_coverage", "line")),
            "traj_redundancy_file": _nested_number(
                row, ("trajectory", "redundancy", "file")
            ),
            "traj_redundancy_symbol": _nested_number(
                row, ("trajectory", "redundancy", "symbol")
            ),
            "traj_redundancy_span": _nested_number(
                row, ("trajectory", "redundancy", "span")
            ),
            "traj_redundancy_line": _nested_number(
                row, ("trajectory", "redundancy", "line")
            ),
            "editloc_recall": _nested_number(row, ("editloc", "recall")),
            "editloc_precision": _nested_number(row, ("editloc", "precision")),
            "raw": row,
        }
        tasks.append(task)

    solved_count = sum(1 for row in tasks if row.get("status") == "solved")
    unsolved_count = sum(1 for row in tasks if row.get("status") == "unsolved")
    parsed_payload = {
        "source_file": str(result_path),
        "task_count": len(tasks),
        "solved_count": solved_count,
        "unsolved_count": unsolved_count,
    }
    write_json(parsed_path, parsed_payload)
    if tasks:
        write_jsonl(tasks_path, tasks)
        resolved_tasks_path: Path | None = tasks_path
    else:
        resolved_tasks_path = None

    return {
        "task_count": len(tasks),
        "solved_count": solved_count,
        "unsolved_count": unsolved_count,
        "parsed_path": parsed_path,
        "tasks_path": resolved_tasks_path,
    }


@lru_cache(maxsize=16)
def _instance_lookup(*, dataset_path: Path, benchmark: str) -> dict[str, BenchmarkInstance]:
    instances = load_instances(dataset_path, benchmark=benchmark)
    return {row.instance_id: row for row in instances}


def _instance_repo_url(instance: BenchmarkInstance) -> str:
    metadata = instance.metadata if isinstance(instance.metadata, dict) else {}
    repo_url = metadata.get("repo_url")
    if isinstance(repo_url, str) and repo_url.strip():
        return repo_url.strip()
    return f"https://github.com/{instance.repo}.git"


def _prediction_repo_url(*, instance: BenchmarkInstance, metadata: dict[str, Any]) -> str:
    workspace_repo = _workspace_repo_path_from_trace_metadata(metadata)
    if workspace_repo is not None:
        return workspace_repo
    return _instance_repo_url(instance)


def _workspace_repo_path_from_trace_metadata(metadata: dict[str, Any]) -> str | None:
    workspace = metadata.get("workspace")
    if not isinstance(workspace, dict):
        return None
    workspace_path = workspace.get("workspace_path")
    if not isinstance(workspace_path, str):
        return None
    candidate = workspace_path.strip()
    if not candidate:
        return None
    path = Path(candidate).expanduser()
    git_marker = path / ".git"
    if not path.exists() or not git_marker.exists():
        return None
    return str(path.resolve())


def _contextbench_eval_instance_id(
    *,
    benchmark_instance_id: str,
    instance: BenchmarkInstance,
) -> str:
    metadata = instance.metadata if isinstance(instance.metadata, dict) else {}
    for key in ("original_inst_id", "inst_id"):
        value = metadata.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return benchmark_instance_id


def _prediction_instance_id_map(
    prediction_path: Path | None,
) -> dict[str, str]:
    if prediction_path is None or not prediction_path.exists():
        return {}
    rows = read_jsonl(prediction_path)
    output: dict[str, str] = {}
    for row in rows:
        evaluator_id = str(row.get("instance_id") or "").strip()
        benchkit_id = str(row.get("benchkit_instance_id") or "").strip()
        if evaluator_id and benchkit_id:
            output[evaluator_id] = benchkit_id
    return output


def _nested_number(payload: dict[str, Any], path: tuple[str, ...]) -> float | int | None:
    current: Any = payload
    for key in path:
        if not isinstance(current, dict) or key not in current:
            return None
        current = current[key]
    if isinstance(current, (int, float)):
        return current
    return None


def _parse_gold_context(raw: Any) -> list[dict[str, Any]]:
    if isinstance(raw, list):
        return [item for item in raw if isinstance(item, dict)]
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return []
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            return []
        if isinstance(parsed, list):
            return [item for item in parsed if isinstance(item, dict)]
    return []
