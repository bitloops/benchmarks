#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os

from common import (  # type: ignore[import-not-found]
    AgentCommandResult,
    add_bitloops_wrapper_args,
    call_command,
    emit_success,
    env_args,
    extract_usage_metrics,
    extract_tool_invocations_curated,
    extract_tool_invocations_raw,
    extract_tool_invocation_sequence,
    extract_tool_usage_breakdown,
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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    add_bitloops_wrapper_args(parser)
    args, _ = parser.parse_known_args()
    return args


def _resolve_bitloops_setup_timeout_seconds(payload: dict[str, object]) -> int:
    return common_resolve_bitloops_setup_timeout_seconds(payload)


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
    bitloops_setup_timeout_seconds = _resolve_bitloops_setup_timeout_seconds(payload)
    prompt = render_task_prompt(payload, wrapper_name="cursor")
    prompt_meta = prompt_template_metadata(payload)
    trust_flag = os.environ.get("CURSOR_TRUST_FLAG", "--trust").strip()
    timeout_seconds = resolve_timeout_seconds(
        payload,
        env_var="CURSOR_TIMEOUT_SECONDS",
        default_seconds=900,
    )

    def run_cursor_command(*, timeout_seconds: int, env: dict[str, str] | None, cwd: str) -> AgentCommandResult:
        command = [
            os.environ.get("CURSOR_AGENT_BIN", "cursor-agent"),
            "--print",
            "--output-format",
            "json",
            "--workspace",
            cwd,
            "--model",
            model_name,
            "--dangerously-skip-permissions",
        ]
        if trust_flag:
            command.append(trust_flag)
        command.extend(env_args("CURSOR_EXTRA_ARGS"))
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
        agent_name="cursor",
        bitloops_setup_timeout_seconds=bitloops_setup_timeout_seconds,
        timeout_seconds=timeout_seconds,
        failure_message="cursor-agent command failed and no workspace changes were made",
        command_runner=run_cursor_command,
    )
    execution = run_result.execution

    parsed_payload = parse_agent_payload(execution.stdout)
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
        patch=run_result.patch,
        metadata={
            "wrapper": "cursor",
            "command": execution.command,
            "canonical_model_name": canonical_model_name or model_name,
            "resolved_model_name": model_name,
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


if __name__ == "__main__":
    main()
