#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
from typing import Any
from copy import deepcopy
import json
import os
import re
import shlex
import subprocess
import sys
import time
import urllib.request

CANONICAL_METRIC_KEYS: tuple[str, ...] = (
    "token_input",
    "token_output",
    "estimated_cost",
    "cache_creation_input_tokens",
    "cache_read_input_tokens",
    "cache_creation_ephemeral_5m_input_tokens",
    "cache_creation_ephemeral_1h_input_tokens",
    "tool_calls",
    "shell_commands",
    "file_reads",
    "search_actions",
)

DEBUG_LOG_PATH = Path("/Users/petros/Desktop/work/benchmarks/.cursor/debug-1440e1.log")
DEBUG_SESSION_ID = "1440e1"
DEBUG_RUN_ID = os.environ.get("BENCHKIT_RUN_ID", "unknown")
DEBUG_SERVER_ENDPOINT = "http://127.0.0.1:7347/ingest/b923941d-c2ed-47b2-9859-b78772d71f43"
DEBUG_HTTP_FALLBACK_ENV_VAR = "BENCHKIT_DEBUG_HTTP_FALLBACK"
DEBUG_SERVER_ENDPOINT_ENV_VAR = "BENCHKIT_DEBUG_SERVER_ENDPOINT"


def _debug_log(*, hypothesis_id: str, location: str, message: str, data: dict[str, Any]) -> None:
    payload = {
        "sessionId": DEBUG_SESSION_ID,
        "runId": DEBUG_RUN_ID,
        "hypothesisId": hypothesis_id,
        "location": location,
        "message": message,
        "data": data,
        "timestamp": int(time.time() * 1000),
    }
    try:
        DEBUG_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with DEBUG_LOG_PATH.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
        return
    except OSError:
        pass
    if str(os.environ.get(DEBUG_HTTP_FALLBACK_ENV_VAR, "")).strip().lower() not in {
        "1",
        "true",
        "yes",
        "on",
    }:
        return
    endpoint = os.environ.get(DEBUG_SERVER_ENDPOINT_ENV_VAR, DEBUG_SERVER_ENDPOINT).strip()
    if not endpoint:
        return
    try:
        request = urllib.request.Request(
            endpoint,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "X-Debug-Session-Id": DEBUG_SESSION_ID,
            },
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=2):
            return
    except OSError:
        pass


def _debug_payload_shape(payload: Any) -> dict[str, Any]:
    if isinstance(payload, list):
        sample_types = []
        for item in payload[:5]:
            if isinstance(item, dict):
                sample_types.append(str(item.get("type") or item.get("event") or "dict"))
            else:
                sample_types.append(type(item).__name__)
        return {
            "kind": "list",
            "len": len(payload),
            "sample_types": sample_types,
        }
    if isinstance(payload, dict):
        return {
            "kind": "dict",
            "keys": sorted(str(key) for key in list(payload.keys())[:10]),
        }
    return {"kind": type(payload).__name__}


def _collect_numeric_candidates(
    payload: Any,
    *,
    keys: set[str],
    limit: int = 20,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []

    def walk(node: Any, path: str) -> None:
        if len(results) >= limit:
            return
        if isinstance(node, dict):
            for key, value in node.items():
                next_path = f"{path}.{key}" if path else str(key)
                if str(key) in keys:
                    number = _coerce_number(value)
                    if number is not None:
                        results.append({"path": next_path, "value": number})
                        if len(results) >= limit:
                            return
                walk(value, next_path)
                if len(results) >= limit:
                    return
        elif isinstance(node, list):
            for index, value in enumerate(node):
                walk(value, f"{path}[{index}]")
                if len(results) >= limit:
                    return

    walk(payload, "")
    return results


def _collect_metric_sources(
    payload: Any,
    *,
    paths: list[tuple[str, ...]],
    limit: int = 20,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    events = payload if isinstance(payload, list) else [payload]
    for index, event in enumerate(events):
        if len(rows) >= limit:
            break
        event_type = None
        if isinstance(event, dict):
            event_type = str(event.get("type") or event.get("event") or "").strip() or None
        for path in paths:
            if len(rows) >= limit:
                break
            values = _values_for_path_anywhere(event, path)
            for value in values:
                number = _coerce_number(value)
                if number is None:
                    continue
                rows.append(
                    {
                        "event_index": index,
                        "event_type": event_type,
                        "path": ".".join(path),
                        "value": number,
                    }
                )
                if len(rows) >= limit:
                    break
    return rows


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


def env_flag(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    text = str(raw).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    return default


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


def setup_bitloops_for_workspace(
    *,
    agent_name: str,
    bitloops_bin: str | None = None,
    timeout_seconds: int | None = None,
) -> dict[str, Any]:
    binary = (bitloops_bin or os.environ.get("BITLOOPS_BIN", "bitloops")).strip() or "bitloops"
    timeout = timeout_seconds or int(os.environ.get("BITLOOPS_SETUP_TIMEOUT_SECONDS", "180"))
    setup_started = time.time()

    status_command = [binary, "status"]
    status_stdout, status_stderr, status_code, status_elapsed_ms = call_command(
        status_command,
        timeout,
    )
    daemon_running = status_code == 0 and _bitloops_daemon_is_running(status_stdout)

    daemon_start_attempted = False
    daemon_bootstrap_attempted = False
    daemon_start_mode = "already_running" if daemon_running else "not_running"
    daemon_start_elapsed_ms = 0
    daemon_start_command: list[str] | None = None
    daemon_bootstrap_command: list[str] | None = None
    git_detached_head = False
    git_checkout_attempted = False
    git_branch_checkout_command: list[str] | None = None
    git_checked_out_branch: str | None = None
    git_checkout_elapsed_ms = 0

    if not daemon_running:
        daemon_start_attempted = True
        start_command = [binary, "start", "--detached"]
        daemon_start_command = start_command
        start_stdout, start_stderr, start_code, start_elapsed_ms = call_command(
            start_command,
            timeout,
        )
        daemon_start_elapsed_ms += start_elapsed_ms

        if start_code == 0:
            daemon_start_mode = "start_detached"
        elif _bitloops_daemon_needs_bootstrap(start_stdout, start_stderr):
            daemon_bootstrap_attempted = True
            bootstrap_command = [
                binary,
                "start",
                "--create-default-config",
                "--telemetry=false",
                "--detached",
            ]
            daemon_bootstrap_command = bootstrap_command
            (
                bootstrap_stdout,
                bootstrap_stderr,
                bootstrap_code,
                bootstrap_elapsed_ms,
            ) = call_command(bootstrap_command, timeout)
            daemon_start_elapsed_ms += bootstrap_elapsed_ms
            if bootstrap_code != 0:
                raise RuntimeError(
                    "bitloops daemon bootstrap failed: "
                    + _serialize_command_failure(
                        command=bootstrap_command,
                        stdout=bootstrap_stdout,
                        stderr=bootstrap_stderr,
                        return_code=bootstrap_code,
                    )
                )
            daemon_start_mode = "start_create_default_config"
        else:
            raise RuntimeError(
                "bitloops daemon start failed: "
                + _serialize_command_failure(
                    command=start_command,
                    stdout=start_stdout,
                    stderr=start_stderr,
                    return_code=start_code,
                )
            )

    (
        git_detached_head,
        git_checkout_attempted,
        git_branch_checkout_command,
        git_checked_out_branch,
        git_checkout_elapsed_ms,
    ) = _ensure_git_branch_for_bitloops_sync(timeout_seconds=timeout)

    include_install_default_daemon = True
    include_ingest_flag = True
    init_fallback_used = False
    init_command: list[str] = []
    init_stdout = ""
    init_stderr = ""
    init_code = 1
    init_elapsed_ms = 0

    while True:
        init_command = [
            binary,
            "init",
            "--agent",
            agent_name,
            "--telemetry=false",
            "--sync=true",
        ]
        if include_install_default_daemon:
            init_command.append("--install-default-daemon")
        if include_ingest_flag:
            init_command.append("--ingest=false")

        (
            init_stdout,
            init_stderr,
            init_code,
            current_init_elapsed_ms,
        ) = call_command(init_command, timeout)
        init_elapsed_ms += current_init_elapsed_ms
        if init_code == 0:
            break

        fallback_applied = False
        if include_ingest_flag and _bitloops_init_rejects_ingest_flag(init_stdout, init_stderr):
            include_ingest_flag = False
            fallback_applied = True
        if include_install_default_daemon and _bitloops_init_rejects_install_default_daemon_flag(
            init_stdout,
            init_stderr,
        ):
            include_install_default_daemon = False
            fallback_applied = True

        if not fallback_applied:
            break
        init_fallback_used = True

    if init_code != 0:
        raise RuntimeError(
            "bitloops init failed: "
            + _serialize_command_failure(
                command=init_command,
                stdout=init_stdout,
                stderr=init_stderr,
                return_code=init_code,
            )
        )

    setup_elapsed_ms = int((time.time() - setup_started) * 1000)
    return {
        "bitloops_enabled": True,
        "bitloops_agent": agent_name,
        "bitloops_daemon_was_running": daemon_running,
        "bitloops_daemon_start_attempted": daemon_start_attempted,
        "bitloops_daemon_bootstrap_attempted": daemon_bootstrap_attempted,
        "bitloops_daemon_start_mode": daemon_start_mode,
        "bitloops_status_command": status_command,
        "bitloops_start_command": daemon_start_command,
        "bitloops_bootstrap_command": daemon_bootstrap_command,
        "bitloops_init_command": init_command,
        "bitloops_init_fallback_used": init_fallback_used,
        "bitloops_git_detached_head": git_detached_head,
        "bitloops_git_checkout_attempted": git_checkout_attempted,
        "bitloops_git_checkout_command": git_branch_checkout_command,
        "bitloops_git_checked_out_branch": git_checked_out_branch,
        "bitloops_git_checkout_elapsed_ms": git_checkout_elapsed_ms,
        "bitloops_status_elapsed_ms": status_elapsed_ms,
        "bitloops_daemon_start_elapsed_ms": daemon_start_elapsed_ms,
        "bitloops_init_elapsed_ms": init_elapsed_ms,
        "bitloops_setup_elapsed_ms": setup_elapsed_ms,
    }


def _bitloops_daemon_is_running(status_stdout: str) -> bool:
    text = status_stdout.strip().lower()
    return "bitloops daemon: running" in text


def _bitloops_daemon_needs_bootstrap(stdout: str, stderr: str) -> bool:
    text = "\n".join((stdout, stderr)).strip().lower()
    return (
        "has not been bootstrapped yet" in text
        or "start --create-default-config" in text
    )


def _bitloops_init_rejects_ingest_flag(stdout: str, stderr: str) -> bool:
    text = "\n".join((stdout, stderr)).strip().lower()
    return "unexpected argument '--ingest'" in text


def _bitloops_init_rejects_install_default_daemon_flag(
    stdout: str,
    stderr: str,
) -> bool:
    text = "\n".join((stdout, stderr)).strip().lower()
    return "unexpected argument '--install-default-daemon'" in text


def _ensure_git_branch_for_bitloops_sync(
    *,
    timeout_seconds: int,
) -> tuple[bool, bool, list[str] | None, str | None, int]:
    detached = _git_head_is_detached(timeout_seconds)
    if not detached:
        return False, False, None, None, 0

    elapsed_ms_total = 0
    short_sha = "detached"
    stdout, _stderr, code, elapsed_ms = call_command(
        ["git", "rev-parse", "--short=12", "HEAD"],
        timeout_seconds,
    )
    elapsed_ms_total += elapsed_ms
    if code == 0 and stdout.strip():
        short_sha = stdout.strip()

    branch_name = f"benchkit-bitloops-{short_sha}"
    create_command = ["git", "switch", "-c", branch_name]
    create_stdout, create_stderr, create_code, create_elapsed_ms = call_command(
        create_command,
        timeout_seconds,
    )
    elapsed_ms_total += create_elapsed_ms
    if create_code == 0:
        return True, True, create_command, branch_name, elapsed_ms_total

    switch_command = ["git", "switch", branch_name]
    switch_stdout, switch_stderr, switch_code, switch_elapsed_ms = call_command(
        switch_command,
        timeout_seconds,
    )
    elapsed_ms_total += switch_elapsed_ms
    if switch_code == 0:
        return True, True, switch_command, branch_name, elapsed_ms_total

    raise RuntimeError(
        "unable to attach detached HEAD before bitloops sync: "
        + _serialize_command_failure(
            command=switch_command,
            stdout=switch_stdout or create_stdout,
            stderr=switch_stderr or create_stderr,
            return_code=switch_code or create_code,
        )
    )


def _git_head_is_detached(timeout_seconds: int) -> bool:
    _stdout, _stderr, code, _elapsed_ms = call_command(
        ["git", "symbolic-ref", "--quiet", "--short", "HEAD"],
        timeout_seconds,
    )
    return code != 0


def _serialize_command_failure(
    *,
    command: list[str],
    stdout: str,
    stderr: str,
    return_code: int,
) -> str:
    summary = summarize_command_failure(stdout, stderr)
    payload: dict[str, Any] = {
        "return_code": return_code,
        "command": command,
    }
    payload.update(summary)
    return json.dumps(payload, ensure_ascii=False)


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


def summarize_command_failure(stdout: str, stderr: str) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    stderr_text = stderr.strip()
    stdout_text = stdout.strip()
    if stderr_text:
        summary["stderr"] = stderr_text
    if not stdout_text:
        return summary

    parsed = _try_parse_json(stdout_text)
    if isinstance(parsed, dict):
        summary["stdout_json"] = parsed
        result = parsed.get("result")
        if isinstance(result, str) and result.strip():
            summary["stdout_result"] = result.strip()
    else:
        summary["stdout"] = stdout_text
    return summary


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


def extract_usage_metrics(payload: Any) -> dict[str, float | int | str]:
    if payload is None:
        return {}

    token_input_paths = [
        ("usage", "input_tokens"),
        ("usage", "inputTokens"),
        ("usage", "prompt_tokens"),
        ("usage", "promptTokens"),
        ("usage", "tokensIn",),
        ("input_tokens",),
        ("inputTokens",),
        ("modelUsage", "*", "inputTokens"),
        ("model_usage", "*", "input_tokens"),
    ]
    token_output_paths = [
        ("usage", "output_tokens"),
        ("usage", "outputTokens"),
        ("usage", "completion_tokens"),
        ("usage", "completionTokens"),
        ("usage", "tokensOut",),
        ("output_tokens",),
        ("outputTokens",),
        ("modelUsage", "*", "outputTokens"),
        ("model_usage", "*", "output_tokens"),
    ]
    estimated_cost_paths = [
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
    ]
    cache_creation_input_paths = [
        ("usage", "cache_creation_input_tokens"),
        ("usage", "cacheCreationInputTokens"),
        ("cache_creation_input_tokens",),
        ("cacheCreationInputTokens",),
    ]
    cache_read_input_paths = [
        ("usage", "cache_read_input_tokens"),
        ("usage", "cacheReadInputTokens"),
        ("cache_read_input_tokens",),
        ("cacheReadInputTokens",),
    ]
    cache_write_5m_paths = [
        ("usage", "cache_creation", "ephemeral_5m_input_tokens"),
        ("usage", "cache_creation", "ephemeral5mInputTokens"),
        ("usage", "cacheCreation", "ephemeral_5m_input_tokens"),
        ("usage", "cacheCreation", "ephemeral5mInputTokens"),
        ("cache_creation_ephemeral_5m_input_tokens",),
        ("cacheCreationEphemeral5mInputTokens",),
    ]
    cache_write_1h_paths = [
        ("usage", "cache_creation", "ephemeral_1h_input_tokens"),
        ("usage", "cache_creation", "ephemeral1hInputTokens"),
        ("usage", "cacheCreation", "ephemeral_1h_input_tokens"),
        ("usage", "cacheCreation", "ephemeral1hInputTokens"),
        ("cache_creation_ephemeral_1h_input_tokens",),
        ("cacheCreationEphemeral1hInputTokens",),
    ]
    search_actions_paths = [
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
    ]
    web_fetches_paths = [
        ("usage", "server_tool_use", "web_fetch_requests"),
        ("usage", "server_tool_use", "webFetchRequests"),
        ("usage", "web_fetch_requests"),
        ("usage", "webFetchRequests"),
    ]
    tool_calls_paths = [
        ("num_turns",),
        ("tool_calls",),
        ("total_tool_calls",),
        ("tools_count",),
        ("usage", "tool_calls"),
        ("usage", "toolCalls"),
        ("usage", "total_tool_calls"),
        ("usage", "totalToolCalls"),
    ]
    file_reads_paths = [
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
    ]
    shell_commands_paths = [
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
    ]

    token_input, token_input_source = _extract_number_with_source(payload, token_input_paths)
    token_output, token_output_source = _extract_number_with_source(payload, token_output_paths)
    estimated_cost, _ = _extract_number_with_source(payload, estimated_cost_paths)
    cache_creation_input_tokens, _ = _extract_number_with_source(payload, cache_creation_input_paths)
    cache_read_input_tokens, _ = _extract_number_with_source(payload, cache_read_input_paths)
    cache_creation_ephemeral_5m_input_tokens, _ = _extract_number_with_source(payload, cache_write_5m_paths)
    cache_creation_ephemeral_1h_input_tokens, _ = _extract_number_with_source(payload, cache_write_1h_paths)
    search_actions, _ = _extract_number_with_source(payload, search_actions_paths)
    web_fetches, _ = _extract_number_with_source(payload, web_fetches_paths)
    tool_calls, _ = _extract_number_with_source(payload, tool_calls_paths)
    file_reads, _ = _extract_number_with_source(payload, file_reads_paths)
    shell_commands, _ = _extract_number_with_source(payload, shell_commands_paths)

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

    metrics: dict[str, float | int | str] = {}
    if token_input is not None:
        metrics["token_input"] = token_input
    if token_output is not None:
        metrics["token_output"] = token_output
    if estimated_cost is not None:
        metrics["estimated_cost"] = estimated_cost
    if cache_creation_input_tokens is not None:
        metrics["cache_creation_input_tokens"] = cache_creation_input_tokens
    if cache_read_input_tokens is not None:
        metrics["cache_read_input_tokens"] = cache_read_input_tokens
    if cache_creation_ephemeral_5m_input_tokens is not None:
        metrics["cache_creation_ephemeral_5m_input_tokens"] = (
            cache_creation_ephemeral_5m_input_tokens
        )
    if cache_creation_ephemeral_1h_input_tokens is not None:
        metrics["cache_creation_ephemeral_1h_input_tokens"] = (
            cache_creation_ephemeral_1h_input_tokens
        )
    if tool_calls is not None:
        metrics["tool_calls"] = tool_calls
    if search_actions is not None:
        metrics["search_actions"] = search_actions
    if file_reads is not None:
        metrics["file_reads"] = file_reads
    if shell_commands is not None:
        metrics["shell_commands"] = shell_commands
    token_metrics_source = _summarize_token_metric_source(
        token_input_source,
        token_output_source,
    )
    if token_metrics_source:
        metrics["token_metrics_source"] = token_metrics_source

    candidate_keys = {
        "input_tokens",
        "inputTokens",
        "output_tokens",
        "outputTokens",
        "total_cost_usd",
        "totalCostUsd",
        "estimated_cost",
        "cost_usd",
        "costUSD",
        "cache_creation_input_tokens",
        "cacheCreationInputTokens",
        "cache_read_input_tokens",
        "cacheReadInputTokens",
        "ephemeral_5m_input_tokens",
        "ephemeral5mInputTokens",
        "ephemeral_1h_input_tokens",
        "ephemeral1hInputTokens",
    }
    # region agent log
    _debug_log(
        hypothesis_id="H1",
        location="scripts/agents/common.py:extract_usage_metrics",
        message="usage extraction summary",
        data={
            "payload_shape": _debug_payload_shape(payload),
            "metrics": metrics,
            "candidates": _collect_numeric_candidates(payload, keys=candidate_keys),
            "metric_sources": {
                "token_input": _collect_metric_sources(
                    payload,
                    paths=[
                        ("usage", "input_tokens"),
                        ("usage", "inputTokens"),
                        ("usage", "prompt_tokens"),
                        ("usage", "promptTokens"),
                        ("usage", "tokensIn"),
                        ("modelUsage", "*", "inputTokens"),
                        ("model_usage", "*", "input_tokens"),
                        ("input_tokens",),
                        ("inputTokens",),
                    ],
                ),
                "token_output": _collect_metric_sources(
                    payload,
                    paths=[
                        ("usage", "output_tokens"),
                        ("usage", "outputTokens"),
                        ("usage", "completion_tokens"),
                        ("usage", "completionTokens"),
                        ("usage", "tokensOut"),
                        ("modelUsage", "*", "outputTokens"),
                        ("model_usage", "*", "output_tokens"),
                        ("output_tokens",),
                        ("outputTokens",),
                    ],
                ),
                "estimated_cost": _collect_metric_sources(
                    payload,
                    paths=[
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
                ),
            },
            "token_metrics_source": token_metrics_source,
        },
    )
    # endregion
    return metrics


def extract_tool_usage_breakdown(payload: Any) -> dict[str, int]:
    breakdown: dict[str, int] = {}
    for block in _collect_tool_usage_blocks(payload):
        for raw_key, raw_value in block.items():
            number = _coerce_number(raw_value)
            if number is None:
                continue
            key = str(raw_key or "").strip()
            if not key:
                continue
            breakdown[key] = int(breakdown.get(key, 0)) + int(float(number))
    return {key: value for key, value in sorted(breakdown.items()) if value > 0}


def extract_tool_invocations_raw(payload: Any) -> list[dict[str, Any]]:
    events = _collect_tool_use_events(payload)
    invocations: list[dict[str, Any]] = []
    for index, event in enumerate(events, start=1):
        tool_name = _extract_tool_name(event)
        if not tool_name:
            continue
        input_payload = _extract_tool_input_payload(event)
        invocation = {
            "call_index": index,
            "tool": tool_name,
            "tool_use_id": _extract_tool_use_id(event),
            "event_type": _normalize_event_type(event.get("type")),
            "input": deepcopy(input_payload),
            "raw_event": deepcopy(event),
        }
        invocations.append(invocation)
    return invocations


def extract_tool_invocations_curated(
    invocations_raw: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    curated: list[dict[str, Any]] = []
    for item in invocations_raw:
        call_index = _coerce_int(item.get("call_index")) or (len(curated) + 1)
        tool_name = str(item.get("tool") or "").strip()
        if not tool_name:
            continue
        tool_use_id = item.get("tool_use_id")
        if isinstance(tool_use_id, str):
            tool_use_id = tool_use_id.strip() or None
        else:
            tool_use_id = None

        input_payload = item.get("input")
        normalized_input = input_payload if isinstance(input_payload, dict) else {}
        record: dict[str, Any] = {
            "call_index": call_index,
            "tool": tool_name,
            "tool_use_id": tool_use_id,
        }

        tool_key = tool_name.strip().lower()
        if tool_key == "grep":
            _populate_grep_curated(record, normalized_input)
        elif tool_key == "read":
            _populate_read_curated(record, normalized_input)
        elif tool_key == "bash":
            _populate_bash_curated(record, normalized_input)
        elif tool_key == "edit":
            _populate_edit_curated(record, normalized_input)
        else:
            _populate_fallback_curated(record, input_payload)

        if len(record.keys()) <= 3:
            _populate_fallback_curated(record, input_payload)

        curated.append(record)
    return curated


def extract_tool_invocation_sequence(payload: Any) -> list[str]:
    return [
        str(item.get("tool")).strip()
        for item in extract_tool_invocations_raw(payload)
        if isinstance(item.get("tool"), str) and str(item.get("tool")).strip()
    ]


def summarize_tool_invocation_counts(sequence: list[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for name in sequence:
        key = str(name).strip()
        if not key:
            continue
        counts[key] = int(counts.get(key, 0)) + 1
    return {key: counts[key] for key in sorted(counts.keys())}


def validate_exact_tool_capture(
    *,
    require_exact_tools: bool,
    output_format: str,
    parsed_payload: Any,
    reported_tool_total: int,
    invocations_raw: list[dict[str, Any]],
    invocations_curated: list[dict[str, Any]],
    tool_usage_breakdown: dict[str, int],
) -> str | None:
    if not require_exact_tools:
        return None
    if output_format != "stream-json":
        return "exact tool capture requires stream-json output"
    if parsed_payload is None:
        return "Unable to parse Claude stream-json payload."
    if reported_tool_total > 0 and not invocations_raw:
        return (
            "Bedrock exact tool capture is required, but no per-tool events "
            "were captured from Claude stream output."
        )
    if reported_tool_total > 0 and not invocations_curated:
        return (
            "Bedrock exact tool capture is required, but normalized per-tool details "
            "were not produced."
        )
    if reported_tool_total == 0 and not invocations_raw and not tool_usage_breakdown:
        return (
            "Exact tool capture is enabled, but neither per-tool events nor "
            "tool usage summary metrics were available."
        )
    return None


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

    for source in metric_sources:
        if not isinstance(source, dict):
            continue
        token_metrics_source = source.get("token_metrics_source")
        if isinstance(token_metrics_source, str) and token_metrics_source.strip():
            merged["token_metrics_source"] = token_metrics_source.strip()
            break

    # region agent log
    _debug_log(
        hypothesis_id="H2",
        location="scripts/agents/common.py:merge_metric_metadata",
        message="metric merge result",
        data={
            "sources": [
                {
                    key: source.get(key)
                    for key in (
                        "token_input",
                        "token_output",
                        "estimated_cost",
                        "cache_creation_input_tokens",
                        "cache_read_input_tokens",
                        "cache_creation_ephemeral_5m_input_tokens",
                        "cache_creation_ephemeral_1h_input_tokens",
                        "tool_calls",
                        "hook_metrics_path",
                        "token_metrics_source",
                    )
                }
                for source in metric_sources
                if isinstance(source, dict)
            ],
            "merged": merged,
        },
    )
    # endregion
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
        numbers = _numbers_for_path(payload, path, use_anywhere=True)
        if numbers:
            return numbers[0]
    return None


def _extract_number_with_source(
    payload: Any,
    paths: list[tuple[str, ...]],
) -> tuple[int | float | None, str | None]:
    result_event = _select_terminal_result_event(payload)
    non_model_usage_paths, model_usage_paths = _split_model_usage_paths(paths)

    if result_event is not None:
        number = _find_number_by_paths_direct(result_event, non_model_usage_paths)
        if number is not None:
            return number, "result_usage"

        model_usage_total = _aggregate_model_usage_number(result_event, model_usage_paths)
        if model_usage_total is not None:
            return model_usage_total, "result_model_usage"

    scanned = _find_number_by_paths(payload, paths)
    if scanned is not None:
        return scanned, "fallback_scan"

    max_fallback = _find_max_number_by_paths(payload, paths)
    if max_fallback is not None:
        return max_fallback, "fallback_max_candidate"
    return None, None


def _summarize_token_metric_source(
    token_input_source: str | None,
    token_output_source: str | None,
) -> str | None:
    if token_input_source and token_output_source:
        if token_input_source == token_output_source:
            return token_input_source
        return f"mixed(input={token_input_source},output={token_output_source})"
    return token_input_source or token_output_source


def _split_model_usage_paths(
    paths: list[tuple[str, ...]],
) -> tuple[list[tuple[str, ...]], list[tuple[str, ...]]]:
    non_model_usage_paths: list[tuple[str, ...]] = []
    model_usage_paths: list[tuple[str, ...]] = []
    for path in paths:
        if path and path[0] in {"modelUsage", "model_usage"}:
            model_usage_paths.append(path)
        else:
            non_model_usage_paths.append(path)
    return non_model_usage_paths, model_usage_paths


def _select_terminal_result_event(payload: Any) -> dict[str, Any] | None:
    result_events: list[dict[str, Any]] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            if _normalize_event_type(node.get("type")) == "result":
                result_events.append(node)
            for value in node.values():
                walk(value)
            return
        if isinstance(node, list):
            for item in node:
                walk(item)

    walk(payload)
    if not result_events:
        return None
    for event in reversed(result_events):
        if _is_successful_result_event(event):
            return event
    return result_events[-1]


def _is_successful_result_event(event: dict[str, Any]) -> bool:
    if _coerce_bool(event.get("is_error")) is True:
        return False
    if _coerce_bool(event.get("error")) is True:
        return False

    subtype = str(event.get("subtype") or "").strip().lower()
    status = str(event.get("status") or "").strip().lower()
    if subtype in {"error", "failed", "failure"}:
        return False
    if status in {"error", "failed", "failure"}:
        return False
    return True


def _aggregate_model_usage_number(
    payload: Any,
    paths: list[tuple[str, ...]],
) -> int | float | None:
    for path in paths:
        numbers = _numbers_for_path(payload, path, use_anywhere=False)
        if numbers:
            return _sum_numbers(numbers)
    return None


def _find_max_number_by_paths(
    payload: Any,
    paths: list[tuple[str, ...]],
) -> int | float | None:
    for path in paths:
        numbers = _numbers_for_path(payload, path, use_anywhere=True)
        if numbers:
            return max(numbers)
    return None


def _find_number_by_paths_direct(
    payload: Any,
    paths: list[tuple[str, ...]],
) -> int | float | None:
    for path in paths:
        value = _value_for_path(payload, path)
        number = _coerce_number(value)
        if number is not None:
            return number
    return None


def _numbers_for_path(
    payload: Any,
    path: tuple[str, ...],
    *,
    use_anywhere: bool,
) -> list[int | float]:
    values: list[Any]
    if use_anywhere:
        values = _values_for_path_anywhere(payload, path)
    else:
        values = _values_for_path(payload, path)

    numbers: list[int | float] = []
    for value in values:
        number = _coerce_number(value)
        if number is not None:
            numbers.append(number)
    return numbers


def _sum_numbers(numbers: list[int | float]) -> int | float:
    if all(isinstance(number, int) for number in numbers):
        return int(sum(int(number) for number in numbers))
    return float(sum(float(number) for number in numbers))


def _coerce_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return bool(value)
    if isinstance(value, str):
        text = value.strip().lower()
        if text in {"1", "true", "yes", "on"}:
            return True
        if text in {"0", "false", "no", "off"}:
            return False
    return None


def _classify_tool_usage_metrics(payload: Any) -> dict[str, int]:
    usage_blocks = _collect_tool_usage_blocks(payload)
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


def _collect_tool_usage_blocks(payload: Any) -> list[dict[str, Any]]:
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
    return usage_blocks


def _extract_tool_name(payload: dict[str, Any]) -> str | None:
    event_type = str(payload.get("type") or "").strip().lower().replace("-", "_")
    if event_type in {"tool_result", "tool_result_delta"}:
        return None

    direct_name = _pick_tool_string(payload, ("tool_name", "toolName", "name"))
    if event_type in {"tool_use", "tool_use_delta", "server_tool_use", "tool_call", "toolcall"}:
        return _normalize_tool_name(direct_name)

    if _pick_tool_string(payload, ("tool_use_id", "toolUseId")) and direct_name:
        return _normalize_tool_name(direct_name)

    tool_id = _pick_tool_string(payload, ("id",))
    if isinstance(tool_id, str) and tool_id.startswith("toolu_") and direct_name:
        return _normalize_tool_name(direct_name)

    return None


def _normalize_tool_name(value: str | None) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    return text


def _normalize_event_type(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip().lower().replace("-", "_")
    return text or None


def _extract_tool_use_id(payload: dict[str, Any]) -> str | None:
    for key in ("tool_use_id", "toolUseId"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    event_id = payload.get("id")
    if isinstance(event_id, str) and event_id.strip().startswith("toolu_"):
        return event_id.strip()
    return None


def _extract_tool_input_payload(payload: dict[str, Any]) -> Any:
    if "input" in payload:
        return payload.get("input")
    if "arguments" in payload:
        return payload.get("arguments")
    if "params" in payload:
        return payload.get("params")
    return {}


def _collect_tool_use_events(payload: Any) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    seen_ids: set[int] = set()

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            node_id = id(node)
            if node_id in seen_ids:
                return
            seen_ids.add(node_id)
            if _is_tool_use_invocation(node):
                output.append(node)
            for value in node.values():
                walk(value)
            return
        if isinstance(node, list):
            for item in node:
                walk(item)

    walk(payload)
    return output


def _is_tool_use_invocation(payload: dict[str, Any]) -> bool:
    event_type = _normalize_event_type(payload.get("type"))
    if event_type in {"tool_result", "tool_result_delta"}:
        return False

    tool_name = _extract_tool_name(payload)
    if not tool_name:
        return False

    input_payload = _extract_tool_input_payload(payload)
    has_input = input_payload not in (None, "", {})
    if not has_input:
        return False

    if event_type in {"tool_use", "server_tool_use", "tool_call", "toolcall"}:
        return True
    if event_type == "tool_use_delta":
        return True

    if _extract_tool_use_id(payload):
        return True
    return False


def _populate_grep_curated(record: dict[str, Any], input_payload: dict[str, Any]) -> None:
    query = _pick_from_dict(input_payload, ("query", "pattern", "regex", "text"))
    if isinstance(query, str) and query.strip():
        record["query"] = query.strip()

    path = _pick_from_dict(input_payload, ("path", "file", "file_path"))
    if isinstance(path, str) and path.strip():
        record["path"] = path.strip()

    include = _pick_from_dict(input_payload, ("include", "glob", "files"))
    if include not in (None, "", [], {}):
        record["include"] = include

    flags: dict[str, Any] = {}
    for key in (
        "flags",
        "case_sensitive",
        "ignore_case",
        "multiline",
        "line_numbers",
        "word_regexp",
        "fixed_strings",
        "before_context",
        "after_context",
        "context",
        "max_results",
    ):
        value = input_payload.get(key)
        if value in (None, ""):
            continue
        flags[key] = value
    if flags:
        record["flags"] = flags


def _populate_read_curated(record: dict[str, Any], input_payload: dict[str, Any]) -> None:
    path = _pick_from_dict(input_payload, ("file_path", "path", "file"))
    if isinstance(path, str) and path.strip():
        record["path"] = path.strip()
    offset = input_payload.get("offset")
    limit = input_payload.get("limit")
    if _coerce_int(offset) is not None:
        record["offset"] = _coerce_int(offset)
    if _coerce_int(limit) is not None:
        record["limit"] = _coerce_int(limit)


def _populate_bash_curated(record: dict[str, Any], input_payload: dict[str, Any]) -> None:
    command = _pick_from_dict(input_payload, ("command", "cmd", "bash"))
    if isinstance(command, str) and command.strip():
        record["command"] = command.strip()
    cwd = input_payload.get("cwd")
    if isinstance(cwd, str) and cwd.strip():
        record["cwd"] = cwd.strip()
    timeout = _coerce_int(input_payload.get("timeout"))
    if timeout is not None:
        record["timeout"] = timeout


def _populate_edit_curated(record: dict[str, Any], input_payload: dict[str, Any]) -> None:
    path = _pick_from_dict(input_payload, ("file_path", "path", "file"))
    if isinstance(path, str) and path.strip():
        record["path"] = path.strip()

    old_text = _pick_from_dict(input_payload, ("old_string", "oldText", "old"))
    new_text = _pick_from_dict(input_payload, ("new_string", "newText", "new"))
    if isinstance(old_text, str):
        record["old_chars"] = len(old_text)
    if isinstance(new_text, str):
        record["new_chars"] = len(new_text)

    replace_all = input_payload.get("replace_all")
    if isinstance(replace_all, bool):
        record["replace_all"] = replace_all


def _populate_fallback_curated(record: dict[str, Any], input_payload: Any) -> None:
    if input_payload in (None, "", {}, []):
        return
    record["raw_input_json"] = json.dumps(
        input_payload,
        ensure_ascii=False,
        sort_keys=True,
    )


def _pick_from_dict(payload: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        if key in payload:
            return payload.get(key)
    return None


def _coerce_int(value: Any) -> int | None:
    number = _coerce_number(value)
    if number is None:
        return None
    return int(float(number))


def _pick_tool_string(payload: dict[str, Any], keys: tuple[str, ...]) -> str | None:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


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


def _values_for_path(data: Any, path: tuple[str, ...]) -> list[Any]:
    if not path:
        return [data]
    head, *tail = path
    results: list[Any] = []
    if head == "*":
        if isinstance(data, dict):
            for value in data.values():
                results.extend(_values_for_path(value, tuple(tail)))
        elif isinstance(data, list):
            for value in data:
                results.extend(_values_for_path(value, tuple(tail)))
        return results
    if isinstance(data, dict) and head in data:
        return _values_for_path(data[head], tuple(tail))
    return []


def _values_for_path_anywhere(data: Any, path: tuple[str, ...]) -> list[Any]:
    results = _values_for_path(data, path)
    if isinstance(data, dict):
        for value in data.values():
            results.extend(_values_for_path_anywhere(value, path))
    elif isinstance(data, list):
        for value in data:
            results.extend(_values_for_path_anywhere(value, path))
    return results


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


def _summarize_hook_payload(payload: Any) -> dict[str, float | int | str]:
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

        for key in (
            "token_input",
            "token_output",
            "estimated_cost",
            "cache_creation_input_tokens",
            "cache_read_input_tokens",
            "cache_creation_ephemeral_5m_input_tokens",
            "cache_creation_ephemeral_1h_input_tokens",
        ):
            number = _coerce_number(item.get(key))
            if number is None:
                continue
            summary[key] = summary.get(key, 0) + number
    return summary
