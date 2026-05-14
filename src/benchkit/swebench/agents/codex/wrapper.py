#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
from copy import deepcopy

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
    resolve_timeout_seconds,
    run_agent_wrapper,
    summarize_tool_invocation_counts,
)
from .runtime import (
    build_codex_runtime_config as _runtime_build_codex_runtime_config,
    load_codex_config_file as _runtime_load_codex_config_file,
    normalize_codex_extra_args as _runtime_normalize_extra_args,
    normalize_codex_reasoning_effort as _runtime_normalize_codex_reasoning_effort,
    normalize_codex_reasoning_summary as _runtime_normalize_codex_reasoning_summary,
    normalize_codex_verbosity as _runtime_normalize_codex_verbosity,
    resolve_codex_config_timeout_seconds as _runtime_resolve_codex_config_timeout_seconds,
    resolve_codex_full_auto as _runtime_resolve_codex_full_auto,
    resolve_codex_sandbox_mode as _runtime_resolve_codex_sandbox_mode,
    resolve_codex_skip_git_repo_check as _runtime_resolve_codex_skip_git_repo_check,
    resolve_codex_timeout_seconds as _runtime_resolve_codex_timeout_seconds,
    resolve_repo_codex_config_path as _runtime_resolve_repo_codex_config_path,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    add_bitloops_wrapper_args(parser)
    args, _ = parser.parse_known_args()
    return args


def _resolve_bitloops_setup_timeout_seconds(payload: dict[str, object]) -> int:
    return common_resolve_bitloops_setup_timeout_seconds(payload)


def _resolve_codex_config_timeout_seconds(runtime_config: dict[str, object]) -> int:
    raw = runtime_config.get("timeout_seconds")
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return 0
    return value if value > 0 else 0


def _resolve_codex_timeout_seconds(
    payload: dict[str, object],
    runtime_config: dict[str, object],
) -> int:
    config_value = _resolve_codex_config_timeout_seconds(runtime_config)
    return resolve_timeout_seconds(
        payload,
        env_var="CODEX_TIMEOUT_SECONDS",
        default_seconds=900,
        extra_values=(config_value,),
    )


def _run_condition(payload: dict[str, object]) -> str:
    run = payload.get("run", {})
    if not isinstance(run, dict):
        return ""
    return str(run.get("condition", "")).strip().lower()


def _should_require_devql_invocation(payload: dict[str, object]) -> bool:
    if "BENCHKIT_REQUIRE_CODEX_DEVQL" in os.environ:
        return env_flag("BENCHKIT_REQUIRE_CODEX_DEVQL", default=False)
    if "BENCHKIT_REQUIRE_DEVQL" in os.environ:
        return env_flag("BENCHKIT_REQUIRE_DEVQL", default=False)
    return _run_condition(payload) == "with_bitloops"


def _has_devql_invocation(tool_invocations_raw: list[dict[str, object]]) -> bool:
    for invocation in tool_invocations_raw:
        tool_name = str(invocation.get("tool") or "").strip().lower()
        if tool_name not in {"bash", "shell", "terminal"}:
            continue
        input_payload = invocation.get("input")
        if not isinstance(input_payload, dict):
            continue
        command = input_payload.get("command")
        if not isinstance(command, str):
            continue
        normalized_command = " ".join(command.strip().lower().split())
        if "bitloops devql query" in normalized_command:
            return True
    return False


def _resolve_missing_devql_invocation_error(
    *,
    payload: dict[str, object],
    tool_invocations_raw: list[dict[str, object]],
) -> str | None:
    if not _should_require_devql_invocation(payload):
        return None
    if _has_devql_invocation(tool_invocations_raw):
        return None
    return (
        "Codex finished a Bitloops run without any captured `bitloops devql query` invocation. "
        "Bitloops runs must issue DevQL exploration commands before broad shell searches."
    )


def _as_bool(value: object, *, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    return default


def _resolve_codex_full_auto(runtime_config: dict[str, object]) -> bool:
    if "CODEX_FULL_AUTO" in os.environ:
        return env_flag("CODEX_FULL_AUTO", True)
    return _as_bool(runtime_config.get("full_auto"), default=True)


def _resolve_codex_skip_git_repo_check(runtime_config: dict[str, object]) -> bool:
    if "CODEX_SKIP_GIT_REPO_CHECK" in os.environ:
        return env_flag("CODEX_SKIP_GIT_REPO_CHECK", False)
    return _as_bool(runtime_config.get("skip_git_repo_check"), default=False)


def _resolve_codex_sandbox_mode(runtime_config: dict[str, object]) -> str:
    sandbox = os.environ.get("CODEX_SANDBOX", "").strip()
    if sandbox:
        return sandbox
    configured = runtime_config.get("sandbox")
    if isinstance(configured, str) and configured.strip():
        return configured.strip()
    return "workspace-write"


def _normalize_codex_reasoning_effort(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip().lower()
    if normalized not in {"none", "minimal", "low", "medium", "high", "xhigh"}:
        return None
    return normalized


def _normalize_codex_verbosity(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip().lower()
    if normalized not in {"low", "medium", "high"}:
        return None
    return normalized


def _normalize_codex_reasoning_summary(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip().lower()
    if normalized not in {"auto", "concise", "detailed", "none"}:
        return None
    return normalized


def _normalize_extra_args(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for item in value:
        if not isinstance(item, str):
            continue
        cleaned = item.strip()
        if cleaned:
            out.append(cleaned)
    return out


def _resolve_repo_codex_config_path() -> Path:
    return Path(__file__).resolve().parents[2] / "configs" / "codex" / "codex.json"


def _decode_codex_config_content(
    raw_content: str,
    *,
    source_name: str,
) -> dict[str, object]:
    loaded = json.loads(raw_content)
    if not isinstance(loaded, dict):
        raise ValueError(f"{source_name} must decode to a JSON object")
    return loaded


def _load_codex_config_file(config_path: Path) -> dict[str, object]:
    return _decode_codex_config_content(
        config_path.read_text(encoding="utf-8"),
        source_name=str(config_path),
    )


def _deep_merge_dicts(base: dict[str, object], overlay: dict[str, object]) -> dict[str, object]:
    merged: dict[str, object] = deepcopy(base)
    for key, value in overlay.items():
        current = merged.get(key)
        if isinstance(current, dict) and isinstance(value, dict):
            merged[key] = _deep_merge_dicts(current, value)
            continue
        merged[key] = deepcopy(value)
    return merged


def _build_codex_runtime_config(
    *,
    existing_content: str,
    repo_config_path: Path,
) -> dict[str, object]:
    merged: dict[str, object] = {}
    if existing_content.strip():
        merged = _decode_codex_config_content(
            existing_content,
            source_name="CODEX_CONFIG_CONTENT",
        )
    if repo_config_path.exists():
        repo_config = _load_codex_config_file(repo_config_path)
        merged = _deep_merge_dicts(merged, repo_config)
    return merged


_resolve_codex_config_timeout_seconds = _runtime_resolve_codex_config_timeout_seconds
_resolve_codex_timeout_seconds = _runtime_resolve_codex_timeout_seconds
_resolve_codex_full_auto = _runtime_resolve_codex_full_auto
_resolve_codex_skip_git_repo_check = _runtime_resolve_codex_skip_git_repo_check
_resolve_codex_sandbox_mode = _runtime_resolve_codex_sandbox_mode
_normalize_codex_reasoning_effort = _runtime_normalize_codex_reasoning_effort
_normalize_codex_verbosity = _runtime_normalize_codex_verbosity
_normalize_codex_reasoning_summary = _runtime_normalize_codex_reasoning_summary
_normalize_extra_args = _runtime_normalize_extra_args
_resolve_repo_codex_config_path = _runtime_resolve_repo_codex_config_path
_load_codex_config_file = _runtime_load_codex_config_file
_build_codex_runtime_config = _runtime_build_codex_runtime_config


def main() -> None:
    args = parse_args()
    payload = read_payload_from_stdin()
    model = payload.get("model", {})
    canonical_model_name = str(model.get("canonical_name", "")).strip()
    repo_config_path = _resolve_repo_codex_config_path()
    existing_config_content = os.environ.get("CODEX_CONFIG_CONTENT", "")
    try:
        runtime_config = _build_codex_runtime_config(
            existing_content=existing_config_content,
            repo_config_path=repo_config_path,
        )
    except (ValueError, json.JSONDecodeError, OSError) as exc:
        fatal_error(
            "invalid Codex config",
            details={"error": str(exc)},
        )
        return

    configured_model = runtime_config.get("model")
    configured_model_name = (
        configured_model.strip()
        if isinstance(configured_model, str)
        else ""
    )
    model_name = (
        str(model.get("name", "")).strip()
        or os.environ.get("CODEX_MODEL", "").strip()
        or configured_model_name
        or "gpt-5.4"
    )
    bitloops_setup_timeout_seconds = _resolve_bitloops_setup_timeout_seconds(payload)
    prompt = render_task_prompt(payload, wrapper_name="codex")
    prompt_meta = prompt_template_metadata(payload)
    codex_bin = resolve_codex_bin()
    reasoning_effort = _normalize_codex_reasoning_effort(
        runtime_config.get("model_reasoning_effort")
    )
    verbosity = _normalize_codex_verbosity(runtime_config.get("model_verbosity"))
    reasoning_summary = _normalize_codex_reasoning_summary(
        runtime_config.get("model_reasoning_summary")
    )
    timeout_seconds = _resolve_codex_timeout_seconds(payload, runtime_config)

    def run_codex_command(*, timeout_seconds: int, env: dict[str, str] | None, cwd: str) -> AgentCommandResult:
        command = [
            codex_bin,
            "exec",
            "--json",
            "--model",
            model_name,
            "--cd",
            cwd,
        ]
        if reasoning_effort:
            command.extend(["--config", f"model_reasoning_effort={reasoning_effort}"])
        if verbosity:
            command.extend(["--config", f"model_verbosity={verbosity}"])
        if reasoning_summary:
            command.extend(["--config", f"model_reasoning_summary={reasoning_summary}"])
        if _resolve_codex_full_auto(runtime_config):
            command.append("--full-auto")
        else:
            sandbox_mode = _resolve_codex_sandbox_mode(runtime_config)
            if sandbox_mode:
                command.extend(["--sandbox", sandbox_mode])
        if _resolve_codex_skip_git_repo_check(runtime_config):
            command.append("--skip-git-repo-check")
        command.extend(_normalize_extra_args(runtime_config.get("extra_args")))
        command.extend(env_args("CODEX_EXTRA_ARGS"))
        command.append(prompt)
        stdout, stderr, return_code, elapsed_ms = call_command(
            command,
            timeout_seconds,
            env=env,
            cwd=cwd,
        )
        return AgentCommandResult(
            command=command,
            stdout=stdout,
            stderr=stderr,
            return_code=return_code,
            elapsed_ms=elapsed_ms,
        )

    run_result = run_agent_wrapper(
        payload=payload,
        args=args,
        agent_name="codex",
        bitloops_setup_timeout_seconds=bitloops_setup_timeout_seconds,
        timeout_seconds=timeout_seconds,
        failure_message="codex exec command failed and no workspace changes were made",
        command_runner=run_codex_command,
    )
    execution = run_result.execution

    parsed_payload = parse_agent_payload(execution.stdout)
    usage_metrics = extract_usage_metrics(parsed_payload)
    tool_usage_breakdown = extract_tool_usage_breakdown(parsed_payload)
    tool_invocations_raw = extract_tool_invocations_raw(parsed_payload)
    tool_invocations_curated = extract_tool_invocations_curated(tool_invocations_raw)
    tool_invocation_sequence = extract_tool_invocation_sequence(parsed_payload)
    tool_invocation_counts = summarize_tool_invocation_counts(tool_invocation_sequence)
    missing_devql_error = _resolve_missing_devql_invocation_error(
        payload=payload,
        tool_invocations_raw=tool_invocations_raw,
    )
    if missing_devql_error:
        fatal_error(
            "codex devql invocation missing",
            details={
                "error": missing_devql_error,
                "command": execution.command,
                "workspace": str(run_result.workspace),
                "tool_invocations_captured": len(tool_invocations_raw),
                "tool_invocation_sequence": tool_invocation_sequence,
            },
        )
    hook_metrics = load_hook_metrics(
        (
            "CODEX_HOOK_METRICS_PATH",
            "CODEX_HOOK_LOG_PATH",
            "AGENT_HOOK_METRICS_PATH",
            "AGENT_HOOK_LOG_PATH",
            "HOOK_METRICS_PATH",
            "HOOK_LOG_PATH",
        )
    )
    merged_metrics = merge_metric_metadata(usage_metrics, hook_metrics)

    emit_success(
        patch=run_result.patch,
        metadata={
            "wrapper": "codex",
            "command": execution.command,
            "canonical_model_name": canonical_model_name or model_name,
            "resolved_model_name": model_name,
            "codex_runtime_config": runtime_config,
            "elapsed_ms": execution.elapsed_ms,
            "patch_source": run_result.patch_source,
            "prompt_text": prompt,
            **prompt_meta,
            "stderr": execution.stderr.strip(),
            "tool_usage_breakdown": tool_usage_breakdown,
            "tool_invocations_raw": tool_invocations_raw,
            "tool_invocations_curated": tool_invocations_curated,
            "tool_invocation_sequence": tool_invocation_sequence,
            "tool_invocation_counts": tool_invocation_counts,
            **merged_metrics,
            **run_result.bitloops_metadata,
        },
    )


def resolve_codex_bin() -> str:
    configured = os.environ.get("CODEX_BIN", "").strip()
    if configured:
        return configured

    resolved = shutil.which("codex")
    if resolved:
        return resolved

    candidates = (
        "/Applications/Codex.app/Contents/Resources/codex",
        "/opt/homebrew/bin/codex",
        "/usr/local/bin/codex",
        str(Path.home() / ".local" / "bin" / "codex"),
    )
    for candidate in candidates:
        path = Path(candidate)
        if path.exists() and os.access(path, os.X_OK):
            return str(path)

    fatal_error(
        "codex binary not found",
        details={
            "searched": ["$CODEX_BIN", "$PATH"] + list(candidates),
            "hint": (
                "Set CODEX_BIN to the absolute codex binary path, "
                "for example /Applications/Codex.app/Contents/Resources/codex."
            ),
        },
    )
    return "codex"


if __name__ == "__main__":
    main()
