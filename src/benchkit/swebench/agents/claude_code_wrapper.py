#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os

from .common import (
    AgentCommandResult,
    _debug_log,
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
    validate_exact_tool_capture,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    add_bitloops_wrapper_args(parser)
    args, _ = parser.parse_known_args()
    return args


def _resolve_claude_timeout_seconds(payload: dict[str, object]) -> int:
    return resolve_timeout_seconds(
        payload,
        env_var="CLAUDE_TIMEOUT_SECONDS",
        default_seconds=900,
    )


def _resolve_bitloops_setup_timeout_seconds(payload: dict[str, object]) -> int:
    return common_resolve_bitloops_setup_timeout_seconds(payload)


def main() -> None:
    args = parse_args()
    payload = read_payload_from_stdin()
    model = payload.get("model", {})
    canonical_model_name = str(model.get("canonical_name", "")).strip()
    model_name = (
        str(model.get("name", "")).strip()
        or os.environ.get("CLAUDE_MODEL", "").strip()
        or "claude-opus-4-6"
    )
    prompt = render_task_prompt(payload, wrapper_name="claude_code")
    prompt_meta = prompt_template_metadata(payload)
    bitloops_setup_timeout_seconds = _resolve_bitloops_setup_timeout_seconds(payload)
    output_format = os.environ.get("CLAUDE_OUTPUT_FORMAT", "stream-json").strip() or "stream-json"
    extra_args = env_args("CLAUDE_EXTRA_ARGS")
    timeout_seconds = _resolve_claude_timeout_seconds(payload)

    def run_claude_command(*, timeout_seconds: int, env: dict[str, str] | None, cwd: str) -> AgentCommandResult:
        command = [
            os.environ.get("CLAUDE_BIN", "claude"),
            "--print",
            "--output-format",
            output_format,
            "--model",
            model_name,
            "--dangerously-skip-permissions",
        ]
        if (
            output_format == "stream-json"
            and env_flag("CLAUDE_STREAM_JSON_VERBOSE", True)
            and "--verbose" not in extra_args
        ):
            # Some Claude CLI builds require --verbose when using stream-json with --print.
            command.append("--verbose")
        if output_format == "stream-json" and env_flag("CLAUDE_INCLUDE_PARTIAL_MESSAGES", True):
            command.append("--include-partial-messages")
        command.extend(extra_args)
        command.append(prompt)

        stdout, stderr, return_code, elapsed_ms = call_command(
            command,
            timeout_seconds,
            env=env,
            cwd=cwd,
        )
        if (
            return_code != 0
            and output_format == "stream-json"
            and "requires --verbose" in stderr.lower()
            and "--verbose" not in command
        ):
            retry_command = command[:-1] + ["--verbose", command[-1]]
            stdout, stderr, return_code, elapsed_ms = call_command(
                retry_command,
                timeout_seconds,
                env=env,
                cwd=cwd,
            )
            command = retry_command
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
        agent_name="claude-code",
        bitloops_setup_timeout_seconds=bitloops_setup_timeout_seconds,
        timeout_seconds=timeout_seconds,
        failure_message="claude command failed and no workspace changes were made",
        command_runner=run_claude_command,
    )
    execution = run_result.execution

    stdout_lines = [line for line in execution.stdout.splitlines() if line.strip()]
    parsed_payload = parse_agent_payload(execution.stdout)
    usage_metrics = extract_usage_metrics(parsed_payload)
    tool_usage_breakdown = extract_tool_usage_breakdown(parsed_payload)
    tool_invocations_raw = extract_tool_invocations_raw(parsed_payload)
    tool_invocations_curated = extract_tool_invocations_curated(tool_invocations_raw)
    tool_invocation_sequence = extract_tool_invocation_sequence(parsed_payload)
    tool_invocation_counts = summarize_tool_invocation_counts(tool_invocation_sequence)
    detailed_tool_total = len(tool_invocations_raw)
    reported_tool_total_raw = usage_metrics.get("tool_calls")
    reported_tool_total = (
        int(float(reported_tool_total_raw))
        if isinstance(reported_tool_total_raw, (int, float))
        else 0
    )
    if detailed_tool_total > 0:
        usage_metrics["tool_calls"] = detailed_tool_total

    require_exact_tools = env_flag(
        "BENCHKIT_REQUIRE_EXACT_TOOLS",
        default=env_flag("CLAUDE_CODE_USE_BEDROCK", False),
    )
    exact_capture_error = validate_exact_tool_capture(
        require_exact_tools=require_exact_tools,
        output_format=output_format,
        parsed_payload=parsed_payload,
        reported_tool_total=reported_tool_total,
        invocations_raw=tool_invocations_raw,
        invocations_curated=tool_invocations_curated,
        tool_usage_breakdown=tool_usage_breakdown,
    )
    if exact_capture_error:
        fatal_error(
            "exact tool capture failed",
            details={
                "message": exact_capture_error,
                "reported_tool_calls": reported_tool_total,
                "captured_invocations": detailed_tool_total,
                "output_format": output_format,
            },
        )

    hook_metrics = load_hook_metrics(
        (
            "CLAUDE_HOOK_METRICS_PATH",
            "CLAUDE_HOOK_LOG_PATH",
            "AGENT_HOOK_METRICS_PATH",
            "AGENT_HOOK_LOG_PATH",
            "HOOK_METRICS_PATH",
            "HOOK_LOG_PATH",
        )
    )
    merged_metrics = merge_metric_metadata(usage_metrics, hook_metrics)

    _debug_log(
        hypothesis_id="H5",
        location="src/benchkit/swebench/agents/claude_code_wrapper.py:main",
        message="claude raw stdout summary",
        data={
            "stdout_nonempty_line_count": len(stdout_lines),
            "stdout_last_lines": [line[:400] for line in stdout_lines[-3:]],
            "parsed_payload_kind": type(parsed_payload).__name__ if parsed_payload is not None else None,
            "parsed_payload_len": len(parsed_payload) if isinstance(parsed_payload, list) else None,
        },
    )

    _debug_log(
        hypothesis_id="H4",
        location="src/benchkit/swebench/agents/claude_code_wrapper.py:main",
        message="claude wrapper output summary",
        data={
            "command_prefix": execution.command[:-1],
            "output_format": output_format,
            "return_code": execution.return_code,
            "stderr": execution.stderr.strip()[:500],
            "detailed_tool_total": detailed_tool_total,
            "reported_tool_total": reported_tool_total,
            "usage_metrics": usage_metrics,
            "merged_metrics": merged_metrics,
        },
    )

    emit_success(
        patch=run_result.patch,
        metadata={
            "wrapper": "claude_code",
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
            "tool_event_capture_required": require_exact_tools,
            "tool_event_capture_satisfied": (detailed_tool_total > 0) or (reported_tool_total == 0),
            **merged_metrics,
            **run_result.bitloops_metadata,
        },
    )


if __name__ == "__main__":
    main()
