#!/usr/bin/env python3
from __future__ import annotations

import os

from common import (  # type: ignore[import-not-found]
    call_command,
    emit_success,
    env_args,
    extract_git_patch,
    fatal_error,
    parse_agent_output,
    read_payload_from_stdin,
    render_task_prompt,
)


def main() -> None:
    payload = read_payload_from_stdin()
    model = payload.get("model", {})
    canonical_model_name = str(model.get("canonical_name", "")).strip()
    model_name = (
        str(model.get("name", "")).strip()
        or os.environ.get("CLAUDE_MODEL", "").strip()
        or "claude-opus-4-6"
    )

    prompt = render_task_prompt(payload, wrapper_name="claude_code")
    command = [
        os.environ.get("CLAUDE_BIN", "claude"),
        "--print",
        "--output-format",
        "json",
        "--model",
        model_name,
    ]
    command.extend(env_args("CLAUDE_EXTRA_ARGS"))
    command.append(prompt)
    timeout_seconds = int(os.environ.get("CLAUDE_TIMEOUT_SECONDS", "900"))

    stdout, stderr, return_code, elapsed_ms = call_command(command, timeout_seconds)
    if return_code != 0:
        fatal_error(
            "claude command failed",
            details={
                "return_code": return_code,
                "stderr": stderr.strip(),
                "command": command,
            },
        )

    parsed_text = parse_agent_output(stdout)
    patch, patch_source = extract_git_patch(parsed_text)

    emit_success(
        patch=patch,
        metadata={
            "wrapper": "claude_code",
            "command": command,
            "canonical_model_name": canonical_model_name or model_name,
            "resolved_model_name": model_name,
            "elapsed_ms": elapsed_ms,
            "patch_source": patch_source,
            "stderr": stderr.strip(),
        },
    )


if __name__ == "__main__":
    main()
