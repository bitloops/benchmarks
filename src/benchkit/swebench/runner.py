from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from benchkit.common.artifacts import create_run_layout, snapshot_config
from benchkit.common.config import AgentConfig, RunConfig
from benchkit.common.io import write_json, write_jsonl
from benchkit.swebench.agents.base import RunContext
from benchkit.swebench.agents.registry import build_agent_adapter
from benchkit.swebench.dataset import filter_instances, load_instances
from benchkit.swebench.evaluation import AttemptEvaluationResult, evaluate_predictions_with_harness
from benchkit.swebench.model_mapper import resolve_model_name
from benchkit.swebench.types import BenchmarkInstance, PredictionRecord
from benchkit.swebench.workspace import WorkspacePrepResult, prepare_instance_workspace


@dataclass(slots=True)
class RunResult:
    run_id: str
    run_root: Path
    total_instances: int
    attempts: int
    prediction_files: list[Path]
    trace_files: list[Path]
    evaluation_reports: list[Path]


def execute_run(config: RunConfig, dry_run: bool = False, attempts: int | None = None) -> RunResult:
    all_instances = load_instances(config.dataset_path)
    selected_instances = filter_instances(
        all_instances,
        language=config.language,
        include_repos=config.include_repos,
        include_instance_ids=config.include_instance_ids,
        max_instances=config.max_instances,
    )
    if not selected_instances:
        raise ValueError(
            "No dataset instances selected after filtering. "
            "Check language/max_instances/dataset content."
        )

    attempts_count = attempts if attempts is not None else config.attempts
    if attempts_count < 1:
        raise ValueError("attempts must be >= 1")

    layout = create_run_layout(config.output_root, config.benchmark)
    snapshot_config(config.source_path, layout.config_snapshot_path)

    write_jsonl(layout.instances_path, (item.to_row() for item in selected_instances))

    agent_cfg = _select_agent_config(config, dry_run=dry_run)
    adapter = build_agent_adapter(agent_cfg)
    model_resolution = resolve_model_name(
        canonical_name=config.model.name,
        agent_id=agent_cfg.id,
        model_map=config.model_map,
    )
    resolved_model = replace(config.model, name=model_resolution.resolved_name)
    model_label = (
        f"agent:{agent_cfg.id}|model:{model_resolution.canonical_name}"
        f"|resolved:{model_resolution.resolved_name}"
    )

    started_at = datetime.now(timezone.utc).isoformat()
    manifest = _build_manifest(
        config=config,
        agent_id=agent_cfg.id,
        model_resolution={
            "canonical_name": model_resolution.canonical_name,
            "resolved_name": model_resolution.resolved_name,
            "map_key": model_resolution.map_key,
            "source": model_resolution.source,
            "agent_id": model_resolution.agent_id,
        },
        run_id=layout.run_id,
        total_instances=len(selected_instances),
        attempts=attempts_count,
        dry_run=dry_run,
        started_at=started_at,
    )
    write_json(layout.manifest_path, manifest)

    prediction_files: list[Path] = []
    trace_files: list[Path] = []
    evaluation_results: list[AttemptEvaluationResult] = []
    workspace_cache: dict[str, WorkspacePrepResult] = {}
    success_count_total = 0

    for attempt in range(1, attempts_count + 1):
        attempt_dir = layout.attempt_dir(attempt)
        attempt_dir.mkdir(parents=True, exist_ok=True)
        prediction_path = attempt_dir / "predictions.jsonl"
        trace_path = attempt_dir / "trace.jsonl"

        predictions: list[dict[str, Any]] = []
        traces: list[dict[str, Any]] = []

        for instance in selected_instances:
            status = "ok"
            patch = ""
            metadata: dict[str, Any] = {}
            error_message = None
            workspace_result = _resolve_workspace(
                config=config,
                layout_run_root=layout.run_root,
                instance=instance,
                cache=workspace_cache,
            )
            workspace_metadata = {"workspace": workspace_result.to_metadata()}

            run_context = RunContext(
                attempt=attempt,
                timeout_seconds=config.timeout_seconds,
                workspace_root=workspace_result.workspace_path or Path.cwd(),
                model=resolved_model,
                canonical_model_name=model_resolution.canonical_name,
                run_id=layout.run_id,
                benchmark=config.benchmark,
            )

            if workspace_result.status == "error":
                status = "error"
                error_message = f"workspace preparation failed: {workspace_result.error}"
                metadata = workspace_metadata
            else:
                try:
                    result = adapter.generate_patch(instance, run_context)
                    patch = result.patch
                    metadata = {**workspace_metadata, **result.metadata}
                    success_count_total += 1
                except Exception as exc:  # noqa: BLE001
                    status = "error"
                    error_message = str(exc)
                    metadata = workspace_metadata

            prediction = PredictionRecord(
                instance_id=instance.instance_id,
                model_name_or_path=model_label,
                model_patch=patch,
            )
            predictions.append(prediction.to_row())
            traces.append(
                {
                    "instance_id": instance.instance_id,
                    "status": status,
                    "error": error_message,
                    "metadata": metadata,
                }
            )

        write_jsonl(prediction_path, predictions)
        write_jsonl(trace_path, traces)
        prediction_files.append(prediction_path)
        trace_files.append(trace_path)

        evaluation_result = evaluate_predictions_with_harness(
            config=config.evaluation,
            run_id=layout.run_id,
            attempt=attempt,
            benchmark=config.benchmark,
            prediction_path=prediction_path,
            attempt_dir=attempt_dir,
        )
        evaluation_results.append(evaluation_result)

    finished_at = datetime.now(timezone.utc).isoformat()
    total_calls = len(selected_instances) * attempts_count
    summary = {
        "run_id": layout.run_id,
        "benchmark": config.benchmark,
        "condition": config.condition,
        "dataset_path": str(config.dataset_path),
        "split": config.split,
        "language": config.language,
        "include_repos": config.include_repos,
        "include_instance_ids": config.include_instance_ids,
        "total_instances": len(selected_instances),
        "attempts": attempts_count,
        "total_agent_calls": total_calls,
        "successful_agent_calls": success_count_total,
        "failed_agent_calls": total_calls - success_count_total,
        "started_at_utc": started_at,
        "finished_at_utc": finished_at,
        "run_root": str(layout.run_root),
        "model_resolution": {
            "canonical_name": model_resolution.canonical_name,
            "resolved_name": model_resolution.resolved_name,
            "map_key": model_resolution.map_key,
            "source": model_resolution.source,
            "agent_id": model_resolution.agent_id,
        },
        "evaluation": {
            "enabled": config.evaluation.enabled,
            "attempts": [result.to_row() for result in evaluation_results],
        },
    }
    write_json(layout.summary_path, summary)

    return RunResult(
        run_id=layout.run_id,
        run_root=layout.run_root,
        total_instances=len(selected_instances),
        attempts=attempts_count,
        prediction_files=prediction_files,
        trace_files=trace_files,
        evaluation_reports=[result.report_path for result in evaluation_results],
    )


def _select_agent_config(config: RunConfig, dry_run: bool) -> AgentConfig:
    if dry_run:
        return AgentConfig(id="noop", command=[], extra_args=[])
    return config.agent


def _build_manifest(
    config: RunConfig,
    agent_id: str,
    model_resolution: dict[str, Any],
    run_id: str,
    total_instances: int,
    attempts: int,
    dry_run: bool,
    started_at: str,
) -> dict[str, Any]:
    return {
        "run_id": run_id,
        "benchmark": config.benchmark,
        "dataset_path": str(config.dataset_path),
        "split": config.split,
        "language": config.language,
        "condition": config.condition,
        "include_repos": config.include_repos,
        "include_instance_ids": config.include_instance_ids,
        "max_instances": config.max_instances,
        "attempts": attempts,
        "timeout_seconds": config.timeout_seconds,
        "dry_run": dry_run,
        "total_instances": total_instances,
        "model": {
            "provider": config.model.provider,
            "canonical_name": config.model.name,
            "resolved_name": model_resolution["resolved_name"],
            "map_key": model_resolution["map_key"],
            "resolution_source": model_resolution["source"],
            "temperature": config.model.temperature,
            "max_tokens": config.model.max_tokens,
        },
        "model_map": config.model_map,
        "workspace": {
            "prepare_workspace": config.prepare_workspace,
            "repo_url_template": config.repo_url_template,
            "git_bin": config.git_bin,
            "workspace_root": str(config.workspace_root) if config.workspace_root else None,
            "workspace_timeout_seconds": config.workspace_timeout_seconds,
        },
        "agent": {
            "id": agent_id,
            "command": config.agent.command if agent_id == config.agent.id else [],
            "extra_args": config.agent.extra_args if agent_id == config.agent.id else [],
        },
        "started_at_utc": started_at,
        "evaluation": {
            "enabled": config.evaluation.enabled,
            "python_bin": config.evaluation.python_bin,
            "swebench_repo": (
                str(config.evaluation.swebench_repo)
                if config.evaluation.swebench_repo
                else None
            ),
            "dataset_name": config.evaluation.dataset_name,
            "split": config.evaluation.split,
            "max_workers": config.evaluation.max_workers,
            "timeout_seconds": config.evaluation.timeout_seconds,
            "command_template": config.evaluation.command_template,
            "result_json_path_template": config.evaluation.result_json_path_template,
            "extra_args": config.evaluation.extra_args,
        },
    }


def _resolve_workspace(
    config: RunConfig,
    layout_run_root: Path,
    instance: BenchmarkInstance,
    cache: dict[str, WorkspacePrepResult],
) -> WorkspacePrepResult:
    if not config.prepare_workspace:
        return WorkspacePrepResult(
            status="disabled",
            workspace_path=Path.cwd(),
            repo_url=None,
            elapsed_ms=0,
            error=None,
        )

    key = f"{instance.repo}@{instance.base_commit}"
    if key in cache and cache[key].status in {"prepared", "reused"}:
        return cache[key]

    result = prepare_instance_workspace(
        instance=instance,
        run_root=layout_run_root,
        repo_url_template=config.repo_url_template,
        git_bin=config.git_bin,
        timeout_seconds=config.workspace_timeout_seconds,
        workspace_root=config.workspace_root,
    )
    cache[key] = result
    return result
