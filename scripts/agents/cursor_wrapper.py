#!/usr/bin/env python3
from __future__ import annotations

import os

from common import (  # type: ignore[import-not-found]
    call_command,
    emit_success,
    env_args,
    extract_usage_metrics,
    extract_git_patch,
    fatal_error,
    load_hook_metrics,
    merge_metric_metadata,
    parse_agent_payload,
    parse_agent_output,
    read_payload_from_stdin,
    render_task_prompt,
    resolve_workspace,
)


def main() -> None:
    payload = read_payload_from_stdin()
    model = payload.get("model", {})
    canonical_model_name = str(model.get("canonical_name", "")).strip()
    model_name = (
        str(model.get("name", "")).strip()
        or os.environ.get("CURSOR_MODEL", "").strip()
        or "sonnet-4"
    )
    workspace = resolve_workspace(payload)

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
    ]
    trust_flag = os.environ.get("CURSOR_TRUST_FLAG", "--trust").strip()
    if trust_flag:
        command.append(trust_flag)
    command.extend(env_args("CURSOR_EXTRA_ARGS"))
    command.append(prompt)
    timeout_seconds = int(os.environ.get("CURSOR_TIMEOUT_SECONDS", "900"))

    stdout, stderr, return_code, elapsed_ms = call_command(command, timeout_seconds)
    if return_code != 0:
        fatal_error(
            "cursor-agent command failed",
            details={
                "return_code": return_code,
                "stderr": stderr.strip(),
                "command": command,
            },
        )

    parsed_payload = parse_agent_payload(stdout)
    parsed_text = parse_agent_output(stdout, parsed_payload=parsed_payload)
    patch, patch_source = extract_git_patch(parsed_text)
    usage_metrics = extract_usage_metrics(parsed_payload)
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
            "stderr": stderr.strip(),
            **merged_metrics,
        },
    )


if __name__ == "__main__":
    main()
