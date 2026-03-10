#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
from typing import Any
import json
import os
import re
import shlex
import subprocess
import sys
import time


def read_payload_from_stdin() -> dict[str, Any]:
    raw = sys.stdin.read().strip()
    if not raw:
        raise ValueError("Wrapper received empty stdin payload")
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise ValueError("Wrapper payload must be a JSON object")
    return payload


def emit_success(patch: str, metadata: dict[str, Any] | None = None) -> None:
    response = {
        "patch": patch,
        "metadata": metadata or {},
    }
    sys.stdout.write(json.dumps(response))


def fatal_error(message: str, details: dict[str, Any] | None = None, exit_code: int = 1) -> None:
    error_payload = {"error": message}
    if details:
        error_payload["details"] = details
    sys.stderr.write(json.dumps(error_payload))
    sys.exit(exit_code)


def env_args(name: str) -> list[str]:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return []
    return shlex.split(raw)


def call_command(command: list[str], timeout_seconds: int) -> tuple[str, str, int, int]:
    start = time.time()
    completed = subprocess.run(
        command,
        text=True,
        capture_output=True,
        timeout=timeout_seconds,
        check=False,
    )
    elapsed_ms = int((time.time() - start) * 1000)
    return completed.stdout, completed.stderr, completed.returncode, elapsed_ms


def first_non_empty_text(data: Any) -> str:
    if isinstance(data, str):
        return data.strip()
    if isinstance(data, list):
        for item in data:
            value = first_non_empty_text(item)
            if value:
                return value
        return ""
    if isinstance(data, dict):
        preferred_keys = (
            "patch",
            "result",
            "output",
            "text",
            "content",
            "message",
            "response",
            "completion",
        )
        for key in preferred_keys:
            if key in data:
                value = first_non_empty_text(data[key])
                if value:
                    return value
        for value in data.values():
            candidate = first_non_empty_text(value)
            if candidate:
                return candidate
    return ""


def parse_agent_output(raw_stdout: str) -> str:
    text = raw_stdout.strip()
    if not text:
        return ""
    parsed = _try_parse_json(text)
    if parsed is not None:
        text = first_non_empty_text(parsed) or ""
    return text.strip()


def _try_parse_json(raw_text: str) -> Any | None:
    try:
        return json.loads(raw_text)
    except json.JSONDecodeError:
        pass

    lines = [line.strip() for line in raw_text.splitlines() if line.strip()]
    if not lines:
        return None
    json_lines = []
    for line in lines:
        try:
            json_lines.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    if not json_lines:
        return None
    if len(json_lines) == 1:
        return json_lines[0]
    return json_lines


def extract_git_patch(raw_text: str) -> tuple[str, str]:
    text = raw_text.strip()
    if not text:
        return "", "empty_output"

    fence_patch = _extract_from_code_fence(text)
    if fence_patch:
        text = fence_patch

    diff_index = text.find("diff --git ")
    if diff_index >= 0:
        return _ensure_trailing_newline(text[diff_index:].strip()), "diff_header"

    header_match = re.search(r"(?m)^---\s.+\n\+\+\+\s.+$", text)
    if header_match:
        start = header_match.start()
        return _ensure_trailing_newline(text[start:].strip()), "unified_header"

    return "", "no_patch_found"


def _extract_from_code_fence(text: str) -> str:
    code_blocks = re.findall(r"```(?:diff|patch)?\n(.*?)```", text, flags=re.DOTALL)
    if not code_blocks:
        return ""
    for block in code_blocks:
        stripped = block.strip()
        if "diff --git " in stripped or re.search(r"(?m)^---\s.+\n\+\+\+\s.+$", stripped):
            return stripped
    return code_blocks[0].strip()


def _ensure_trailing_newline(text: str) -> str:
    return f"{text}\n" if text and not text.endswith("\n") else text


def render_task_prompt(payload: dict[str, Any], wrapper_name: str) -> str:
    instance_id = str(payload.get("instance_id", "unknown"))
    repo = str(payload.get("repo", "unknown"))
    base_commit = str(payload.get("base_commit", "unknown"))
    language = str(payload.get("language", "unknown"))
    problem = str(payload.get("problem_statement", "")).strip()
    extra_notes = payload.get("metadata", {})

    return (
        f"You are {wrapper_name} running in benchmark mode.\n"
        "Task: produce a fix patch only.\n"
        "Output constraints:\n"
        "- Return ONLY a valid unified git diff patch.\n"
        "- Start with 'diff --git'.\n"
        "- Do not add explanations or markdown.\n"
        "- If no fix can be produced, return an empty response.\n\n"
        f"Instance ID: {instance_id}\n"
        f"Repository: {repo}\n"
        f"Base commit: {base_commit}\n"
        f"Language: {language}\n\n"
        f"Issue:\n{problem}\n\n"
        f"Additional metadata:\n{json.dumps(extra_notes, ensure_ascii=False)}"
    )


def resolve_workspace(payload: dict[str, Any]) -> Path:
    run = payload.get("run", {})
    if isinstance(run, dict):
        root = run.get("workspace_root")
        if isinstance(root, str) and root.strip():
            return Path(root).resolve()
    return Path.cwd()
