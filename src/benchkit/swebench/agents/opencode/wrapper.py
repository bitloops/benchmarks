#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess

from ..common import (
    AgentCommandResult,
    add_bitloops_wrapper_args,
    call_command,
    emit_success,
    env_args,
    env_flag,
    extract_tool_invocation_sequence,
    extract_tool_invocations_curated,
    extract_tool_invocations_raw,
    extract_tool_usage_breakdown,
    extract_usage_metrics,
    fatal_error,
    load_hook_metrics,
    merge_metric_metadata,
    parse_agent_payload,
    prompt_template_metadata,
    read_payload_from_stdin,
    render_task_prompt,
    resolve_bitloops_setup_timeout_seconds as common_resolve_bitloops_setup_timeout_seconds,
    run_agent_wrapper,
    summarize_command_failure,
    summarize_tool_invocation_counts,
)
from .runtime import (
    build_ollama_opencode_provider_overlay as _runtime_build_ollama_opencode_provider_overlay,
    build_opencode_invocation_config as _runtime_build_opencode_invocation_config,
    deep_merge_opencode_dicts as _runtime_deep_merge_opencode_dicts,
    load_opencode_config_file as _runtime_load_opencode_config_file,
    merge_opencode_config_content as _runtime_merge_opencode_config_content,
    normalize_opencode_model_reference as _runtime_normalize_opencode_model_reference,
    normalize_opencode_provider_id as _runtime_normalize_opencode_provider_id,
    resolve_opencode_timeout_seconds as _runtime_resolve_opencode_timeout_seconds,
    resolve_repo_opencode_config_path as _runtime_resolve_repo_opencode_config_path,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    add_bitloops_wrapper_args(parser)
    args, _ = parser.parse_known_args()
    return args


def _resolve_bitloops_setup_timeout_seconds(payload: dict[str, object]) -> int:
    return common_resolve_bitloops_setup_timeout_seconds(payload)


_resolve_opencode_timeout_seconds = _runtime_resolve_opencode_timeout_seconds
_normalize_opencode_provider_id = _runtime_normalize_opencode_provider_id
_normalize_opencode_model_reference = _runtime_normalize_opencode_model_reference
_resolve_repo_opencode_config_path = _runtime_resolve_repo_opencode_config_path
_load_opencode_config_file = _runtime_load_opencode_config_file
_deep_merge_dicts = _runtime_deep_merge_opencode_dicts
_merge_opencode_config_content = _runtime_merge_opencode_config_content
_build_opencode_invocation_config = _runtime_build_opencode_invocation_config
_build_ollama_opencode_provider_overlay = _runtime_build_ollama_opencode_provider_overlay


def _resolve_attempt_dir(payload: dict[str, object]) -> Path | None:
    run = payload.get("run", {})
    if not isinstance(run, dict):
        return None
    raw_attempt_dir = run.get("attempt_dir")
    if not isinstance(raw_attempt_dir, str) or not raw_attempt_dir.strip():
        return None
    return Path(raw_attempt_dir).expanduser()


def _sanitize_instance_id(instance_id: object) -> str:
    text = str(instance_id or "").strip()
    if not text:
        return "unknown-instance"
    sanitized = re.sub(r"[^A-Za-z0-9_.-]+", "_", text)
    return sanitized or "unknown-instance"


def _resolve_raw_output_paths(payload: dict[str, object]) -> tuple[Path | None, Path | None]:
    attempt_dir = _resolve_attempt_dir(payload)
    if attempt_dir is None:
        return None, None
    raw_dir = attempt_dir / "agent_raw"
    instance_stem = _sanitize_instance_id(payload.get("instance_id"))
    return (
        raw_dir / f"{instance_stem}.opencode.stdout.jsonl",
        raw_dir / f"{instance_stem}.opencode.stderr.log",
    )


def _persist_raw_opencode_output(
    *,
    payload: dict[str, object],
    stdout: str,
    stderr: str,
) -> tuple[str | None, str | None]:
    stdout_path, stderr_path = _resolve_raw_output_paths(payload)
    if stdout_path is None or stderr_path is None:
        return None, None
    stdout_path.parent.mkdir(parents=True, exist_ok=True)
    stdout_path.write_text(stdout, encoding="utf-8")
    stderr_path.write_text(stderr, encoding="utf-8")
    return str(stdout_path), str(stderr_path)


def _coerce_command_output(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def _run_condition(payload: dict[str, object]) -> str:
    run = payload.get("run", {})
    if not isinstance(run, dict):
        return ""
    return str(run.get("condition", "")).strip().lower()


def _should_require_tool_invocations(payload: dict[str, object]) -> bool:
    if "BENCHKIT_REQUIRE_OPENCODE_TOOL_EVENTS" in os.environ:
        return env_flag("BENCHKIT_REQUIRE_OPENCODE_TOOL_EVENTS", default=False)
    if "BENCHKIT_REQUIRE_EXACT_TOOLS" in os.environ:
        return env_flag("BENCHKIT_REQUIRE_EXACT_TOOLS", default=False)
    return _run_condition(payload) == "with_bitloops"


def _resolve_missing_tool_capture_error(
    *,
    payload: dict[str, object],
    tool_invocations_raw: list[dict[str, object]],
    tool_usage_breakdown: dict[str, int],
) -> str | None:
    if not _should_require_tool_invocations(payload):
        return None
    if tool_invocations_raw:
        return None
    if tool_usage_breakdown:
        return (
            "OpenCode finished a Bitloops run without any captured per-tool invocations, "
            "even though aggregated tool usage metrics were present."
        )
    return (
        "OpenCode finished a Bitloops run without any captured tool invocations. "
        "The prompt requires using `bitloops devql` first, so this run should be treated as invalid."
    )


def _clip_text(text: str, limit: int = 4000) -> str:
    stripped = text.strip()
    if len(stripped) <= limit:
        return stripped
    return f"{stripped[:limit]}\n… (truncated)"


def _summarize_opencode_stdout_errors(stdout: str) -> str | None:
    """Parse OpenCode ``--format json`` JSONL for top-level ``type: error`` events (e.g. API 401)."""
    summaries: list[str] = []
    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(obj, dict) or obj.get("type") != "error":
            continue
        err = obj.get("error")
        if isinstance(err, dict):
            name = str(err.get("name", "")).strip()
            data = err.get("data")
            part = ""
            if isinstance(data, dict):
                msg = str(data.get("message", "")).strip()
                code = data.get("statusCode")
                if code is not None and msg:
                    part = f"HTTP {code}: {msg}"
                elif msg:
                    part = msg
                elif code is not None:
                    part = f"HTTP {code}"
            if not part:
                part = name or str(err)[:400]
            elif name and name not in part:
                part = f"{name}: {part}"
        else:
            part = str(err)[:400] if err is not None else "unknown error"
        if part:
            summaries.append(part)
    if not summaries:
        return None
    return "; ".join(summaries[:3])


def _ensure_nonempty_patch_or_exit(
    *,
    patch: str,
    return_code: int,
    patch_source: str,
    stdout: str,
    stderr: str,
    command: list[str],
    workspace: Path,
    raw_stdout_path: str | None,
    raw_stderr_path: str | None,
) -> None:
    if patch.strip():
        return
    if env_flag("BENCHKIT_ALLOW_EMPTY_OPENCODE_PATCH", default=False):
        return
    stream_error = _summarize_opencode_stdout_errors(stdout)
    if stream_error:
        fatal_error(
            "opencode failed before producing edits (model or API error)",
            details={
                "error_summary": stream_error,
                "hint": (
                    "OpenCode returned JSONL error event(s) instead of completing the task. "
                    "For Fireworks, configure an API key in OpenCode auth "
                    "(see https://docs.fireworks.ai/api-reference/introduction#authentication) "
                    "or your team's opencode provider setup. "
                    "Set BENCHKIT_ALLOW_EMPTY_OPENCODE_PATCH=1 only for targeted tests."
                ),
                "return_code": return_code,
                "patch_source": patch_source,
                "stdout_preview": _clip_text(stdout),
                "stderr_preview": _clip_text(stderr),
                "command": command,
                "workspace": str(workspace),
                "raw_stdout_path": raw_stdout_path,
                "raw_stderr_path": raw_stderr_path,
            },
        )
    fatal_error(
        "opencode produced no patch",
        details={
            "hint": (
                "OpenCode exited successfully but produced no unified diff and no "
                "git-tracked workspace changes. Set BENCHKIT_ALLOW_EMPTY_OPENCODE_PATCH=1 "
                "only for targeted tests."
            ),
            "return_code": return_code,
            "patch_source": patch_source,
            "stdout_preview": _clip_text(stdout),
            "stderr_preview": _clip_text(stderr),
            "command": command,
            "workspace": str(workspace),
            "raw_stdout_path": raw_stdout_path,
            "raw_stderr_path": raw_stderr_path,
        },
    )


def resolve_opencode_bin() -> str:
    configured = os.environ.get("OPENCODE_BIN", "").strip()
    if configured:
        return configured

    resolved = shutil.which("opencode")
    if resolved:
        return resolved

    candidates = (
        str(Path.home() / ".opencode" / "bin" / "opencode"),
        "/opt/homebrew/bin/opencode",
        "/usr/local/bin/opencode",
        str(Path.home() / ".local" / "bin" / "opencode"),
    )
    for candidate in candidates:
        path = Path(candidate)
        if path.exists() and os.access(path, os.X_OK):
            return str(path)

    fatal_error(
        "opencode binary not found",
        details={
            "searched": ["$OPENCODE_BIN", "$PATH"] + list(candidates),
            "hint": (
                "Set OPENCODE_BIN to the absolute opencode binary path, "
                "for example ~/.opencode/bin/opencode."
            ),
        },
    )
    return "opencode"


def main() -> None:
    args = parse_args()
    payload = read_payload_from_stdin()
    model = payload.get("model", {})
    canonical_model_name = str(model.get("canonical_name", "")).strip()
    raw_model_name = (
        str(model.get("name", "")).strip()
        or os.environ.get("OPENCODE_MODEL", "").strip()
        or "openai/gpt-5"
    )
    model_name = _normalize_opencode_model_reference(raw_model_name)
    agent_name = os.environ.get("OPENCODE_AGENT", "").strip() or "build"

    prompt = render_task_prompt(payload, wrapper_name="opencode")
    prompt_meta = prompt_template_metadata(payload)
    bitloops_setup_timeout_seconds = _resolve_bitloops_setup_timeout_seconds(payload)
    timeout_seconds = _resolve_opencode_timeout_seconds(payload)
    opencode_bin = resolve_opencode_bin()
    command_env = None
    repo_config_path = _resolve_repo_opencode_config_path()
    existing_config_content = os.environ.get("OPENCODE_CONFIG_CONTENT", "")
    try:
        invocation_config = _build_opencode_invocation_config(
            existing_content=existing_config_content,
            repo_config_path=repo_config_path,
        )
    except (ValueError, json.JSONDecodeError, OSError) as exc:
        fatal_error(
            "invalid OpenCode config",
            details={"error": str(exc)},
        )

    ollama_overlay = _build_ollama_opencode_provider_overlay(
        model_name=model_name,
        existing_ollama_content=os.environ.get("OLLAMA_CONFIG_CONTENT", ""),
    )
    if ollama_overlay is not None:
        invocation_config = _deep_merge_dicts(invocation_config or {}, ollama_overlay)

    if invocation_config is not None:
        try:
            encoded_invocation_config = json.dumps(invocation_config)
        except (TypeError, ValueError) as exc:
            fatal_error(
                "invalid OpenCode config",
                details={"error": str(exc)},
            )
        command_env = {"OPENCODE_CONFIG_CONTENT": encoded_invocation_config}

    def run_opencode_command(*, timeout_seconds: int, env: dict[str, str] | None, cwd: str) -> AgentCommandResult:
        command = [
            opencode_bin,
            "run",
            "--format",
            "json",
            "--model",
            model_name,
            "--agent",
            agent_name,
            "--dangerously-skip-permissions",
        ]
        command.extend(env_args("OPENCODE_EXTRA_ARGS"))
        command.append(prompt)
        try:
            stdout, stderr, return_code, elapsed_ms = call_command(
                command,
                timeout_seconds,
                env=env,
                cwd=cwd,
            )
        except subprocess.TimeoutExpired as exc:
            timeout_stdout = _coerce_command_output(exc.stdout)
            timeout_stderr = _coerce_command_output(exc.stderr)
            raw_stdout_path, raw_stderr_path = _persist_raw_opencode_output(
                payload=payload,
                stdout=timeout_stdout,
                stderr=timeout_stderr,
            )
            failure_summary = summarize_command_failure(timeout_stdout, timeout_stderr)
            fatal_error(
                "opencode command timed out",
                details={
                    "timeout_seconds": timeout_seconds,
                    "command": command,
                    "workspace": cwd,
                    "raw_stdout_path": raw_stdout_path,
                    "raw_stderr_path": raw_stderr_path,
                    **failure_summary,
                },
            )
        return AgentCommandResult(
            command=command,
            stdout=stdout,
            stderr=stderr,
            return_code=return_code,
            elapsed_ms=elapsed_ms,
        )

    def persist_execution_output(execution: AgentCommandResult) -> dict[str, str | None]:
        raw_stdout_path, raw_stderr_path = _persist_raw_opencode_output(
            payload=payload,
            stdout=execution.stdout,
            stderr=execution.stderr,
        )
        return {
            "raw_stdout_path": raw_stdout_path,
            "raw_stderr_path": raw_stderr_path,
        }

    run_result = run_agent_wrapper(
        payload=payload,
        args=args,
        agent_name="opencode",
        bitloops_setup_timeout_seconds=bitloops_setup_timeout_seconds,
        timeout_seconds=timeout_seconds,
        failure_message="opencode command failed and no workspace changes were made",
        command_runner=run_opencode_command,
        command_env=command_env,
        post_command=persist_execution_output,
    )
    execution = run_result.execution
    raw_stdout_path = run_result.command_details.get("raw_stdout_path")
    raw_stderr_path = run_result.command_details.get("raw_stderr_path")

    _ensure_nonempty_patch_or_exit(
        patch=run_result.patch,
        return_code=execution.return_code,
        patch_source=run_result.patch_source,
        stdout=execution.stdout,
        stderr=execution.stderr,
        command=execution.command,
        workspace=run_result.workspace,
        raw_stdout_path=raw_stdout_path,
        raw_stderr_path=raw_stderr_path,
    )

    parsed_payload = parse_agent_payload(execution.stdout)
    usage_metrics = extract_usage_metrics(parsed_payload)
    tool_usage_breakdown = extract_tool_usage_breakdown(parsed_payload)
    tool_invocations_raw = extract_tool_invocations_raw(parsed_payload)
    tool_invocations_curated = extract_tool_invocations_curated(tool_invocations_raw)
    tool_invocation_sequence = extract_tool_invocation_sequence(parsed_payload)
    tool_invocation_counts = summarize_tool_invocation_counts(tool_invocation_sequence)
    if tool_invocations_raw:
        usage_metrics["tool_calls"] = len(tool_invocations_raw)
    missing_tool_capture_error = _resolve_missing_tool_capture_error(
        payload=payload,
        tool_invocations_raw=tool_invocations_raw,
        tool_usage_breakdown=tool_usage_breakdown,
    )
    if missing_tool_capture_error:
        fatal_error(
            "opencode tool capture missing",
            details={
                "error": missing_tool_capture_error,
                "command": execution.command,
                "workspace": str(run_result.workspace),
                "raw_stdout_path": raw_stdout_path,
                "raw_stderr_path": raw_stderr_path,
                "tool_usage_breakdown": tool_usage_breakdown,
                "parsed_payload_type": type(parsed_payload).__name__ if parsed_payload is not None else None,
            },
        )
    hook_metrics = load_hook_metrics(
        (
            "OPENCODE_HOOK_METRICS_PATH",
            "OPENCODE_HOOK_LOG_PATH",
            "AGENT_HOOK_METRICS_PATH",
            "AGENT_HOOK_LOG_PATH",
            "HOOK_METRICS_PATH",
            "HOOK_LOG_PATH",
        )
    )
    merged_metrics = merge_metric_metadata(usage_metrics, hook_metrics)

    model_manifest = model if isinstance(model, dict) else {}
    emit_success(
        patch=run_result.patch,
        metadata={
            "wrapper": "opencode",
            "command": execution.command,
            "agent_mode": agent_name,
            "canonical_model_name": canonical_model_name or model_name,
            "resolved_model_name": model_name,
            "opencode_sampling_source": "repo_json",
            "benchmark_manifest_temperature": model_manifest.get("temperature"),
            "benchmark_manifest_seed": model_manifest.get("seed"),
            "benchmark_manifest_max_tokens": model_manifest.get("max_tokens"),
            "elapsed_ms": execution.elapsed_ms,
            "patch_source": run_result.patch_source,
            "prompt_text": prompt,
            **prompt_meta,
            "stderr": execution.stderr.strip(),
            "raw_stdout_path": raw_stdout_path,
            "raw_stderr_path": raw_stderr_path,
            "tool_usage_breakdown": tool_usage_breakdown,
            "tool_invocations_raw": tool_invocations_raw,
            "tool_invocations_curated": tool_invocations_curated,
            "tool_invocation_sequence": tool_invocation_sequence,
            "tool_invocation_counts": tool_invocation_counts,
            **merged_metrics,
            **run_result.bitloops_metadata,
        },
    )


if __name__ == "__main__":
    main()
