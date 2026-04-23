#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import sys

from common import (  # type: ignore[import-not-found]
    call_command,
    build_bitloops_task_environment,
    capture_workspace_patch,
    emit_success,
    env_args,
    extract_usage_metrics,
    extract_git_patch,
    extract_tool_invocations_curated,
    extract_tool_invocations_raw,
    extract_tool_invocation_sequence,
    extract_tool_usage_breakdown,
    fatal_error,
    load_hook_metrics,
    merge_metric_metadata,
    parse_agent_payload,
    parse_agent_output,
    read_payload_from_stdin,
    render_task_prompt,
    reset_workspace,
    resolve_bitloops_sandbox,
    resolve_workspace,
    setup_bitloops_for_workspace,
    start_bitloops_task_daemon,
    stop_bitloops_task_daemon,
    summarize_tool_invocation_counts,
    summarize_command_failure,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--bitloops-init",
        action="store_true",
        help="Initialize Bitloops before running the agent command.",
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

    return max(env_value, run_value, 180)


def main() -> None:
    args = parse_args()
    payload = read_payload_from_stdin()
    model = payload.get("model", {})
    canonical_model_name = str(model.get("canonical_name", "")).strip()
    model_name = (
        str(model.get("name", "")).strip()
        or os.environ.get("CURSOR_MODEL", "").strip()
        or "sonnet-4"
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
                agent_name="cursor",
                timeout_seconds=bitloops_setup_timeout_seconds,
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

    prompt = render_task_prompt(payload, wrapper_name="cursor")
    command = [
        os.environ.get("CURSOR_AGENT_BIN", "cursor-agent"),
        "--print",
        "--output-format",
        "json",
        "--workspace",
        str(workspace),
        "--model",
        model_name,
        "--dangerously-skip-permissions",
    ]
    trust_flag = os.environ.get("CURSOR_TRUST_FLAG", "--trust").strip()
    if trust_flag:
        command.append(trust_flag)
    command.extend(env_args("CURSOR_EXTRA_ARGS"))
    command.append(prompt)
    timeout_seconds = int(os.environ.get("CURSOR_TIMEOUT_SECONDS", "900"))

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
            "cursor-agent command failed and no workspace changes were made",
            details={
                "return_code": return_code,
                "command": command,
                **failure_summary,
            },
        )
        sys.exit(1)
    else:
        parsed_text = parse_agent_output(
            stdout, parsed_payload=parse_agent_payload(stdout)
        )
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
            "CURSOR_HOOK_METRICS_PATH",
            "CURSOR_HOOK_LOG_PATH",
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
            "wrapper": "cursor",
            "command": command,
            "canonical_model_name": canonical_model_name or model_name,
            "resolved_model_name": model_name,
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


if __name__ == "__main__":
    main()
