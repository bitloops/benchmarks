from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
import json
import os
import shutil
import subprocess
import time
from collections.abc import Iterable

from benchkit.common.config import EvaluationConfig
from benchkit.common.io import write_json, write_jsonl
from benchkit.contextbench.evaluation import (
    build_contextbench_gold_jsonl,
    build_contextbench_prediction_jsonl,
    parse_contextbench_results_jsonl,
)
from benchkit.swebench.dataset import BENCHMARK_CONTEXTBENCH_VERIFIED, BENCHMARK_PRO
from benchkit.swebench.pro_evaluation import (
    convert_predictions_jsonl_to_pro_patches,
    normalize_swebench_pro_eval_results,
)


@dataclass(slots=True)
class AttemptEvaluationResult:
    attempt: int
    status: str
    command: list[str]
    return_code: int | None
    elapsed_ms: int | None
    stdout_path: Path | None
    stderr_path: Path | None
    report_path: Path
    parsed_path: Path | None
    tasks_path: Path | None
    parsed_source_file: Path | None
    task_count: int | None
    solved_count: int | None
    unsolved_count: int | None
    error: str | None

    def to_row(self) -> dict[str, Any]:
        return {
            "attempt": self.attempt,
            "status": self.status,
            "command": self.command,
            "return_code": self.return_code,
            "elapsed_ms": self.elapsed_ms,
            "stdout_path": str(self.stdout_path) if self.stdout_path else None,
            "stderr_path": str(self.stderr_path) if self.stderr_path else None,
            "report_path": str(self.report_path),
            "parsed_path": str(self.parsed_path) if self.parsed_path else None,
            "tasks_path": str(self.tasks_path) if self.tasks_path else None,
            "parsed_source_file": (
                str(self.parsed_source_file) if self.parsed_source_file else None
            ),
            "task_count": self.task_count,
            "solved_count": self.solved_count,
            "unsolved_count": self.unsolved_count,
            "error": self.error,
        }


def evaluate_predictions_with_harness(
    config: EvaluationConfig,
    run_id: str,
    attempt: int,
    benchmark: str,
    dataset_path: Path | None,
    prediction_path: Path,
    attempt_dir: Path,
    run_label: str | None = None,
    artifact_dir: Path | None = None,
) -> AttemptEvaluationResult:
    effective_artifact_dir = (artifact_dir or attempt_dir).resolve()
    effective_run_label = run_label or f"{run_id}-attempt-{attempt:02d}"
    report_path = effective_artifact_dir / "evaluation.json"
    if not config.enabled:
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

    pro_patch_path: Path | None = None
    pro_raw_results_path: Path | None = None
    contextbench_pred_path: Path | None = None
    contextbench_results_path: Path | None = None
    contextbench_gold_path: Path | None = None
    if benchmark == BENCHMARK_PRO:
        pro_patch_path = (
            effective_artifact_dir
            / f"{effective_run_label}.swebench_pro.patches.json"
        )
        convert_predictions_jsonl_to_pro_patches(
            prediction_path=prediction_path,
            output_path=pro_patch_path,
            prefix=effective_run_label,
        )
        pro_raw_results_path = effective_artifact_dir / "eval_results.json"
    elif benchmark == BENCHMARK_CONTEXTBENCH_VERIFIED:
        if dataset_path is None:
            raise ValueError("run.dataset_path is required for ContextBench evaluation")
        preflight_error = _contextbench_dependency_preflight(
            python_bin=_resolve_python_bin(config.python_bin),
            contextbench_repo=config.contextbench_repo,
            env=os.environ.copy(),
        )
        if preflight_error is not None:
            result = AttemptEvaluationResult(
                attempt=attempt,
                status="error",
                command=[],
                return_code=1,
                elapsed_ms=None,
                stdout_path=None,
                stderr_path=None,
                report_path=report_path,
                parsed_path=None,
                tasks_path=None,
                parsed_source_file=None,
                task_count=0,
                solved_count=0,
                unsolved_count=0,
                error=preflight_error,
            )
            write_json(report_path, result.to_row())
            return result
        contextbench_pred_path = (
            effective_artifact_dir / f"{effective_run_label}.contextbench.predictions.jsonl"
        )
        contextbench_gold_path = (
            effective_artifact_dir / f"{effective_run_label}.contextbench.gold.jsonl"
        )
        build_contextbench_gold_jsonl(
            dataset_path=dataset_path,
            output_path=contextbench_gold_path,
        )
        trace_path = attempt_dir / "trace.jsonl"
        build_contextbench_prediction_jsonl(
            benchmark=benchmark,
            dataset_path=dataset_path,
            prediction_path=prediction_path,
            trace_path=trace_path,
            output_path=contextbench_pred_path,
        )
        contextbench_results_path = (
            effective_artifact_dir / f"{effective_run_label}.contextbench.results.jsonl"
        )

    command_dataset_path = contextbench_gold_path or dataset_path
    command = _build_evaluation_command(
        config=config,
        run_id=run_id,
        attempt=attempt,
        benchmark=benchmark,
        dataset_path=command_dataset_path,
        prediction_path=prediction_path,
        attempt_dir=attempt_dir,
        pro_patch_path=pro_patch_path,
        contextbench_pred_path=contextbench_pred_path,
        contextbench_results_path=contextbench_results_path,
        run_label=effective_run_label,
        artifact_dir=effective_artifact_dir,
    )

    stdout_path = effective_artifact_dir / "evaluation.stdout.log"
    stderr_path = effective_artifact_dir / "evaluation.stderr.log"
    if benchmark == BENCHMARK_CONTEXTBENCH_VERIFIED and config.contextbench_repo is not None:
        cwd = config.contextbench_repo.resolve()
    else:
        cwd = config.swebench_repo.resolve() if config.swebench_repo else None
    env = _build_evaluation_env(cwd)

    try:
        start = time.time()
        completed = subprocess.run(
            command,
            text=True,
            capture_output=True,
            timeout=config.timeout_seconds,
            cwd=str(cwd) if cwd else None,
            env=env,
            check=False,
        )
        elapsed_ms = int((time.time() - start) * 1000)
    except Exception as exc:  # noqa: BLE001
        result = AttemptEvaluationResult(
            attempt=attempt,
            status="error",
            command=command,
            return_code=None,
            elapsed_ms=None,
            stdout_path=stdout_path,
            stderr_path=stderr_path,
            report_path=report_path,
            parsed_path=None,
            tasks_path=None,
            parsed_source_file=None,
            task_count=None,
            solved_count=None,
            unsolved_count=None,
            error=str(exc),
        )
        write_json(report_path, result.to_row())
        return result

    stdout_path.write_text(completed.stdout, encoding="utf-8")
    stderr_path.write_text(completed.stderr, encoding="utf-8")
    if (
        benchmark == BENCHMARK_PRO
        and completed.returncode == 0
        and pro_raw_results_path is not None
        and pro_raw_results_path.exists()
    ):
        normalized_path = (
            effective_artifact_dir
            / f"{effective_run_label}.swebench_pro.results.json"
        )
        normalize_swebench_pro_eval_results(
            raw_results_path=pro_raw_results_path,
            output_path=normalized_path,
        )

    if benchmark == BENCHMARK_CONTEXTBENCH_VERIFIED and contextbench_results_path is not None:
        parsed = parse_contextbench_results_jsonl(
            result_path=contextbench_results_path,
            parsed_path=effective_artifact_dir / "evaluation.parsed.json",
            tasks_path=effective_artifact_dir / "evaluation.tasks.jsonl",
            prediction_path=contextbench_pred_path,
        )
    else:
        parsed = _parse_evaluation_outputs(
            config=config,
            run_id=run_id,
            attempt=attempt,
            run_label=effective_run_label,
            artifact_dir=effective_artifact_dir,
            swebench_cwd=cwd if cwd else Path.cwd(),
            started_epoch=start,
        )

    status = "ok" if completed.returncode == 0 else "error"
    result = AttemptEvaluationResult(
        attempt=attempt,
        status=status,
        command=command,
        return_code=completed.returncode,
        elapsed_ms=elapsed_ms,
        stdout_path=stdout_path,
        stderr_path=stderr_path,
        report_path=report_path,
        parsed_path=parsed.get("parsed_path"),
        tasks_path=parsed.get("tasks_path"),
        parsed_source_file=parsed.get("source_file"),
        task_count=parsed.get("task_count"),
        solved_count=parsed.get("solved_count"),
        unsolved_count=parsed.get("unsolved_count"),
        error=None if status == "ok" else "harness command exited non-zero",
    )
    write_json(report_path, result.to_row())
    return result


def _build_evaluation_env(cwd: Path | None) -> dict[str, str]:
    env = os.environ.copy()
    if cwd:
        existing = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = f"{cwd}:{existing}" if existing else str(cwd)
    _ensure_docker_credential_helper_on_path(env)
    return env


def _ensure_docker_credential_helper_on_path(env: dict[str, str]) -> None:
    helper_name = _docker_credential_helper_name(env)
    if not helper_name:
        return
    helper_path = shutil.which(helper_name, path=env.get("PATH"))
    if helper_path:
        return

    helper_dir = _docker_desktop_bin_dir()
    if helper_dir is None:
        return
    candidate = helper_dir / helper_name
    if not candidate.exists():
        return

    existing = env.get("PATH", "")
    env["PATH"] = f"{helper_dir}{os.pathsep}{existing}" if existing else str(helper_dir)


def _docker_credential_helper_name(env: dict[str, str]) -> str | None:
    config = _load_docker_config(env)
    store = config.get("credsStore")
    if not isinstance(store, str) or not store.strip():
        return None
    return f"docker-credential-{store.strip()}"


def _load_docker_config(env: dict[str, str]) -> dict[str, Any]:
    config_dir = env.get("DOCKER_CONFIG")
    if config_dir:
        config_path = Path(config_dir).expanduser() / "config.json"
    else:
        config_path = Path(env.get("HOME", "~")).expanduser() / ".docker" / "config.json"
    if not config_path.exists():
        return {}
    try:
        payload = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _docker_desktop_bin_dir() -> Path | None:
    candidate = Path("/Applications/Docker.app/Contents/Resources/bin")
    return candidate if candidate.exists() else None


def _build_evaluation_command(
    config: EvaluationConfig,
    run_id: str,
    attempt: int,
    benchmark: str,
    dataset_path: Path | None,
    prediction_path: Path,
    attempt_dir: Path,
    pro_patch_path: Path | None = None,
    contextbench_pred_path: Path | None = None,
    contextbench_results_path: Path | None = None,
    *,
    run_label: str | None = None,
    artifact_dir: Path | None = None,
) -> list[str]:
    python_bin = _resolve_python_bin(config.python_bin)
    effective_artifact_dir = (artifact_dir or attempt_dir).resolve()
    effective_run_label = run_label or f"{run_id}-attempt-{attempt:02d}"
    template_context = {
        "run_id": run_id,
        "run_label": effective_run_label,
        "attempt": attempt,
        "benchmark": benchmark,
        "predictions_path": str(prediction_path.resolve()),
        "attempt_dir": str(effective_artifact_dir),
        "dataset_name": config.dataset_name or "",
        "split": config.split or "",
        "max_workers": config.max_workers,
        "python_bin": python_bin,
        "swebench_repo": str(config.swebench_repo.resolve()) if config.swebench_repo else "",
        "dataset_path": str(dataset_path.resolve()) if dataset_path else "",
        "swebench_pro_patch_path": (
            str(pro_patch_path.resolve()) if pro_patch_path else ""
        ),
        "contextbench_repo": (
            str(config.contextbench_repo.resolve()) if config.contextbench_repo else ""
        ),
        "contextbench_pred_path": (
            str(contextbench_pred_path.resolve()) if contextbench_pred_path else ""
        ),
        "contextbench_results_path": (
            str(contextbench_results_path.resolve()) if contextbench_results_path else ""
        ),
    }

    if config.command_template:
        try:
            return [item.format(**template_context) for item in config.command_template]
        except KeyError as exc:
            raise ValueError(
                f"evaluation.command_template uses unknown placeholder: {exc}"
            ) from exc

    if benchmark == BENCHMARK_PRO:
        return _build_swebench_pro_evaluation_command(
            config=config,
            dataset_path=dataset_path,
            pro_patch_path=pro_patch_path,
            artifact_dir=effective_artifact_dir,
        )
    if benchmark == BENCHMARK_CONTEXTBENCH_VERIFIED:
        return _build_contextbench_evaluation_command(
            config=config,
            dataset_path=dataset_path,
            contextbench_pred_path=contextbench_pred_path,
            contextbench_results_path=contextbench_results_path,
            artifact_dir=effective_artifact_dir,
        )

    if not config.dataset_name:
        raise ValueError(
            "evaluation.dataset_name is required for default harness invocation"
        )

    command: list[str] = [
        python_bin,
        "-m",
        "swebench.harness.run_evaluation",
        "--dataset_name",
        config.dataset_name,
        "--predictions_path",
        str(prediction_path.resolve()),
        "--max_workers",
        str(config.max_workers),
        "--run_id",
        effective_run_label,
        "--report_dir",
        str(effective_artifact_dir),
    ]
    if config.split:
        command.extend(["--split", config.split])
    command.extend(config.extra_args)
    return command


def _build_swebench_pro_evaluation_command(
    *,
    config: EvaluationConfig,
    dataset_path: Path | None,
    pro_patch_path: Path | None,
    artifact_dir: Path,
) -> list[str]:
    if dataset_path is None:
        raise ValueError(
            "run.dataset_path is required for default SWE-bench Pro evaluation"
        )
    if pro_patch_path is None:
        raise ValueError(
            "predictions conversion path is required for default SWE-bench Pro evaluation"
        )
    if config.swebench_repo is None:
        raise ValueError(
            "evaluation.swebench_repo must point to scaleapi/SWE-bench_Pro-os for swebench_pro"
        )

    swebench_pro_repo = config.swebench_repo.resolve()
    eval_script = swebench_pro_repo / "swe_bench_pro_eval.py"
    if not eval_script.exists():
        raise ValueError(f"SWE-bench Pro evaluator script not found: {eval_script}")

    scripts_dir = (
        config.pro_scripts_dir.resolve()
        if config.pro_scripts_dir is not None
        else swebench_pro_repo / "run_scripts"
    )

    command: list[str] = [
        _resolve_python_bin(config.python_bin),
        str(eval_script),
        "--raw_sample_path",
        str(dataset_path.resolve()),
        "--patch_path",
        str(pro_patch_path.resolve()),
        "--output_dir",
        str(artifact_dir.resolve()),
        "--scripts_dir",
        str(scripts_dir),
        "--dockerhub_username",
        config.pro_dockerhub_username,
        "--num_workers",
        str(config.max_workers),
    ]
    if config.pro_use_local_docker:
        command.append("--use_local_docker")
    if config.pro_block_network:
        command.append("--block_network")
    if config.pro_docker_platform:
        command.extend(["--docker_platform", config.pro_docker_platform])
    command.extend(config.extra_args)
    return command


def _build_contextbench_evaluation_command(
    *,
    config: EvaluationConfig,
    dataset_path: Path | None,
    contextbench_pred_path: Path | None,
    contextbench_results_path: Path | None,
    artifact_dir: Path,
) -> list[str]:
    if dataset_path is None:
        raise ValueError("run.dataset_path is required for ContextBench evaluation")
    if contextbench_pred_path is None:
        raise ValueError("converted ContextBench prediction path is required")
    if contextbench_results_path is None:
        raise ValueError("ContextBench results output path is required")
    if config.contextbench_repo is None:
        raise ValueError(
            "evaluation.contextbench_repo must point to a local ContextBench checkout"
        )
    contextbench_repo = config.contextbench_repo.resolve()
    cache_dir = (
        config.contextbench_cache_dir.resolve()
        if config.contextbench_cache_dir is not None
        else artifact_dir / "contextbench_repos"
    )
    command: list[str] = [
        _resolve_python_bin(config.python_bin),
        "-m",
        "contextbench.evaluate",
        "--gold",
        str(dataset_path.resolve()),
        "--pred",
        str(contextbench_pred_path.resolve()),
        "--cache",
        str(cache_dir.resolve()),
        "--out",
        str(contextbench_results_path.resolve()),
    ]
    command.extend(config.extra_args)
    return command


def _resolve_python_bin(python_bin: str) -> str:
    text = str(python_bin).strip()
    if not text:
        return python_bin
    candidate = Path(text).expanduser()
    if candidate.is_absolute():
        return str(candidate)
    # Resolve relative executable paths against the current run cwd so they
    # remain valid even when evaluator subprocess cwd points elsewhere.
    if "/" in text or text.startswith("."):
        # Use absolute path normalization without dereferencing symlinks so a
        # venv shim like ./.venv/bin/python is preserved as-is.
        return str((Path.cwd() / candidate).absolute())
    return text


def _contextbench_dependency_preflight(
    *,
    python_bin: str,
    contextbench_repo: Path | None,
    env: dict[str, str],
) -> str | None:
    if contextbench_repo is None:
        return "evaluation.contextbench_repo must point to a local ContextBench checkout"
    repo = contextbench_repo.resolve()
    if not repo.exists():
        return f"ContextBench repo not found: {repo}"

    check_cmd = [
        python_bin,
        "-c",
        (
            "import importlib.util,sys; "
            "mods=['tree_sitter','tree_sitter_languages']; "
            "missing=[m for m in mods if importlib.util.find_spec(m) is None]; "
            "print(','.join(missing)); "
            "sys.exit(1 if missing else 0)"
        ),
    ]
    completed = subprocess.run(
        check_cmd,
        text=True,
        capture_output=True,
        cwd=str(repo),
        env=env,
        check=False,
    )
    if completed.returncode == 0:
        return None
    missing = (completed.stdout or "").strip() or "tree_sitter,tree_sitter_languages"
    return (
        "ContextBench evaluation preflight failed: missing dependencies "
        f"({missing}). Install into benchmark venv with "
        "`./.venv/bin/python -m pip install -r third_party/ContextBench/requirements.txt`."
    )


def _parse_evaluation_outputs(
    config: EvaluationConfig,
    run_id: str,
    attempt: int,
    run_label: str,
    artifact_dir: Path,
    swebench_cwd: Path,
    started_epoch: float,
) -> dict[str, Any]:
    source_file = _find_evaluation_source_file(
        config=config,
        run_id=run_id,
        attempt=attempt,
        run_label=run_label,
        artifact_dir=artifact_dir,
        swebench_cwd=swebench_cwd,
        started_epoch=started_epoch,
    )
    if not source_file:
        return {}

    try:
        payload = json.loads(source_file.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}

    tasks = _extract_task_results(payload)
    parsed_path = artifact_dir / "evaluation.parsed.json"
    tasks_path = artifact_dir / "evaluation.tasks.jsonl"

    solved_count = sum(1 for row in tasks if row.get("status") == "solved")
    unsolved_count = sum(1 for row in tasks if row.get("status") == "unsolved")
    parsed_payload = {
        "source_file": str(source_file),
        "task_count": len(tasks),
        "solved_count": solved_count,
        "unsolved_count": unsolved_count,
    }
    write_json(parsed_path, parsed_payload)
    if tasks:
        write_jsonl(tasks_path, tasks)
    else:
        tasks_path = None

    return {
        "source_file": source_file,
        "parsed_path": parsed_path,
        "tasks_path": tasks_path,
        "task_count": len(tasks),
        "solved_count": solved_count,
        "unsolved_count": unsolved_count,
    }


def _find_evaluation_source_file(
    config: EvaluationConfig,
    run_id: str,
    attempt: int,
    run_label: str,
    artifact_dir: Path,
    swebench_cwd: Path,
    started_epoch: float,
) -> Path | None:
    if config.result_json_path_template:
        candidate = Path(
            config.result_json_path_template.format(
                run_id=run_id,
                attempt=attempt,
                run_label=run_label,
                attempt_dir=str(artifact_dir.resolve()),
                swebench_repo=str(swebench_cwd),
            )
        )
        if not candidate.is_absolute():
            candidate = swebench_cwd / candidate
        candidate = candidate.resolve()
        if candidate.exists():
            return candidate

    # Prefer reports written directly into attempt_dir.
    attempt_candidates = sorted(
        artifact_dir.glob("*.json"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    for path in attempt_candidates:
        if run_label in path.name:
            return path

    # SWE-bench often writes the final report JSON directly in the harness cwd.
    root_candidates = sorted(
        swebench_cwd.glob("*.json"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    for path in root_candidates:
        if run_label in path.name:
            return path

    eval_dir = swebench_cwd / "evaluation_results"
    if not eval_dir.exists():
        for path in root_candidates:
            if path.stat().st_mtime >= started_epoch - 2:
                return path
        return None
    candidates = sorted(
        eval_dir.rglob("*.json"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    for path in candidates:
        if run_label in str(path):
            return path
    for path in candidates:
        if path.stat().st_mtime >= started_epoch - 2:
            return path
    return None


def _extract_task_results(payload: Any) -> list[dict[str, Any]]:
    records = list(_iter_candidate_records(payload))
    records.extend(_extract_summary_id_records(payload))
    deduped: dict[str, dict[str, Any]] = {}
    for record in records:
        instance_id = record.get("instance_id")
        if not instance_id:
            continue
        deduped[instance_id] = record
    return list(deduped.values())


def _extract_summary_id_records(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []

    submitted = _string_id_set(payload.get("submitted_ids"))
    if not submitted:
        return []
    resolved = _string_id_set(payload.get("resolved_ids"))
    unresolved = _string_id_set(payload.get("unresolved_ids"))
    errors = _string_id_set(payload.get("error_ids"))

    output: list[dict[str, Any]] = []
    for instance_id in sorted(submitted):
        if instance_id in resolved:
            status = "solved"
            resolved_bool: bool | None = True
        elif instance_id in unresolved:
            status = "unsolved"
            resolved_bool = False
        elif instance_id in errors:
            status = "invalid"
            resolved_bool = None
        else:
            status = "unsolved"
            resolved_bool = False
        output.append(
            {
                "instance_id": instance_id,
                "status": status,
                "resolved": resolved_bool,
                "raw": {
                    "source": "summary_ids",
                    "submitted": True,
                    "resolved": instance_id in resolved,
                    "unresolved": instance_id in unresolved,
                    "error": instance_id in errors,
                },
            }
        )
    return output


def _iter_candidate_records(payload: Any) -> Iterable[dict[str, Any]]:
    if isinstance(payload, list):
        for item in payload:
            yield from _iter_candidate_records(item)
        return

    if isinstance(payload, dict):
        for key, value in payload.items():
            if isinstance(value, dict) and _looks_like_instance_id(key):
                normalized = _normalize_task_record(value, fallback_instance_id=key)
                if normalized:
                    yield normalized

        for key in (
            "results",
            "instances",
            "instance_results",
            "per_instance",
            "evaluation_results",
            "reports",
        ):
            if key in payload:
                yield from _iter_candidate_records(payload[key])

        normalized = _normalize_task_record(payload, fallback_instance_id=None)
        if normalized:
            yield normalized

        for value in payload.values():
            if isinstance(value, (dict, list)):
                yield from _iter_candidate_records(value)


def _normalize_task_record(
    record: dict[str, Any],
    fallback_instance_id: str | None,
) -> dict[str, Any] | None:
    instance_id = (
        _first_str(record, ("instance_id", "id", "task_id", "sample_id"))
        or fallback_instance_id
    )
    if not instance_id:
        return None

    resolved = _extract_bool(record, ("resolved", "is_resolved", "success", "passed"))
    status = _extract_status(record, resolved)
    return {
        "instance_id": instance_id,
        "status": status,
        "resolved": resolved,
        "raw": record,
    }


def _extract_status(record: dict[str, Any], resolved: bool | None) -> str:
    status_raw = _first_str(record, ("status", "result", "outcome"))
    if status_raw:
        text = status_raw.strip().lower()
        if "timeout" in text:
            return "timeout"
        if text in {"resolved", "solved", "passed", "success", "succeeded"}:
            return "solved"
        if text in {"failed", "unsolved", "unresolved", "error"}:
            return "unsolved"
    if resolved is True:
        return "solved"
    if resolved is False:
        return "unsolved"
    return "unknown"


def _extract_bool(record: dict[str, Any], keys: tuple[str, ...]) -> bool | None:
    for key in keys:
        if key not in record:
            continue
        value = record[key]
        if isinstance(value, bool):
            return value
        if isinstance(value, int):
            return bool(value)
        if isinstance(value, str):
            text = value.strip().lower()
            if text in {"true", "1", "yes", "y", "passed", "success"}:
                return True
            if text in {"false", "0", "no", "n", "failed"}:
                return False
    return None


def _first_str(record: dict[str, Any], keys: tuple[str, ...]) -> str | None:
    for key in keys:
        if key not in record:
            continue
        value = record[key]
        if isinstance(value, str):
            text = value.strip()
            if text:
                return text
    return None


def _string_id_set(value: Any) -> set[str]:
    if not isinstance(value, list):
        return set()
    output: set[str] = set()
    for item in value:
        if isinstance(item, str):
            text = item.strip()
            if text:
                output.add(text)
    return output


def _looks_like_instance_id(value: str) -> bool:
    text = value.strip()
    return "__" in text or text.startswith("task_")
