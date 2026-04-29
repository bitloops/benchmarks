from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import sys
import threading

from benchkit.common.artifacts import create_run_layout, snapshot_config
from benchkit.common.config import AgentConfig, RunConfig
from benchkit.common.io import write_json, write_jsonl
from benchkit.swebench.agents.base import AgentAdapter, RunContext
from benchkit.swebench.agents.registry import build_agent_adapter
from benchkit.swebench.codex_config_metadata import build_codex_run_metadata
from benchkit.swebench.dataset import filter_instances, load_instances
from benchkit.swebench.evaluation import AttemptEvaluationResult, evaluate_predictions_with_harness
from benchkit.swebench.model_mapper import resolve_model_name
from benchkit.swebench.opencode_config_metadata import build_opencode_run_metadata
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


@dataclass(slots=True)
class _AttemptState:
    attempt: int
    attempt_dir: Path
    prediction_path: Path
    trace_path: Path
    prediction_slots: list[dict[str, Any] | None]
    trace_slots: list[dict[str, Any] | None]
    success_count: int = 0


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
    success_count_total = 0

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
            )
        )

    if parallel_attempts_enabled:
        total_calls = len(selected_instances) * attempts_count
        job_max_workers = min(max_workers_count, total_calls)
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
                (
                    attempt,
                    item_index,
                    prediction_row,
                    trace_row,
                    succeeded,
                ) = future.result()
                attempt_state = attempt_states[attempt - 1]
                attempt_state.prediction_slots[item_index] = prediction_row
                attempt_state.trace_slots[item_index] = trace_row
                if succeeded:
                    attempt_state.success_count += 1
    else:
        for attempt_state in attempt_states:
            attempt_max_workers = min(max_workers_count, len(selected_instances))
            _log_progress(
                f"attempt {attempt_state.attempt}/{attempts_count}: executing "
                f"{len(selected_instances)} instance(s) with max_workers={attempt_max_workers}"
            )

            if attempt_max_workers == 1:
                for instance_index, instance in enumerate(selected_instances, start=1):
                    (
                        attempt,
                        item_index,
                        prediction_row,
                        trace_row,
                        succeeded,
                    ) = _run_instance(
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
                    _ = attempt
                    attempt_state.prediction_slots[item_index] = prediction_row
                    attempt_state.trace_slots[item_index] = trace_row
                    if succeeded:
                        attempt_state.success_count += 1
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
                        (
                            attempt,
                            item_index,
                            prediction_row,
                            trace_row,
                            succeeded,
                        ) = future.result()
                        _ = attempt
                        attempt_state.prediction_slots[item_index] = prediction_row
                        attempt_state.trace_slots[item_index] = trace_row
                        if succeeded:
                            attempt_state.success_count += 1

    for attempt_state in attempt_states:
        if any(item is None for item in attempt_state.prediction_slots):
            raise RuntimeError("Internal error: missing predictions for one or more instances")
        if any(item is None for item in attempt_state.trace_slots):
            raise RuntimeError("Internal error: missing traces for one or more instances")

        predictions = [item for item in attempt_state.prediction_slots if item is not None]
        traces = [item for item in attempt_state.trace_slots if item is not None]
        success_count_total += attempt_state.success_count

        write_jsonl(attempt_state.prediction_path, predictions)
        write_jsonl(attempt_state.trace_path, traces)
        prediction_files.append(attempt_state.prediction_path)
        trace_files.append(attempt_state.trace_path)

        evaluation_result = evaluate_predictions_with_harness(
            config=config.evaluation,
            run_id=layout.run_id,
            attempt=attempt_state.attempt,
            benchmark=config.benchmark,
            prediction_path=attempt_state.prediction_path,
            attempt_dir=attempt_state.attempt_dir,
        )
        evaluation_results.append(evaluation_result)

    finished_at = datetime.now(timezone.utc).isoformat()
    total_calls = len(selected_instances) * attempts_count
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
