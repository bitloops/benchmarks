#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import sys
from copy import deepcopy

from common import (  # type: ignore[import-not-found]
    call_command,
    build_bitloops_task_environment,
    capture_workspace_patch,
    emit_success,
    env_args,
    env_flag,
    extract_git_patch,
    extract_tool_invocation_sequence,
    extract_tool_invocations_curated,
    extract_tool_invocations_raw,
    extract_tool_usage_breakdown,
    extract_usage_metrics,
    fatal_error,
    load_hook_metrics,
    merge_metric_metadata,
    parse_agent_output,
    parse_agent_payload,
    read_payload_from_stdin,
    render_task_prompt,
    reset_workspace,
    resolve_bitloops_sandbox,
    resolve_workspace,
    setup_bitloops_for_workspace,
    start_bitloops_task_daemon,
    stop_bitloops_task_daemon,
    summarize_command_failure,
    summarize_tool_invocation_counts,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--bitloops-init",
        action="store_true",
        help="Initialize Bitloops before running the agent command.",
    )
    parser.add_argument(
        "--bitloops-sync",
        choices=("true", "false"),
        default="true",
        help="Whether Bitloops init should queue sync.",
    )
    parser.add_argument(
        "--bitloops-ingest",
        choices=("true", "false"),
        default="true",
        help="Whether Bitloops init should queue ingest.",
    )
    parser.add_argument(
        "--bitloops-embeddings-runtime",
        choices=("local", "platform"),
        help="Embeddings runtime to configure during Bitloops init.",
    )
    parser.add_argument(
        "--bitloops-no-embeddings",
        action="store_true",
        help="Disable embeddings setup during Bitloops init.",
    )
    parser.add_argument(
        "--bitloops-summary-mode",
        choices=("auto", "off"),
        help="Repo-local Bitloops summary mode override to apply after init.",
    )
    parser.add_argument(
        "--bitloops-embedding-mode",
        choices=("off", "deterministic", "refresh_on_upgrade", "semantic_aware_once"),
        help="Repo-local Bitloops embedding mode override to apply after init.",
    )
    args, _ = parser.parse_known_args()
    return args


def _resolve_opencode_timeout_seconds(payload: dict[str, object]) -> int:
    env_timeout = os.environ.get("OPENCODE_TIMEOUT_SECONDS", "").strip()
    env_value = 0
    if env_timeout:
        try:
            env_value = int(env_timeout)
        except ValueError:
            env_value = 0

    run = payload.get("run", {})
    run_value = 0
    if isinstance(run, dict):
        raw_timeout = run.get("timeout_seconds")
        try:
            run_value = int(raw_timeout)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            run_value = 0

    return max(env_value, run_value, 900)


def _resolve_bitloops_setup_timeout_seconds(payload: dict[str, object]) -> int:
    env_timeout = os.environ.get("BITLOOPS_SETUP_TIMEOUT_SECONDS", "").strip()
    env_value = 0
    if env_timeout:
        try:
            env_value = int(env_timeout)
        except ValueError:
            env_value = 0

    run = payload.get("run", {})
    run_value = 0
    if isinstance(run, dict):
        raw_timeout = run.get("timeout_seconds")
        try:
            run_value = int(raw_timeout)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            run_value = 0

    return max(env_value, run_value, 1500)


def _coerce_temperature(model: object) -> float | None:
    if not isinstance(model, dict):
        return None
    raw_value = model.get("temperature")
    if raw_value is None:
        return None
    try:
        return float(raw_value)
    except (TypeError, ValueError):
        return None


def _coerce_seed(model: object) -> int | None:
    if not isinstance(model, dict):
        return None
    raw_value = model.get("seed")
    if raw_value is None:
        return None
    try:
        return int(raw_value)
    except (TypeError, ValueError):
        return None


def _normalize_opencode_provider_id(provider_id: str | None) -> str | None:
    normalized = str(provider_id or "").strip()
    if not normalized:
        return None
    if normalized == "fireworks":
        return "fireworks-ai"
    return normalized


def _normalize_opencode_model_reference(model_reference: str) -> str:
    normalized = model_reference.strip()
    if not normalized or "/" not in normalized:
        return normalized

    provider_id, model_id = normalized.split("/", 1)
    canonical_provider_id = _normalize_opencode_provider_id(provider_id)
    if not canonical_provider_id:
        return normalized
    return f"{canonical_provider_id}/{model_id.strip()}"


def _split_opencode_model_reference(
    resolved_model_name: str,
    fallback_provider: str | None,
) -> tuple[str | None, str | None]:
    model_name = _normalize_opencode_model_reference(resolved_model_name)
    if not model_name:
        return None, None

    if "/" in model_name:
        provider_id, model_id = model_name.split("/", 1)
        provider_id = _normalize_opencode_provider_id(provider_id)
        model_id = model_id.strip()
        if provider_id and model_id:
            return provider_id, model_id

    provider_id = _normalize_opencode_provider_id(fallback_provider)
    if not provider_id:
        return None, None
    return provider_id, model_name


def _build_opencode_runtime_config(
    *,
    payload: dict[str, object],
    resolved_model_name: str,
) -> dict[str, object] | None:
    model = payload.get("model", {})
    temperature = _coerce_temperature(model)
    seed = _coerce_seed(model)
    if temperature is None and seed is None:
        return None

    fallback_provider = None
    if isinstance(model, dict):
        fallback_provider = str(model.get("provider", "")).strip() or None

    provider_id, model_id = _split_opencode_model_reference(
        resolved_model_name,
        fallback_provider,
    )
    if not provider_id or not model_id:
        return None

    options: dict[str, object] = {}
    if temperature is not None:
        options["temperature"] = temperature
    if seed is not None:
        options["seed"] = seed

    return {
        "$schema": "https://opencode.ai/config.json",
        "provider": {
            provider_id: {
                "models": {
                    model_id: {
                        "options": options,
                    }
                }
            }
        },
    }


def _deep_merge_dicts(base: dict[str, object], overlay: dict[str, object]) -> dict[str, object]:
    merged: dict[str, object] = deepcopy(base)
    for key, value in overlay.items():
        current = merged.get(key)
        if isinstance(current, dict) and isinstance(value, dict):
            merged[key] = _deep_merge_dicts(current, value)
            continue
        merged[key] = deepcopy(value)
    return merged


def _merge_opencode_config_content(
    existing_content: str,
    runtime_config: dict[str, object],
) -> dict[str, object]:
    if not existing_content.strip():
        return deepcopy(runtime_config)

    loaded = json.loads(existing_content)
    if not isinstance(loaded, dict):
        raise ValueError("OPENCODE_CONFIG_CONTENT must decode to a JSON object")
    return _deep_merge_dicts(loaded, runtime_config)


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
    runtime_config = _build_opencode_runtime_config(
        payload=payload,
        resolved_model_name=model_name,
    )

    workspace = resolve_workspace(payload)
    bitloops_sandbox = resolve_bitloops_sandbox(payload)
    bitloops_env = build_bitloops_task_environment(bitloops_sandbox)
    prompt = render_task_prompt(payload, wrapper_name="opencode")

    try:
        reset_workspace(workspace)
    except Exception as exc:
        fatal_error(
            "workspace reset failed",
            details={"error": str(exc), "workspace": str(workspace)},
        )

    bitloops_metadata: dict[str, object] = {}
    task_daemon_handle = None
    if args.bitloops_init:
        bitloops_setup_timeout_seconds = _resolve_bitloops_setup_timeout_seconds(payload)
        try:
            if bitloops_env is not None and bitloops_sandbox is not None:
                task_daemon_handle = start_bitloops_task_daemon(
                    binary=os.environ.get("BITLOOPS_BIN", "bitloops"),
                    timeout=bitloops_setup_timeout_seconds,
                    env=bitloops_env,
                    sandbox=bitloops_sandbox,
                    cwd=str(workspace),
                )
            bitloops_metadata = setup_bitloops_for_workspace(
                agent_name="opencode",
                timeout_seconds=bitloops_setup_timeout_seconds,
                sync=args.bitloops_sync == "true",
                ingest=args.bitloops_ingest == "true",
                embeddings_runtime=args.bitloops_embeddings_runtime,
                no_embeddings=args.bitloops_no_embeddings,
                summary_mode=args.bitloops_summary_mode,
                embedding_mode=args.bitloops_embedding_mode,
                sandbox=bitloops_sandbox,
                env=bitloops_env,
                cwd=str(workspace),
                task_daemon_handle=task_daemon_handle,
            )
        except Exception as exc:
            stop_bitloops_task_daemon(task_daemon_handle)
            fatal_error(
                "bitloops setup failed",
                details={"error": str(exc), "workspace": str(workspace)},
            )

    command = [
        os.environ.get("OPENCODE_BIN", "opencode"),
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
    timeout_seconds = _resolve_opencode_timeout_seconds(payload)
    command_env = bitloops_env
    if runtime_config is not None:
        existing_config_content = os.environ.get("OPENCODE_CONFIG_CONTENT", "")
        try:
            merged_runtime_config = _merge_opencode_config_content(
                existing_config_content,
                runtime_config,
            )
        except (ValueError, json.JSONDecodeError) as exc:
            fatal_error(
                "invalid OPENCODE_CONFIG_CONTENT",
                details={"error": str(exc)},
            )
        command_env = dict(bitloops_env) if bitloops_env is not None else dict(os.environ)
        command_env["OPENCODE_CONFIG_CONTENT"] = json.dumps(merged_runtime_config)

    try:
        stdout, stderr, return_code, elapsed_ms = call_command(
            command,
            timeout_seconds,
            env=command_env,
            cwd=str(workspace),
        )
    finally:
        stop_bitloops_task_daemon(task_daemon_handle)
    raw_stdout_path, raw_stderr_path = _persist_raw_opencode_output(
        payload=payload,
        stdout=stdout,
        stderr=stderr,
    )

    workspace_patch = capture_workspace_patch(workspace)

    if workspace_patch:
        patch = workspace_patch + "\n"
        patch_source = "workspace_git_diff"
    elif return_code != 0:
        failure_summary = summarize_command_failure(stdout, stderr)
        fatal_error(
            "opencode command failed and no workspace changes were made",
            details={
                "return_code": return_code,
                "command": command,
                "raw_stdout_path": raw_stdout_path,
                "raw_stderr_path": raw_stderr_path,
                **failure_summary,
            },
        )
        sys.exit(1)
    else:
        parsed_payload = parse_agent_payload(stdout)
        parsed_text = parse_agent_output(stdout, parsed_payload=parsed_payload)
        patch, patch_source = extract_git_patch(parsed_text)

    parsed_payload = parse_agent_payload(stdout)
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
                "command": command,
                "workspace": str(workspace),
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

    emit_success(
        patch=patch,
        metadata={
            "wrapper": "opencode",
            "command": command,
            "agent_mode": agent_name,
            "canonical_model_name": canonical_model_name or model_name,
            "resolved_model_name": model_name,
            "temperature": _coerce_temperature(model),
            "seed": _coerce_seed(model),
            "runtime_config_applied": runtime_config is not None,
            "elapsed_ms": elapsed_ms,
            "patch_source": patch_source,
            "prompt_text": prompt,
            "stderr": stderr.strip(),
            "raw_stdout_path": raw_stdout_path,
            "raw_stderr_path": raw_stderr_path,
            "tool_usage_breakdown": tool_usage_breakdown,
            "tool_invocations_raw": tool_invocations_raw,
            "tool_invocations_curated": tool_invocations_curated,
            "tool_invocation_sequence": tool_invocation_sequence,
            "tool_invocation_counts": tool_invocation_counts,
            **merged_metrics,
            **bitloops_metadata,
        },
    )


if __name__ == "__main__":
    main()
