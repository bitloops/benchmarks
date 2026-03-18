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

CANONICAL_METRIC_KEYS: tuple[str, ...] = (
    "token_input",
    "token_output",
    "estimated_cost",
    "tool_calls",
    "shell_commands",
    "file_reads",
    "search_actions",
)


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


def parse_agent_payload(raw_stdout: str) -> Any | None:
    text = raw_stdout.strip()
    if not text:
        return None
    return _try_parse_json(text)


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


def parse_agent_output(raw_stdout: str, parsed_payload: Any | None = None) -> str:
    text = raw_stdout.strip()
    if not text:
        return ""
    parsed = parsed_payload if parsed_payload is not None else _try_parse_json(text)
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


def extract_usage_metrics(payload: Any) -> dict[str, float | int]:
    if payload is None:
        return {}

    token_input = _find_number_by_paths(
        payload,
        [
            ("usage", "input_tokens"),
            ("usage", "inputTokens"),
            ("usage", "prompt_tokens"),
            ("usage", "promptTokens"),
            ("usage", "tokensIn",),
            ("modelUsage", "*", "inputTokens"),
            ("model_usage", "*", "input_tokens"),
            ("input_tokens",),
            ("inputTokens",),
        ],
    )
    token_output = _find_number_by_paths(
        payload,
        [
            ("usage", "output_tokens"),
            ("usage", "outputTokens"),
            ("usage", "completion_tokens"),
            ("usage", "completionTokens"),
            ("usage", "tokensOut",),
            ("modelUsage", "*", "outputTokens"),
            ("model_usage", "*", "output_tokens"),
            ("output_tokens",),
            ("outputTokens",),
        ],
    )
    estimated_cost = _find_number_by_paths(
        payload,
        [
            ("total_cost_usd",),
            ("totalCostUsd",),
            ("estimated_cost",),
            ("usage", "total_cost_usd"),
            ("usage", "totalCostUsd"),
            ("cost_usd",),
            ("costUSD",),
            ("usage", "cost_usd"),
            ("usage", "costUSD"),
            ("modelUsage", "*", "costUSD"),
            ("model_usage", "*", "cost_usd"),
        ],
    )

    search_actions = _find_number_by_paths(
        payload,
        [
            ("search_actions",),
            ("search_count",),
            ("usage", "search_actions"),
            ("usage", "search_count"),
            ("usage", "searchActions"),
            ("usage", "searchRequests"),
            ("usage", "server_tool_use", "web_search_requests"),
            ("usage", "server_tool_use", "webSearchRequests"),
            ("usage", "server_tool_use", "search_requests"),
            ("usage", "server_tool_use", "searchRequests"),
            ("usage", "web_search_requests"),
            ("usage", "webSearchRequests"),
            ("modelUsage", "*", "webSearchRequests"),
        ],
    )
    web_fetches = _find_number_by_paths(
        payload,
        [
            ("usage", "server_tool_use", "web_fetch_requests"),
            ("usage", "server_tool_use", "webFetchRequests"),
            ("usage", "web_fetch_requests"),
            ("usage", "webFetchRequests"),
        ],
    )
    tool_calls = _find_number_by_paths(
        payload,
        [
            ("num_turns",),
            ("tool_calls",),
            ("total_tool_calls",),
            ("tools_count",),
            ("usage", "tool_calls"),
            ("usage", "toolCalls"),
            ("usage", "total_tool_calls"),
            ("usage", "totalToolCalls"),
        ],
    )

    file_reads = _find_number_by_paths(
        payload,
        [
            ("file_reads",),
            ("files_read",),
            ("file_open_count",),
            ("usage", "file_reads"),
            ("usage", "files_read"),
            ("usage", "fileReads"),
            ("usage", "server_tool_use", "file_reads"),
            ("usage", "server_tool_use", "fileReadRequests"),
            ("usage", "server_tool_use", "read_file_requests"),
            ("usage", "server_tool_use", "readFileRequests"),
        ],
    )
    shell_commands = _find_number_by_paths(
        payload,
        [
            ("shell_commands",),
            ("shell_command_count",),
            ("usage", "shell_commands"),
            ("usage", "shell_command_count"),
            ("usage", "shellCommands"),
            ("usage", "server_tool_use", "shell_commands"),
            ("usage", "server_tool_use", "terminal_commands"),
            ("usage", "server_tool_use", "terminalCommandRequests"),
            ("usage", "server_tool_use", "run_terminal_cmd_requests"),
            ("usage", "server_tool_use", "runTerminalCmdRequests"),
        ],
    )

    classified_tool_usage = _classify_tool_usage_metrics(payload)
    if tool_calls is None:
        tool_calls = classified_tool_usage.get("tool_calls")
    if shell_commands is None:
        shell_commands = classified_tool_usage.get("shell_commands")
    if file_reads is None:
        file_reads = classified_tool_usage.get("file_reads")
    if search_actions is None:
        search_actions = classified_tool_usage.get("search_actions")

    if tool_calls is None:
        derived = 0.0
        has_any = False
        for value in (search_actions, web_fetches, file_reads, shell_commands):
            if value is None:
                continue
            has_any = True
            derived += float(value)
        if has_any:
            tool_calls = int(derived)

    metrics: dict[str, float | int] = {}
    if token_input is not None:
        metrics["token_input"] = token_input
    if token_output is not None:
        metrics["token_output"] = token_output
    if estimated_cost is not None:
        metrics["estimated_cost"] = estimated_cost
    if tool_calls is not None:
        metrics["tool_calls"] = tool_calls
    if search_actions is not None:
        metrics["search_actions"] = search_actions
    if file_reads is not None:
        metrics["file_reads"] = file_reads
    if shell_commands is not None:
        metrics["shell_commands"] = shell_commands
    return metrics


def merge_metric_metadata(*metric_sources: dict[str, Any] | None) -> dict[str, float | int | str]:
    merged: dict[str, float | int | str] = {}

    for key in CANONICAL_METRIC_KEYS:
        for source in metric_sources:
            if not isinstance(source, dict):
                continue
            number = _coerce_number(source.get(key))
            if number is None:
                continue
            merged[key] = number
            break

    for source in metric_sources:
        if not isinstance(source, dict):
            continue
        hook_path = source.get("hook_metrics_path")
        if isinstance(hook_path, str) and hook_path.strip():
            merged["hook_metrics_path"] = hook_path.strip()
            break
    return merged


def load_hook_metrics(
    env_var_names: tuple[str, ...],
) -> dict[str, float | int | str]:
    for env_name in env_var_names:
        raw_path = os.environ.get(env_name, "").strip()
        if not raw_path:
            continue
        path = Path(raw_path).expanduser()
        if not path.is_absolute():
            path = (Path.cwd() / path).resolve()
        if not path.exists():
            return {}
        payload = _load_json_or_jsonl(path)
        metrics = _summarize_hook_payload(payload)
        if metrics:
            metrics["hook_metrics_path"] = str(path)
        return metrics
    return {}


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
    prompt_context = payload.get("prompt_context")

    parts = [
        f"You are {wrapper_name} running in benchmark mode.\n"
        f"You have access to a workspace containing the source code of {repo} "
        f"at commit {base_commit}.\n\n"
        "Task: Investigate and fix the following issue by editing files "
        "directly in the workspace.\n\n"
        "Instructions:\n"
        "- Read the relevant source files to understand the code.\n"
        "- Identify the root cause of the issue described below.\n"
        "- Edit the necessary files to fix the bug.\n"
        "- Do not commit your changes; just leave the edited files in place.\n"
        "- Do not add explanations or commentary to your final response.\n\n"
        f"Instance ID: {instance_id}\n"
        f"Repository: {repo}\n"
        f"Base commit: {base_commit}\n"
        f"Language: {language}\n\n"
        f"Issue:\n{problem}\n\n"
        f"Additional metadata:\n{json.dumps(extra_notes, ensure_ascii=False)}"
    ]

    if isinstance(prompt_context, str) and prompt_context.strip():
        parts.append(f"\n\nAdditional context:\n{prompt_context.strip()}")

    return "".join(parts)


def resolve_workspace(payload: dict[str, Any]) -> Path:
    run = payload.get("run", {})
    if isinstance(run, dict):
        root = run.get("workspace_root")
        if isinstance(root, str) and root.strip():
            return Path(root).resolve()
    return Path.cwd()


def reset_workspace(workspace: Path, git_bin: str = "git") -> None:
    """Reset workspace to a clean state (HEAD commit, no uncommitted changes)."""
    subprocess.run(
        [git_bin, "reset", "--hard", "HEAD"],
        cwd=str(workspace),
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
    )
    subprocess.run(
        [git_bin, "clean", "-fd"],
        cwd=str(workspace),
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
    )


def capture_workspace_patch(workspace: Path, git_bin: str = "git") -> str:
    """Capture uncommitted changes in the workspace via ``git diff``."""
    result = subprocess.run(
        [git_bin, "diff"],
        cwd=str(workspace),
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    return result.stdout.strip()


def _find_number_by_paths(
    payload: Any,
    paths: list[tuple[str, ...]],
) -> int | float | None:
    for path in paths:
        for resolver in (_value_for_path, _value_for_path_anywhere):
            value = resolver(payload, path)
            number = _coerce_number(value)
            if number is not None:
                return number
    return None


def _classify_tool_usage_metrics(payload: Any) -> dict[str, int]:
    usage_blocks: list[dict[str, Any]] = []
    seen_block_ids: set[int] = set()
    for path in (
        ("usage", "server_tool_use"),
        ("usage", "serverToolUse"),
        ("server_tool_use",),
        ("serverToolUse",),
        ("usage", "tool_usage"),
        ("usage", "toolUsage"),
        ("tool_usage",),
        ("toolUsage",),
    ):
        block = _find_dict_by_path(payload, path)
        if block and id(block) not in seen_block_ids:
            seen_block_ids.add(id(block))
            usage_blocks.append(block)

    totals = {
        "tool_calls": 0,
        "shell_commands": 0,
        "file_reads": 0,
        "search_actions": 0,
    }
    for block in usage_blocks:
        for raw_key, raw_value in block.items():
            count = _coerce_number(raw_value)
            if count is None:
                continue
            count_int = int(float(count))
            key = str(raw_key or "").strip().lower()
            if not key:
                continue
            totals["tool_calls"] += count_int
            if any(token in key for token in ("terminal", "shell", "bash", "cmd")):
                totals["shell_commands"] += count_int
            if any(token in key for token in ("search", "grep", "find")):
                totals["search_actions"] += count_int
            if any(token in key for token in ("file", "read", "open", "view")):
                totals["file_reads"] += count_int

    return {k: v for k, v in totals.items() if v > 0}


def _find_dict_by_path(payload: Any, path: tuple[str, ...]) -> dict[str, Any] | None:
    for resolver in (_value_for_path, _value_for_path_anywhere):
        value = resolver(payload, path)
        if isinstance(value, dict):
            return value
    return None


def _value_for_path(data: Any, path: tuple[str, ...]) -> Any:
    if not path:
        return data
    head, *tail = path
    if head == "*":
        if isinstance(data, dict):
            for value in data.values():
                candidate = _value_for_path(value, tuple(tail))
                if candidate is not None:
                    return candidate
        elif isinstance(data, list):
            for value in data:
                candidate = _value_for_path(value, tuple(tail))
                if candidate is not None:
                    return candidate
        return None
    if isinstance(data, dict) and head in data:
        return _value_for_path(data[head], tuple(tail))
    return None


def _value_for_path_anywhere(data: Any, path: tuple[str, ...]) -> Any:
    direct = _value_for_path(data, path)
    if direct is not None:
        return direct
    if isinstance(data, dict):
        for value in data.values():
            candidate = _value_for_path_anywhere(value, path)
            if candidate is not None:
                return candidate
    if isinstance(data, list):
        for value in data:
            candidate = _value_for_path_anywhere(value, path)
            if candidate is not None:
                return candidate
    return None


def _coerce_number(value: Any) -> int | float | None:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return value
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            return int(text)
        except ValueError:
            try:
                return float(text)
            except ValueError:
                return None
    return None


def _load_json_or_jsonl(path: Path) -> Any:
    text = path.read_text(encoding="utf-8")
    stripped = text.strip()
    if not stripped:
        return None
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        rows: list[Any] = []
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return rows


def _summarize_hook_payload(payload: Any) -> dict[str, float | int]:
    if payload is None:
        return {}

    if isinstance(payload, dict):
        direct = extract_usage_metrics(payload)
        # Allow pre-aggregated hook summaries with canonical keys.
        for key in CANONICAL_METRIC_KEYS:
            number = _coerce_number(payload.get(key))
            if number is not None:
                direct[key] = number
        return direct

    if not isinstance(payload, list):
        return {}

    summary: dict[str, float | int] = {
        "tool_calls": 0,
        "shell_commands": 0,
        "file_reads": 0,
        "search_actions": 0,
    }
    for item in payload:
        if not isinstance(item, dict):
            continue
        event_type = str(
            item.get("type")
            or item.get("event")
            or item.get("action")
            or item.get("name")
            or ""
        ).lower()
        command = str(item.get("command") or "")
        path = str(item.get("path") or item.get("file") or "")
        query = str(item.get("query") or item.get("pattern") or "")

        if event_type:
            summary["tool_calls"] = int(summary["tool_calls"]) + 1
        if any(token in event_type for token in ("shell", "bash", "terminal")) or command:
            summary["shell_commands"] = int(summary["shell_commands"]) + 1
        if any(token in event_type for token in ("search", "grep", "find")) or query:
            summary["search_actions"] = int(summary["search_actions"]) + 1
        if any(token in event_type for token in ("read", "open", "view")) or path:
            summary["file_reads"] = int(summary["file_reads"]) + 1

        for key in ("token_input", "token_output", "estimated_cost"):
            number = _coerce_number(item.get(key))
            if number is None:
                continue
            summary[key] = summary.get(key, 0) + number
    return summary
