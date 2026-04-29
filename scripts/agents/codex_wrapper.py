#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import sys
from copy import deepcopy

from common import (  # type: ignore[import-not-found]
    build_bitloops_task_environment,
    call_command,
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
        "--bitloops-no-summaries",
        action="store_true",
        help="Disable summaries setup during Bitloops init.",
    )
    parser.add_argument(
        "--bitloops-summary-mode",
        choices=("auto", "off"),
        help="Benchmark wrapper control: 'auto' keeps Bitloops init defaults, 'off' maps to --bitloops-no-summaries.",
    )
    parser.add_argument(
        "--bitloops-embedding-mode",
        choices=("off", "deterministic", "refresh_on_upgrade", "semantic_aware_once"),
        help="Repo-local Bitloops embedding mode override to apply after init.",
    )
    args, _ = parser.parse_known_args()
    return args


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
    env_timeout = os.environ.get("CODEX_TIMEOUT_SECONDS", "").strip()
    env_value = 0
    if env_timeout:
        try:
            env_value = int(env_timeout)
        except ValueError:
            env_value = 0
    config_value = _resolve_codex_config_timeout_seconds(runtime_config)

    run = payload.get("run", {})
    run_value = 0
    if isinstance(run, dict):
        raw_timeout = run.get("timeout_seconds")
        try:
            run_value = int(raw_timeout)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            run_value = 0

    return max(env_value, config_value, run_value, 900)


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
    workspace = resolve_workspace(payload)
    bitloops_sandbox = resolve_bitloops_sandbox(payload)
    bitloops_env = build_bitloops_task_environment(bitloops_sandbox)

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
                agent_name="codex",
                timeout_seconds=bitloops_setup_timeout_seconds,
                sync=args.bitloops_sync == "true",
                ingest=args.bitloops_ingest == "true",
                embeddings_runtime=args.bitloops_embeddings_runtime,
                no_embeddings=args.bitloops_no_embeddings,
                no_summaries=args.bitloops_no_summaries,
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

    prompt = render_task_prompt(payload, wrapper_name="codex")
    codex_bin = resolve_codex_bin()
    command = [
        codex_bin,
        "exec",
        "--json",
        "--model",
        model_name,
        "--cd",
        str(workspace),
    ]
    reasoning_effort = _normalize_codex_reasoning_effort(
        runtime_config.get("model_reasoning_effort")
    )
    if reasoning_effort:
        command.extend(["--config", f"model_reasoning_effort={reasoning_effort}"])
    verbosity = _normalize_codex_verbosity(runtime_config.get("model_verbosity"))
    if verbosity:
        command.extend(["--config", f"model_verbosity={verbosity}"])
    reasoning_summary = _normalize_codex_reasoning_summary(
        runtime_config.get("model_reasoning_summary")
    )
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
    timeout_seconds = _resolve_codex_timeout_seconds(payload, runtime_config)

    try:
        stdout, stderr, return_code, elapsed_ms = call_command(
            command,
            timeout_seconds,
            env=bitloops_env,
            cwd=str(workspace),
        )
    finally:
        stop_bitloops_task_daemon(task_daemon_handle)

    workspace_patch = capture_workspace_patch(workspace)

    if workspace_patch:
        patch = workspace_patch + "\n"
        patch_source = "workspace_git_diff"
    elif return_code != 0:
        failure_summary = summarize_command_failure(stdout, stderr)
        fatal_error(
            "codex exec command failed and no workspace changes were made",
            details={
                "return_code": return_code,
                "command": command,
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
        patch=patch,
        metadata={
            "wrapper": "codex",
            "command": command,
            "canonical_model_name": canonical_model_name or model_name,
            "resolved_model_name": model_name,
            "codex_runtime_config": runtime_config,
            "elapsed_ms": elapsed_ms,
            "patch_source": patch_source,
            "prompt_text": prompt,
            "stderr": stderr.strip(),
            "tool_usage_breakdown": tool_usage_breakdown,
            "tool_invocations_raw": tool_invocations_raw,
            "tool_invocations_curated": tool_invocations_curated,
            "tool_invocation_sequence": tool_invocation_sequence,
            "tool_invocation_counts": tool_invocation_counts,
            **merged_metrics,
            **bitloops_metadata,
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
