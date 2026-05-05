from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import hashlib
import sys
import threading

from benchkit.common.artifacts import create_run_layout, snapshot_config
from benchkit.common.config import AgentConfig, RunConfig
from benchkit.common.io import read_jsonl, write_json, write_jsonl
from benchkit.swebench.agents.base import AgentAdapter, RunContext
from benchkit.swebench.agents.registry import build_agent_adapter
from benchkit.swebench.agents.codex.config import build_codex_run_metadata
from benchkit.swebench.agents.opencode.config import (
    build_ollama_provider_run_metadata,
    build_opencode_run_metadata,
)
from benchkit.swebench.dataset import filter_instances, load_instances
from benchkit.swebench.evaluation import AttemptEvaluationResult, evaluate_predictions_with_harness
from benchkit.swebench.model_mapper import resolve_model_name
from benchkit.swebench.types import BenchmarkInstance, PredictionRecord
from benchkit.swebench.workspace import WorkspacePrepResult, prepare_instance_workspace


def _prompt_template_version(prompt_protocol: str) -> str:
    normalized = str(prompt_protocol or "").strip().lower()
    if normalized in {"swe", "style3"}:
        return "swe_v1"
    return "minimal_v3"


def _prompt_template_hash(
    *,
    prompt_protocol: str,
    retrieval_file_source: str,
    retrieval_k: int,
) -> str:
    version = _prompt_template_version(prompt_protocol)
    token = (
        f"{version}|protocol={prompt_protocol}|"
        f"source={retrieval_file_source}|k={retrieval_k}"
    )
    return hashlib.sha256(token.encode("utf-8")).hexdigest()[:16]


@dataclass(slots=True)
class RunResult:
    run_id: str
    run_root: Path
    total_instances: int
    attempts: int
    prediction_files: list[Path]
    trace_files: list[Path]
    evaluation_reports: list[Path]


@dataclass(slots=True)
class _AttemptState:
    attempt: int
    attempt_dir: Path
    prediction_path: Path
    trace_path: Path
    prediction_slots: list[dict[str, Any] | None]
    trace_slots: list[dict[str, Any] | None]
    instance_artifact_dirs: list[Path | None]
    instance_evaluation_futures: list[Future[AttemptEvaluationResult] | None]
    lock: threading.Lock = field(default_factory=threading.Lock)
    completed_count: int = 0
    finalization_future: Future["_AttemptFinalizationResult"] | None = None
    success_count: int = 0


@dataclass(slots=True)
class _AttemptFinalizationResult:
    attempt: int
    evaluation_result: AttemptEvaluationResult


def execute_run(
    config: RunConfig,
    dry_run: bool = False,
    attempts: int | None = None,
    max_workers: int | None = None,
) -> RunResult:
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
    max_workers_count = max_workers if max_workers is not None else config.max_workers
    if max_workers_count < 1:
        raise ValueError("max_workers must be >= 1")
    parallel_attempts_enabled = attempts_count > 1 and max_workers_count > 1
    workspace_isolation_mode = _resolve_workspace_isolation_mode(
        requested_mode=config.workspace_isolation_mode,
        parallel_attempts_enabled=parallel_attempts_enabled,
    )

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
        max_workers=max_workers_count,
        workspace_isolation_mode=workspace_isolation_mode,
        parallel_attempts_enabled=parallel_attempts_enabled,
        dry_run=dry_run,
        started_at=started_at,
    )
    write_json(layout.manifest_path, manifest)

    prediction_files: list[Path] = []
    trace_files: list[Path] = []
    evaluation_results: list[AttemptEvaluationResult] = []
    workspace_cache: dict[str, WorkspacePrepResult] = {}
    workspace_in_flight: dict[str, threading.Event] = {}
    workspace_lock = threading.Lock()

    attempt_states: list[_AttemptState] = []
    for attempt in range(1, attempts_count + 1):
        attempt_dir = layout.attempt_dir(attempt)
        attempt_dir.mkdir(parents=True, exist_ok=True)
        attempt_states.append(
            _AttemptState(
                attempt=attempt,
                attempt_dir=attempt_dir,
                prediction_path=attempt_dir / "predictions.jsonl",
                trace_path=attempt_dir / "trace.jsonl",
                prediction_slots=[None] * len(selected_instances),
                trace_slots=[None] * len(selected_instances),
                instance_artifact_dirs=[None] * len(selected_instances),
                instance_evaluation_futures=[None] * len(selected_instances),
            )
        )

    total_calls = len(selected_instances) * attempts_count
    job_max_workers = min(max_workers_count, total_calls)
    finalization_max_workers = max(1, job_max_workers)
    with ThreadPoolExecutor(max_workers=finalization_max_workers) as finalizer_executor:
        if parallel_attempts_enabled:
            _log_progress(
                f"parallel attempts enabled: executing {total_calls} agent call(s) "
                f"across {attempts_count} attempt(s) with max_workers={job_max_workers}"
            )
            with ThreadPoolExecutor(max_workers=job_max_workers) as executor:
                futures = [
                    executor.submit(
                        _run_instance,
                        attempt=attempt_state.attempt,
                        attempt_dir=attempt_state.attempt_dir,
                        attempts_count=attempts_count,
                        instance_index=instance_index,
                        total_instances=len(selected_instances),
                        instance=instance,
                        adapter=adapter,
                        config=config,
                        layout_run_root=layout.run_root,
                        run_id=layout.run_id,
                        resolved_model=resolved_model,
                        canonical_model_name=model_resolution.canonical_name,
                        model_label=model_label,
                        workspace_cache=workspace_cache,
                        workspace_in_flight=workspace_in_flight,
                        workspace_lock=workspace_lock,
                        workspace_isolation_mode=workspace_isolation_mode,
                    )
                    for attempt_state in attempt_states
                    for instance_index, instance in enumerate(selected_instances, start=1)
                ]
                for future in as_completed(futures):
                    _record_attempt_completion(
                        future.result(),
                        attempt_states=attempt_states,
                        total_instances=len(selected_instances),
                        selected_instances=selected_instances,
                        attempts_count=attempts_count,
                        benchmark=config.benchmark,
                        run_id=layout.run_id,
                        evaluation_config=config.evaluation,
                        finalizer_executor=finalizer_executor,
                    )
        else:
            for attempt_state in attempt_states:
                attempt_max_workers = min(max_workers_count, len(selected_instances))
                _log_progress(
                    f"attempt {attempt_state.attempt}/{attempts_count}: executing "
                    f"{len(selected_instances)} instance(s) with max_workers={attempt_max_workers}"
                )

                if attempt_max_workers == 1:
                    for instance_index, instance in enumerate(selected_instances, start=1):
                        _record_attempt_completion(
                            _run_instance(
                                attempt=attempt_state.attempt,
                                attempt_dir=attempt_state.attempt_dir,
                                attempts_count=attempts_count,
                                instance_index=instance_index,
                                total_instances=len(selected_instances),
                                instance=instance,
                                adapter=adapter,
                                config=config,
                                layout_run_root=layout.run_root,
                                run_id=layout.run_id,
                                resolved_model=resolved_model,
                                canonical_model_name=model_resolution.canonical_name,
                                model_label=model_label,
                                workspace_cache=workspace_cache,
                                workspace_in_flight=workspace_in_flight,
                                workspace_lock=workspace_lock,
                                workspace_isolation_mode=workspace_isolation_mode,
                            ),
                            attempt_states=attempt_states,
                            total_instances=len(selected_instances),
                            selected_instances=selected_instances,
                            attempts_count=attempts_count,
                            benchmark=config.benchmark,
                            run_id=layout.run_id,
                            evaluation_config=config.evaluation,
                            finalizer_executor=finalizer_executor,
                        )
                else:
                    with ThreadPoolExecutor(max_workers=attempt_max_workers) as executor:
                        futures = [
                            executor.submit(
                                _run_instance,
                                attempt=attempt_state.attempt,
                                attempt_dir=attempt_state.attempt_dir,
                                attempts_count=attempts_count,
                                instance_index=instance_index,
                                total_instances=len(selected_instances),
                                instance=instance,
                                adapter=adapter,
                                config=config,
                                layout_run_root=layout.run_root,
                                run_id=layout.run_id,
                                resolved_model=resolved_model,
                                canonical_model_name=model_resolution.canonical_name,
                                model_label=model_label,
                                workspace_cache=workspace_cache,
                                workspace_in_flight=workspace_in_flight,
                                workspace_lock=workspace_lock,
                                workspace_isolation_mode=workspace_isolation_mode,
                            )
                            for instance_index, instance in enumerate(selected_instances, start=1)
                        ]
                        for future in as_completed(futures):
                            _record_attempt_completion(
                                future.result(),
                                attempt_states=attempt_states,
                                total_instances=len(selected_instances),
                                selected_instances=selected_instances,
                                attempts_count=attempts_count,
                                benchmark=config.benchmark,
                                run_id=layout.run_id,
                                evaluation_config=config.evaluation,
                                finalizer_executor=finalizer_executor,
                            )

        finalization_results: dict[int, _AttemptFinalizationResult] = {}
        finalization_futures = [state.finalization_future for state in attempt_states]
        if any(future is None for future in finalization_futures):
            raise RuntimeError("Internal error: one or more attempts were not finalized")
        for future in as_completed(
            [future for future in finalization_futures if future is not None]
        ):
            result = future.result()
            finalization_results[result.attempt] = result

    success_count_total = sum(state.success_count for state in attempt_states)
    for attempt_state in attempt_states:
        prediction_files.append(attempt_state.prediction_path)
        trace_files.append(attempt_state.trace_path)
        evaluation_results.append(finalization_results[attempt_state.attempt].evaluation_result)

    finished_at = datetime.now(timezone.utc).isoformat()
    summary = {
        "run_id": layout.run_id,
        "benchmark": config.benchmark,
        "config_mode": config.config_mode,
        "condition": config.condition,
        "bitloops_enabled": config.bitloops_enabled,
        "bitloops_sandbox_mode": config.bitloops_sandbox_mode,
        "workspace_isolation_mode": workspace_isolation_mode,
        "dataset_path": str(config.dataset_path),
        "split": config.split,
        "language": config.language,
        "include_repos": config.include_repos,
        "include_instance_ids": config.include_instance_ids,
        "total_instances": len(selected_instances),
        "attempts": attempts_count,
        "max_workers": max_workers_count,
        "parallel_attempts_enabled": parallel_attempts_enabled,
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
    max_workers: int,
    workspace_isolation_mode: str,
    parallel_attempts_enabled: bool,
    dry_run: bool,
    started_at: str,
) -> dict[str, Any]:
    manifest: dict[str, Any] = {
        "run_id": run_id,
        "benchmark": config.benchmark,
        "config_mode": config.config_mode,
        "dataset_path": str(config.dataset_path),
        "split": config.split,
        "language": config.language,
        "condition": config.condition,
        "bitloops_enabled": config.bitloops_enabled,
        "bitloops_sandbox_mode": config.bitloops_sandbox_mode,
        "prompt_context": config.prompt_context,
        "prompt_protocol": config.prompt_protocol,
        "retrieval": {
            "file_source": config.retrieval_file_source,
            "k": config.retrieval_k,
        },
        "prompt_template_version": _prompt_template_version(config.prompt_protocol),
        "prompt_template_hash": _prompt_template_hash(
            prompt_protocol=config.prompt_protocol,
            retrieval_file_source=config.retrieval_file_source,
            retrieval_k=config.retrieval_k,
        ),
        "include_repos": config.include_repos,
        "include_instance_ids": config.include_instance_ids,
        "max_instances": config.max_instances,
        "attempts": attempts,
        "max_workers": max_workers,
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
            "seed": config.model.seed,
        },
        "model_map": config.model_map,
        "workspace": {
            "prepare_workspace": config.prepare_workspace,
            "isolation_mode": workspace_isolation_mode,
            "requested_isolation_mode": config.workspace_isolation_mode,
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
        "parallel_attempts_enabled": parallel_attempts_enabled,
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
    if str(config.model.provider).strip().lower() == "ollama":
        manifest["ollama"] = build_ollama_provider_run_metadata(
            config=config,
            resolved_model_name=str(model_resolution["resolved_name"]),
        )
    if config.agent.id == "opencode":
        manifest["opencode"] = build_opencode_run_metadata()
    if config.agent.id == "codex":
        manifest["codex"] = build_codex_run_metadata()
    return manifest


def _run_instance(
    *,
    attempt: int,
    attempt_dir: Path,
    attempts_count: int,
    instance_index: int,
    total_instances: int,
    instance: BenchmarkInstance,
    adapter: AgentAdapter,
    config: RunConfig,
    layout_run_root: Path,
    run_id: str,
    resolved_model: Any,
    canonical_model_name: str,
    model_label: str,
    workspace_cache: dict[str, WorkspacePrepResult],
    workspace_in_flight: dict[str, threading.Event],
    workspace_lock: threading.Lock,
    workspace_isolation_mode: str,
) -> tuple[int, dict[str, Any], dict[str, Any], bool]:
    status = "ok"
    patch = ""
    metadata: dict[str, Any] = {}
    error_message: str | None = None

    _log_progress(
        f"attempt {attempt}/{attempts_count} "
        f"instance {instance_index}/{total_instances} "
        f"{instance.instance_id}: start"
    )
    workspace_result = _resolve_workspace(
        config=config,
        layout_run_root=layout_run_root,
        instance=instance,
        attempt=attempt,
        run_id=run_id,
        cache=workspace_cache,
        in_flight=workspace_in_flight,
        cache_lock=workspace_lock,
        isolation_mode=workspace_isolation_mode,
    )
    workspace_metadata = {"workspace": workspace_result.to_metadata()}
    bitloops_sandbox = _build_bitloops_task_sandbox(
        config=config,
        workspace_path=workspace_result.workspace_path,
    )
    if bitloops_sandbox is not None:
        workspace_metadata["bitloops_sandbox"] = bitloops_sandbox

    run_context = RunContext(
        attempt=attempt,
        timeout_seconds=config.timeout_seconds,
        workspace_root=workspace_result.workspace_path or Path.cwd(),
        attempt_dir=attempt_dir,
        model=resolved_model,
        canonical_model_name=canonical_model_name,
        run_id=run_id,
        benchmark=config.benchmark,
        condition=config.condition,
        prompt_context=config.prompt_context,
        prompt_protocol=config.prompt_protocol,
        retrieval_file_source=config.retrieval_file_source,
        retrieval_k=config.retrieval_k,
        bitloops_sandbox=bitloops_sandbox,
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
            call_elapsed_ms = result.metadata.get("elapsed_ms")
            elapsed_note = (
                f", elapsed_ms={call_elapsed_ms}"
                if isinstance(call_elapsed_ms, (int, float))
                else ""
            )
            _log_progress(
                f"attempt {attempt}/{attempts_count} "
                f"instance {instance_index}/{total_instances} "
                f"{instance.instance_id}: success{elapsed_note}"
            )
        except Exception as exc:  # noqa: BLE001
            status = "error"
            error_message = str(exc)
            metadata = workspace_metadata
            _log_progress(
                f"attempt {attempt}/{attempts_count} "
                f"instance {instance_index}/{total_instances} "
                f"{instance.instance_id}: error ({error_message})"
            )

    prediction = PredictionRecord(
        instance_id=instance.instance_id,
        model_name_or_path=model_label,
        model_patch=patch,
    ).to_row()
    trace = {
        "instance_id": instance.instance_id,
        "status": status,
        "error": error_message,
        "metadata": metadata,
    }

    return attempt, instance_index - 1, prediction, trace, status == "ok"


def _record_attempt_completion(
    result: tuple[int, int, dict[str, Any], dict[str, Any], bool],
    *,
    attempt_states: list[_AttemptState],
    total_instances: int,
    selected_instances: list[BenchmarkInstance],
    attempts_count: int,
    benchmark: str,
    run_id: str,
    evaluation_config: Any,
    finalizer_executor: ThreadPoolExecutor,
) -> None:
    attempt, item_index, prediction_row, trace_row, succeeded = result
    attempt_state = attempt_states[attempt - 1]
    instance = selected_instances[item_index]
    instance_dir = _instance_artifact_dir(
        attempt_dir=attempt_state.attempt_dir,
        instance_index=item_index + 1,
        instance_id=instance.instance_id,
    )
    _write_instance_artifacts(
        instance_dir=instance_dir,
        prediction_row=prediction_row,
        trace_row=trace_row,
    )

    evaluation_future: Future[AttemptEvaluationResult] | None = None
    if evaluation_config.enabled:
        evaluation_future = finalizer_executor.submit(
            evaluate_predictions_with_harness,
            config=evaluation_config,
            run_id=run_id,
            attempt=attempt,
            benchmark=benchmark,
            prediction_path=instance_dir / "predictions.jsonl",
            attempt_dir=instance_dir,
            run_label=_instance_run_label(
                run_id=run_id,
                attempt=attempt,
                instance_index=item_index + 1,
                instance_id=instance.instance_id,
            ),
        )

    with attempt_state.lock:
        attempt_state.prediction_slots[item_index] = prediction_row
        attempt_state.trace_slots[item_index] = trace_row
        attempt_state.instance_artifact_dirs[item_index] = instance_dir
        attempt_state.instance_evaluation_futures[item_index] = evaluation_future
        attempt_state.completed_count += 1
        if succeeded:
            attempt_state.success_count += 1
        if (
            attempt_state.completed_count == total_instances
            and attempt_state.finalization_future is None
        ):
            prediction_rows = list(attempt_state.prediction_slots)
            trace_rows = list(attempt_state.trace_slots)
            evaluation_futures = list(attempt_state.instance_evaluation_futures)
            instance_dirs = list(attempt_state.instance_artifact_dirs)
            attempt_state.finalization_future = finalizer_executor.submit(
                _finalize_attempt,
                attempt=attempt_state.attempt,
                attempts_count=attempts_count,
                attempt_dir=attempt_state.attempt_dir,
                prediction_path=attempt_state.prediction_path,
                trace_path=attempt_state.trace_path,
                prediction_rows=prediction_rows,
                trace_rows=trace_rows,
                instance_dirs=instance_dirs,
                evaluation_futures=evaluation_futures,
            )


def _finalize_attempt(
    *,
    attempt: int,
    attempts_count: int,
    attempt_dir: Path,
    prediction_path: Path,
    trace_path: Path,
    prediction_rows: list[dict[str, Any] | None],
    trace_rows: list[dict[str, Any] | None],
    instance_dirs: list[Path | None],
    evaluation_futures: list[Future[AttemptEvaluationResult] | None],
) -> _AttemptFinalizationResult:
    if any(item is None for item in prediction_rows):
        raise RuntimeError("Internal error: missing predictions for one or more instances")
    if any(item is None for item in trace_rows):
        raise RuntimeError("Internal error: missing traces for one or more instances")

    _log_progress(
        f"attempt {attempt}/{attempts_count}: finalizing "
        f"{len(prediction_rows)} completed instance(s)"
    )
    predictions = [item for item in prediction_rows if item is not None]
    traces = [item for item in trace_rows if item is not None]
    write_jsonl(prediction_path, predictions)
    write_jsonl(trace_path, traces)

    evaluation_result = _aggregate_instance_evaluations(
        attempt=attempt,
        attempt_dir=attempt_dir,
        instance_dirs=instance_dirs,
        evaluation_futures=evaluation_futures,
    )
    return _AttemptFinalizationResult(
        attempt=attempt,
        evaluation_result=evaluation_result,
    )


def _aggregate_instance_evaluations(
    *,
    attempt: int,
    attempt_dir: Path,
    instance_dirs: list[Path | None],
    evaluation_futures: list[Future[AttemptEvaluationResult] | None],
) -> AttemptEvaluationResult:
    report_path = attempt_dir / "evaluation.json"
    if not any(future is not None for future in evaluation_futures):
        result = AttemptEvaluationResult(
            attempt=attempt,
            status="skipped",
            command=[],
            return_code=None,
            elapsed_ms=None,
            stdout_path=None,
            stderr_path=None,
            report_path=report_path,
            parsed_path=None,
            tasks_path=None,
            parsed_source_file=None,
            task_count=None,
            solved_count=None,
            unsolved_count=None,
            error="evaluation disabled",
        )
        write_json(report_path, result.to_row())
        return result

    instance_results: list[AttemptEvaluationResult] = []
    for future in evaluation_futures:
        if future is None:
            continue
        instance_results.append(future.result())

    task_rows: list[dict[str, Any]] = []
    stdout_chunks: list[str] = []
    stderr_chunks: list[str] = []
    errors: list[str] = []
    commands: list[list[str]] = []
    for index, result in enumerate(instance_results, start=1):
        commands.append(result.command)
        if result.tasks_path is not None and result.tasks_path.exists():
            task_rows.extend(read_jsonl(result.tasks_path))
        if result.stdout_path is not None and result.stdout_path.exists():
            stdout_chunks.append(f"# instance {index}\n")
            stdout_chunks.append(result.stdout_path.read_text(encoding="utf-8"))
            stdout_chunks.append("\n")
        if result.stderr_path is not None and result.stderr_path.exists():
            stderr_chunks.append(f"# instance {index}\n")
            stderr_chunks.append(result.stderr_path.read_text(encoding="utf-8"))
            stderr_chunks.append("\n")
        if result.error:
            errors.append(result.error)

    parsed_path = attempt_dir / "evaluation.parsed.json"
    tasks_path = attempt_dir / "evaluation.tasks.jsonl"
    stdout_path = attempt_dir / "evaluation.stdout.log"
    stderr_path = attempt_dir / "evaluation.stderr.log"
    solved_count = sum(1 for row in task_rows if row.get("status") == "solved")
    unsolved_count = sum(1 for row in task_rows if row.get("status") == "unsolved")
    task_count = len(task_rows)

    parsed_payload = {
        "source_file": None,
        "source_files": [
            str(path.resolve()) for path in instance_dirs if isinstance(path, Path)
        ],
        "task_count": task_count,
        "solved_count": solved_count,
        "unsolved_count": unsolved_count,
    }
    write_json(parsed_path, parsed_payload)
    if task_rows:
        write_jsonl(tasks_path, task_rows)
    else:
        tasks_path = None

    if stdout_chunks:
        stdout_path.write_text("".join(stdout_chunks), encoding="utf-8")
    else:
        stdout_path = None
    if stderr_chunks:
        stderr_path.write_text("".join(stderr_chunks), encoding="utf-8")
    else:
        stderr_path = None

    statuses = {result.status for result in instance_results}
    if statuses == {"skipped"}:
        status = "skipped"
    elif "error" in statuses:
        status = "error"
    else:
        status = "ok"
    return_code = 0 if status != "error" else 1
    elapsed_values = [result.elapsed_ms for result in instance_results if result.elapsed_ms is not None]
    elapsed_ms = sum(elapsed_values) if elapsed_values else None
    summary_result = AttemptEvaluationResult(
        attempt=attempt,
        status=status,
        command=["aggregate-instance-evaluations", *[str(command) for command in commands]],
        return_code=return_code,
        elapsed_ms=elapsed_ms,
        stdout_path=stdout_path,
        stderr_path=stderr_path,
        report_path=report_path,
        parsed_path=parsed_path,
        tasks_path=tasks_path,
        parsed_source_file=None,
        task_count=task_count,
        solved_count=solved_count,
        unsolved_count=unsolved_count,
        error="; ".join(errors) if errors else None,
    )
    write_json(report_path, summary_result.to_row())
    return summary_result


def _write_instance_artifacts(
    *,
    instance_dir: Path,
    prediction_row: dict[str, Any],
    trace_row: dict[str, Any],
) -> None:
    write_jsonl(instance_dir / "predictions.jsonl", [prediction_row])
    write_jsonl(instance_dir / "trace.jsonl", [trace_row])


def _instance_artifact_dir(
    *,
    attempt_dir: Path,
    instance_index: int,
    instance_id: str,
) -> Path:
    return (
        attempt_dir
        / "instances"
        / f"{instance_index:03d}_{_sanitize_path_segment(instance_id, fallback='instance')}"
    )


def _instance_run_label(
    *,
    run_id: str,
    attempt: int,
    instance_index: int,
    instance_id: str,
) -> str:
    slug = _sanitize_path_segment(instance_id, fallback=f"instance-{instance_index:03d}")
    return f"{run_id}-attempt-{attempt:02d}-instance-{instance_index:03d}-{slug}"


def _sanitize_path_segment(value: str | None, *, fallback: str) -> str:
    text = str(value or "").strip()
    if not text:
        return fallback
    sanitized = "".join(char if char.isalnum() or char in {"-", "_"} else "_" for char in text)
    sanitized = sanitized.strip("._")
    return sanitized or fallback


def _resolve_workspace(
    config: RunConfig,
    layout_run_root: Path,
    instance: BenchmarkInstance,
    attempt: int,
    run_id: str,
    cache: dict[str, WorkspacePrepResult],
    in_flight: dict[str, threading.Event],
    cache_lock: threading.Lock,
    isolation_mode: str,
) -> WorkspacePrepResult:
    if not config.prepare_workspace:
        return WorkspacePrepResult(
            status="disabled",
            workspace_path=Path.cwd(),
            repo_url=None,
            elapsed_ms=0,
            isolation_mode=isolation_mode,
            error=None,
        )

    key = _workspace_cache_key(
        instance=instance,
        attempt=attempt,
        run_id=run_id,
        isolation_mode=isolation_mode,
    )
    while True:
        wait_event: threading.Event | None = None
        is_owner = False

        with cache_lock:
            cached = cache.get(key)
            if cached and cached.status in {"prepared", "reused"}:
                return cached
            wait_event = in_flight.get(key)
            if wait_event is None:
                wait_event = threading.Event()
                in_flight[key] = wait_event
                is_owner = True

        if is_owner:
            try:
                result = prepare_instance_workspace(
                    instance=instance,
                    run_root=layout_run_root,
                    repo_url_template=config.repo_url_template,
                    git_bin=config.git_bin,
                    timeout_seconds=config.workspace_timeout_seconds,
                    workspace_root=config.workspace_root,
                    isolation_mode=isolation_mode,
                    run_id=run_id,
                    attempt=attempt,
                )
            except Exception as exc:  # noqa: BLE001
                result = WorkspacePrepResult(
                    status="error",
                    workspace_path=None,
                    repo_url=None,
                    elapsed_ms=0,
                    isolation_mode=isolation_mode,
                    error=str(exc),
                )

            with cache_lock:
                cache[key] = result
                in_flight.pop(key, None)
                wait_event.set()
            return result

        assert wait_event is not None
        wait_event.wait()


def _workspace_cache_key(
    *,
    instance: BenchmarkInstance,
    attempt: int,
    run_id: str,
    isolation_mode: str,
) -> str:
    if isolation_mode == "attempt_scoped":
        return f"{run_id}@{instance.instance_id}@attempt:{attempt}"
    if isolation_mode == "task_scoped":
        return f"{run_id}@{instance.instance_id}"
    return f"{instance.repo}@{instance.base_commit}"


def _resolve_workspace_isolation_mode(
    *,
    requested_mode: str,
    parallel_attempts_enabled: bool,
) -> str:
    if requested_mode == "attempt_scoped":
        return requested_mode
    if parallel_attempts_enabled:
        return "attempt_scoped"
    return requested_mode


def _build_bitloops_task_sandbox(
    *,
    config: RunConfig,
    workspace_path: Path | None,
) -> dict[str, Any] | None:
    if not config.bitloops_enabled or config.bitloops_sandbox_mode != "per_task_daemon":
        return None
    if workspace_path is None:
        return None

    sandbox_root = workspace_path.parent / f"{workspace_path.name}__bitloops"
    home_root = sandbox_root / "home"
    return {
        "mode": config.bitloops_sandbox_mode,
        "sandbox_root": str(sandbox_root),
        "workspace_root": str(workspace_path),
        "home_root": str(home_root),
        "daemon_host": "127.0.0.1",
        "xdg_config_home": str(home_root / "xdg"),
        "xdg_state_home": str(home_root / "xdg-state"),
        "xdg_cache_home": str(home_root / "xdg-cache"),
        "xdg_data_home": str(home_root / "xdg-data"),
        "daemon_stderr_log_path": str(sandbox_root / "daemon.stderr.log"),
    }


def _log_progress(message: str) -> None:
    sys.stderr.write(f"[benchkit] {message}\n")
    sys.stderr.flush()
