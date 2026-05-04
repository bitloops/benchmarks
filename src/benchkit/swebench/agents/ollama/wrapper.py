#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

from ..common import (
    build_bitloops_task_environment,
    capture_workspace_patch,
    emit_success,
    extract_tool_invocation_sequence,
    extract_tool_invocations_curated,
    extract_tool_invocations_raw,
    extract_tool_usage_breakdown,
    extract_usage_metrics,
    extract_git_patch,
    fatal_error,
    merge_metric_metadata,
    prompt_template_metadata,
    read_payload_from_stdin,
    render_task_prompt,
    reset_workspace,
    resolve_bitloops_sandbox,
    resolve_workspace,
    setup_bitloops_for_workspace,
    start_bitloops_task_daemon,
    stop_bitloops_task_daemon,
    summarize_tool_invocation_counts,
)
from .runtime import (
    build_ollama_request_options,
    build_ollama_runtime_config as _build_ollama_runtime_config,
    decode_ollama_json_object as _decode_json_object,
    default_ollama_json_path as _resolve_repo_ollama_config_path,
    load_ollama_config_file as _load_ollama_config_file,
    resolve_ollama_auth_bearer_token as _resolve_auth_bearer_token,
    resolve_ollama_base_url as _resolve_base_url,
    resolve_ollama_max_num_predict as _resolve_max_num_predict,
    resolve_ollama_model_name as _resolve_model_name,
    resolve_ollama_timeout_seconds as _resolve_timeout_seconds,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--bitloops-init",
        action="store_true",
        help="Initialize Bitloops before running the agent call.",
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


def _persist_raw_patch(
    *,
    payload: dict[str, object],
    patch: str,
    label: str,
) -> str | None:
    if not patch.strip():
        return None
    attempt_dir = _resolve_attempt_dir(payload)
    if attempt_dir is None:
        return None
    raw_dir = attempt_dir / "agent_raw"
    instance_stem = _sanitize_instance_id(payload.get("instance_id"))
    safe_label = re.sub(r"[^A-Za-z0-9_.-]+", "-", label.strip()) or "candidate"
    patch_path = raw_dir / f"{instance_stem}.ollama.{safe_label}.patch"
    raw_dir.mkdir(parents=True, exist_ok=True)
    patch_path.write_text(patch, encoding="utf-8")
    return str(patch_path)


def _build_agent_tools() -> list[dict[str, object]]:
    return [
        {
            "type": "function",
            "function": {
                "name": "Read",
                "description": "Read a file from the workspace. Optionally provide line bounds.",
                "parameters": {
                    "type": "object",
                    "required": ["path"],
                    "properties": {
                        "path": {"type": "string", "description": "Workspace-relative file path."},
                        "start_line": {"type": "integer", "description": "1-based start line."},
                        "end_line": {"type": "integer", "description": "1-based end line."},
                    },
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "Grep",
                "description": "Search for text in workspace files.",
                "parameters": {
                    "type": "object",
                    "required": ["pattern"],
                    "properties": {
                        "pattern": {"type": "string", "description": "Text or regex pattern to search for."},
                        "path": {
                            "type": "string",
                            "description": "Optional workspace-relative directory to search within.",
                        },
                    },
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "Glob",
                "description": "List files matching a glob pattern in the workspace.",
                "parameters": {
                    "type": "object",
                    "required": ["pattern"],
                    "properties": {
                        "pattern": {"type": "string", "description": "Glob pattern such as '**/*.rs'."},
                    },
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "Bash",
                "description": "Run a bounded shell command inside the workspace.",
                "parameters": {
                    "type": "object",
                    "required": ["command"],
                    "properties": {
                        "command": {"type": "string", "description": "Shell command to run from the workspace root."},
                    },
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "Edit",
                "description": (
                    "Edit a workspace file either by replacing old_text with new_text or by "
                    "overwriting the file with content."
                ),
                "parameters": {
                    "type": "object",
                    "required": ["path"],
                    "properties": {
                        "path": {"type": "string", "description": "Workspace-relative file path."},
                        "old_text": {"type": "string", "description": "Exact existing text to replace."},
                        "new_text": {"type": "string", "description": "Replacement text for old_text."},
                        "content": {"type": "string", "description": "Full file content to write."},
                    },
                },
            },
        },
    ]


def _build_system_prompt() -> str:
    return (
        "You are a coding agent running in a benchmark workspace. "
        "Use the available tools to inspect files, search the repository, edit files, and run "
        "verification commands. Inspect the workspace before editing, and after changing files run "
        "at least one focused verification command. Prefer making real workspace edits through tools instead of "
        "describing a patch in prose. You may call tools multiple times. "
        'If native tool calling is unavailable, request tools by replying with a single JSON object '
        'with keys "name" and "arguments", or a JSON array of such objects. Do not include prose '
        "around JSON tool requests. When you are done, stop calling tools and respond briefly with "
        "what you changed. Do not use markdown fences."
    )


def _build_agent_prompt(prompt: str) -> str:
    rewritten = prompt
    rewritten = re.sub(
        r"\nHere is an example of a patch file\. It consists of changes to the code base\.\n<patch>\n.*?\n</patch>\n",
        "\n",
        rewritten,
        flags=re.DOTALL,
    )
    rewritten = rewritten.replace(
        "Solve the issue by generating a single patch file applicable with git apply.\n",
        "",
    )
    rewritten = rewritten.replace("Respond with only the patch.", "")
    rewritten = rewritten.strip()
    agent_suffix = (
        "\n\nUse the available tools to inspect the workspace, edit files directly, and run "
        "verification commands when useful.\n"
        "Inspect relevant files before your first edit.\n"
        "After making edits, run at least one focused Bash verification command before finishing.\n"
        "Make the necessary changes in the workspace instead of replying with a textual patch.\n"
        "Do not commit your changes; just leave the edited files in place.\n"
        "When you are done, respond briefly and stop calling tools."
    )
    return f"{rewritten}{agent_suffix}".strip()


def _build_chat_request_body(
    *,
    prompt: str,
    model_name: str,
    payload: dict[str, object],
    runtime_config: dict[str, object],
    messages: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    chat_messages = messages or [
        {"role": "system", "content": _build_system_prompt()},
        {"role": "user", "content": prompt},
    ]
    body: dict[str, object] = {
        "model": model_name,
        "messages": chat_messages,
        "tools": _build_agent_tools(),
        "stream": False,
    }

    options = build_ollama_request_options(
        payload=payload,
        runtime_config=runtime_config,
        model_name=model_name,
    )
    if options:
        body["options"] = options
    return body


def _extract_response_tool_calls(response_payload: dict[str, object]) -> list[dict[str, object]]:
    message = response_payload.get("message")
    if not isinstance(message, dict):
        return []
    tool_calls = message.get("tool_calls")
    if not isinstance(tool_calls, list):
        return []
    return [call for call in tool_calls if isinstance(call, dict)]


def _extract_json_tool_calls_from_text(response_text: str) -> list[tuple[str, dict[str, object]]]:
    text = response_text.strip()
    if not text:
        return []
    candidates: list[str] = [text]
    seen_candidates = {text}

    for match in re.finditer(r"[\{\[]", text):
        candidate = text[match.start() :].strip()
        if not candidate or candidate in seen_candidates:
            continue
        seen_candidates.add(candidate)
        candidates.append(candidate)
        if len(candidates) >= 12:
            break

    for candidate in candidates:
        try:
            decoded, _ = json.JSONDecoder().raw_decode(candidate)
        except json.JSONDecodeError:
            continue

        if isinstance(decoded, dict) and isinstance(decoded.get("tool_calls"), list):
            payloads: list[object] = list(decoded.get("tool_calls") or [])
        elif isinstance(decoded, dict) and isinstance(decoded.get("calls"), list):
            payloads = list(decoded.get("calls") or [])
        elif isinstance(decoded, list):
            payloads = list(decoded)
        else:
            payloads = [decoded]

        parsed_calls: list[tuple[str, dict[str, object]]] = []
        for payload in payloads:
            if not isinstance(payload, dict):
                continue
            function = payload.get("function")
            function_dict = function if isinstance(function, dict) else {}
            raw_name = (
                function_dict.get("name")
                or payload.get("name")
                or payload.get("tool_name")
                or payload.get("tool")
            )
            tool_name = _normalize_tool_name_for_event(str(raw_name or "").strip())
            if not tool_name:
                continue
            raw_arguments = (
                function_dict.get("arguments")
                if function_dict
                else payload.get("arguments", payload.get("args", payload.get("input")))
            )
            parsed_calls.append((tool_name, _parse_tool_arguments(raw_arguments)))
        if parsed_calls:
            return parsed_calls

    return []


def _extract_structured_tool_calls(
    *,
    response_payload: dict[str, object],
    response_text: str,
    call_index_start: int,
) -> list[dict[str, object]]:
    native_tool_calls = _extract_response_tool_calls(response_payload)
    if native_tool_calls:
        return native_tool_calls

    structured_calls: list[dict[str, object]] = []
    text_calls = _extract_json_tool_calls_from_text(response_text)
    if not text_calls:
        text_calls = [
            (_normalize_tool_name_for_event(tool_name), args)
            for tool_name, args in _extract_tagged_tool_calls_from_text(response_text)
        ]

    for offset, (tool_name, args) in enumerate(text_calls, start=0):
        if not tool_name:
            continue
        structured_calls.append(
            {
                "id": f"ollama_text_tool_call_{call_index_start + offset}",
                "function": {
                    "name": tool_name,
                    "arguments": args,
                },
            }
        )
    return structured_calls


def _extract_response_text(response_payload: dict[str, object]) -> str:
    message = response_payload.get("message")
    if isinstance(message, dict):
        content = message.get("content")
        if isinstance(content, str):
            return content.strip()

    response = response_payload.get("response")
    if isinstance(response, str):
        return response.strip()
    return ""


def _truncate_tool_output(text: str, *, limit: int = 12000) -> str:
    if len(text) <= limit:
        return text
    return f"{text[:limit]}\n...[truncated]..."


def _resolve_workspace_path(
    *,
    workspace: Path,
    raw_path: object,
    allow_missing: bool = False,
) -> Path:
    text = str(raw_path or "").strip()
    if not text:
        raise ValueError("path is required")
    candidate = (workspace / text).resolve()
    workspace_root = workspace.resolve()
    try:
        candidate.relative_to(workspace_root)
    except ValueError as exc:
        raise ValueError(f"path escapes workspace: {text}") from exc
    if not allow_missing and not candidate.exists():
        raise ValueError(f"path not found: {text}")
    return candidate


def _read_tool(
    *,
    workspace: Path,
    args: dict[str, object],
) -> str:
    workspace_root = workspace.resolve()
    file_path = _resolve_workspace_path(workspace=workspace, raw_path=args.get("path"))
    content = file_path.read_text(encoding="utf-8", errors="replace")
    lines = content.splitlines()
    if not lines:
        rel_path = file_path.relative_to(workspace_root).as_posix()
        return f"[start of {rel_path}]\n[end of {rel_path}]"

    start_raw = args.get("start_line")
    end_raw = args.get("end_line")
    try:
        start_line = max(int(start_raw), 1) if start_raw is not None else 1
    except (TypeError, ValueError):
        start_line = 1
    try:
        end_line = min(int(end_raw), len(lines)) if end_raw is not None else len(lines)
    except (TypeError, ValueError):
        end_line = len(lines)
    if end_line < start_line:
        end_line = start_line

    rendered = "\n".join(
        f"{line_number} {lines[line_number - 1]}"
        for line_number in range(start_line, end_line + 1)
    )
    rel_path = file_path.relative_to(workspace_root).as_posix()
    return _truncate_tool_output(
        f"[start of {rel_path} lines {start_line}-{end_line}]\n{rendered}\n[end of {rel_path} lines {start_line}-{end_line}]"
    )


def _glob_tool(
    *,
    workspace: Path,
    args: dict[str, object],
) -> str:
    workspace_root = workspace.resolve()
    pattern = str(args.get("pattern") or "").strip()
    if not pattern:
        raise ValueError("pattern is required")
    matches = sorted(
        path.resolve().relative_to(workspace_root).as_posix()
        for path in workspace.glob(pattern)
    )
    if not matches:
        return "No matches"
    return _truncate_tool_output("\n".join(matches[:200]))


def _grep_tool(
    *,
    workspace: Path,
    args: dict[str, object],
) -> tuple[str, list[dict[str, object]]]:
    pattern = str(args.get("pattern") or "").strip()
    if not pattern:
        raise ValueError("pattern is required")
    search_root = workspace
    if args.get("path") is not None:
        search_root = _resolve_workspace_path(
            workspace=workspace,
            raw_path=args.get("path"),
        )
    rg_bin = shutil.which("rg")
    command: list[str]
    if rg_bin:
        command = [rg_bin, "-n", "--no-heading", pattern, str(search_root)]
    else:
        command = ["grep", "-RIn", pattern, str(search_root)]
    completed = subprocess.run(
        command,
        capture_output=True,
        text=True,
        cwd=str(workspace),
        timeout=30,
        check=False,
    )
    tool_events = [_tool_event_for_command(command=" ".join(command), exit_code=int(completed.returncode))]
    output = completed.stdout.strip() or completed.stderr.strip() or "No matches"
    return _truncate_tool_output(output), tool_events


def _bash_tool(
    *,
    workspace: Path,
    args: dict[str, object],
) -> tuple[str, list[dict[str, object]]]:
    command = str(args.get("command") or "").strip()
    if not command:
        raise ValueError("command is required")
    completed = subprocess.run(
        ["/bin/zsh", "-lc", command],
        capture_output=True,
        text=True,
        cwd=str(workspace),
        timeout=60,
        check=False,
    )
    tool_events = [_tool_event_for_command(command=command, exit_code=int(completed.returncode))]
    sections = [f"exit_code={completed.returncode}"]
    if completed.stdout.strip():
        sections.append(f"stdout:\n{completed.stdout.strip()}")
    if completed.stderr.strip():
        sections.append(f"stderr:\n{completed.stderr.strip()}")
    return _truncate_tool_output("\n\n".join(sections)), tool_events


def _edit_tool(
    *,
    workspace: Path,
    args: dict[str, object],
) -> str:
    workspace_root = workspace.resolve()
    file_path = _resolve_workspace_path(
        workspace=workspace,
        raw_path=args.get("path"),
        allow_missing=True,
    )
    rel_path = file_path.relative_to(workspace_root).as_posix()
    content = args.get("content")
    old_text = args.get("old_text")
    new_text = args.get("new_text")

    if isinstance(content, str):
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(content, encoding="utf-8")
        return f"Updated {rel_path} with {len(content)} bytes of content."

    if not isinstance(old_text, str) or not isinstance(new_text, str):
        raise ValueError("Edit requires either content or both old_text and new_text")

    existing = file_path.read_text(encoding="utf-8", errors="replace") if file_path.exists() else ""
    if old_text not in existing:
        raise ValueError(f"old_text not found in {rel_path}")
    updated = existing.replace(old_text, new_text, 1)
    file_path.write_text(updated, encoding="utf-8")
    return f"Replaced text in {rel_path}."


def _parse_tool_arguments(raw_args: object) -> dict[str, object]:
    if isinstance(raw_args, dict):
        return dict(raw_args)
    if isinstance(raw_args, str):
        text = raw_args.strip()
        if not text:
            return {}
        try:
            decoded = json.loads(text)
        except json.JSONDecodeError:
            return {}
        if isinstance(decoded, dict):
            return decoded
    return {}


def _execute_tool_call(
    *,
    workspace: Path,
    tool_name: str,
    args: dict[str, object],
) -> tuple[str, list[dict[str, object]]]:
    if tool_name == "Read":
        return _read_tool(workspace=workspace, args=args), []
    if tool_name == "Glob":
        return _glob_tool(workspace=workspace, args=args), []
    if tool_name == "Grep":
        return _grep_tool(workspace=workspace, args=args)
    if tool_name == "Bash":
        return _bash_tool(workspace=workspace, args=args)
    if tool_name == "Edit":
        return _edit_tool(workspace=workspace, args=args), []
    raise ValueError(f"unsupported tool: {tool_name}")


def _resolve_max_turns() -> int:
    raw_value = os.environ.get("BENCHKIT_OLLAMA_MAX_TURNS", "").strip()
    if raw_value:
        try:
            return max(int(raw_value), 1)
        except ValueError:
            return 12
    return 12


def _run_agent_loop(
    *,
    initial_prompt: str,
    model_name: str,
    payload: dict[str, object],
    runtime_config: dict[str, object],
    base_url: str,
    timeout_seconds: int,
    auth_bearer_token: str | None,
    workspace: Path,
) -> dict[str, object]:
    initial_prompt = _build_agent_prompt(initial_prompt)
    messages: list[dict[str, object]] = [
        {"role": "system", "content": _build_system_prompt()},
        {"role": "user", "content": initial_prompt},
    ]
    tool_events: list[dict[str, object]] = []
    usage_metrics: dict[str, float | int | str] = {}
    response_payload: dict[str, object] = {}
    response_text = ""
    turn_count = 0
    max_turns = _resolve_max_turns()
    tool_calls_seen = False
    inspected_workspace = False
    changed_workspace = False
    verified_after_edit = False

    for turn_index in range(max_turns):
        turn_count = turn_index + 1
        request_body = _build_chat_request_body(
            prompt=initial_prompt,
            model_name=model_name,
            payload=payload,
            runtime_config=runtime_config,
            messages=messages,
        )
        response_payload = _call_ollama_chat(
            base_url=base_url,
            body=request_body,
            timeout_seconds=timeout_seconds,
            auth_bearer_token=auth_bearer_token,
        )
        usage_metrics = _sum_usage_metrics(
            usage_metrics,
            merge_metric_metadata(extract_usage_metrics(response_payload)),
        )
        response_text = _extract_response_text(response_payload)
        tool_calls = _extract_structured_tool_calls(
            response_payload=response_payload,
            response_text=response_text,
            call_index_start=len(tool_events),
        )
        assistant_message: dict[str, object] = {"role": "assistant", "content": response_text}
        if tool_calls:
            tool_calls_seen = True
            assistant_message["tool_calls"] = tool_calls
        messages.append(assistant_message)
        if not tool_calls:
            if not tool_calls_seen and turn_index + 1 < max_turns:
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            "You have not used any tools yet. Do not reply with a textual patch. "
                            "Use the available tools to inspect the repository, edit the workspace, "
                            "and verify your changes before finishing. If native tool calling is "
                            'unavailable, request a tool by replying with a JSON object with keys '
                            '"name" and "arguments".'
                        ),
                    }
                )
                continue
            if changed_workspace and not verified_after_edit and turn_index + 1 < max_turns:
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            "You changed the workspace but have not verified the result yet. "
                            "Run at least one focused Bash verification command before finishing."
                        ),
                    }
                )
                continue
            break

        for offset, call in enumerate(tool_calls, start=1):
            function = call.get("function")
            function_dict = function if isinstance(function, dict) else {}
            tool_name = _normalize_tool_name_for_event(str(function_dict.get("name") or "").strip())
            if not tool_name:
                continue
            args = _parse_tool_arguments(function_dict.get("arguments"))
            tool_events.append(
                {
                    "type": "tool_call",
                    "name": tool_name,
                    "tool_use_id": str(call.get("id") or f"ollama_tool_call_{turn_count}_{offset}"),
                    "input": args,
                }
            )
            try:
                if tool_name == "Edit" and not inspected_workspace:
                    tool_output = (
                        "tool_error: Before editing, inspect the workspace with Read, Grep, "
                        "Glob, or Bash."
                    )
                    nested_events = []
                else:
                    tool_output, nested_events = _execute_tool_call(
                        workspace=workspace,
                        tool_name=tool_name,
                        args=args,
                    )
                    if tool_name in {"Read", "Grep", "Glob", "Bash"}:
                        inspected_workspace = True
                    if tool_name == "Edit":
                        changed_workspace = True
                        verified_after_edit = False
                    elif tool_name == "Bash" and changed_workspace:
                        verified_after_edit = True
            except Exception as exc:  # noqa: BLE001
                tool_output = f"tool_error: {exc}"
                nested_events = []
            tool_events.extend(nested_events)
            messages.append(
                {
                    "role": "tool",
                    "tool_name": tool_name,
                    "content": tool_output,
                }
            )

    return {
        "messages": messages,
        "response_payload": response_payload,
        "response_text": response_text,
        "tool_events": tool_events,
        "tool_invocation_sequence": extract_tool_invocation_sequence(tool_events),
        "usage_metrics": usage_metrics,
        "turn_count": turn_count,
    }


def _resolve_final_patch(
    *,
    workspace: Path,
    response_text: str,
) -> tuple[str, str]:
    workspace_patch = capture_workspace_patch(workspace)
    if workspace_patch:
        patch = workspace_patch if workspace_patch.endswith("\n") else f"{workspace_patch}\n"
        return patch, "workspace_git_diff"
    return extract_git_patch(response_text)


def _coerce_float(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            return float(text)
        except ValueError:
            return None
    return None


def _sum_usage_metrics(
    current: dict[str, float | int | str],
    incoming: dict[str, float | int | str],
) -> dict[str, float | int | str]:
    # Keep summation constrained to token/cost fields so this mirrors shared usage extraction.
    summable_keys = (
        "token_input",
        "token_output",
        "reasoning_output_tokens",
        "total_tokens",
        "estimated_cost",
        "cached_input_tokens",
        "cached_output_tokens",
        "cache_creation_input_tokens",
        "cache_read_input_tokens",
        "cache_creation_ephemeral_5m_input_tokens",
        "cache_creation_ephemeral_1h_input_tokens",
        "token_input_uncached",
        "token_output_uncached",
        "input_tokens",
        "output_tokens",
        "total_input_processed_tokens",
        "total_processed_tokens",
    )
    merged: dict[str, float | int | str] = dict(current)
    summed_any = False
    for key in summable_keys:
        incoming_number = _coerce_float(incoming.get(key))
        if incoming_number is None:
            continue
        existing_number = _coerce_float(merged.get(key))
        summed = incoming_number if existing_number is None else existing_number + incoming_number
        if summed.is_integer():
            merged[key] = int(summed)
        else:
            merged[key] = summed
        summed_any = True

    if summed_any:
        merged["token_metrics_source"] = "ollama_response_sum"
    if "token_usage_semantics" not in merged and isinstance(incoming.get("token_usage_semantics"), str):
        merged["token_usage_semantics"] = str(incoming.get("token_usage_semantics"))
    return merge_metric_metadata(merged)


def _normalize_tool_name_for_event(raw_name: str) -> str:
    text = raw_name.strip()
    if not text:
        return text
    mapped = {
        "file_search": "Grep",
        "search_files": "Grep",
        "list_directory": "Glob",
        "glob": "Glob",
        "read_file": "Read",
        "open_file": "Read",
        "run_command": "Bash",
        "shell": "Bash",
        "bash": "Bash",
        "edit_file": "Edit",
        "apply_patch": "Edit",
        "web_search": "WebSearch",
        "web_fetch": "WebFetch",
    }.get(text.lower())
    return mapped or text


def _parse_tool_arguments(raw_args: object) -> dict[str, object]:
    if isinstance(raw_args, dict):
        return dict(raw_args)
    if isinstance(raw_args, str):
        text = raw_args.strip()
        if not text:
            return {}
        try:
            decoded = json.loads(text)
        except json.JSONDecodeError:
            return {"raw": text}
        return decoded if isinstance(decoded, dict) else {"raw": text}
    return {}


def _extract_tagged_tool_calls_from_text(response_text: str) -> list[tuple[str, dict[str, object]]]:
    tagged_calls: list[tuple[str, dict[str, object]]] = []
    if not response_text.strip():
        return tagged_calls

    for block in re.findall(r"<tool_call>(.*?)</tool_call>", response_text, flags=re.DOTALL | re.IGNORECASE):
        match = re.search(r"<([a-zA-Z_][a-zA-Z0-9_]*)>(.*?)</\1>", block, flags=re.DOTALL)
        if not match:
            continue
        tool_name = match.group(1).strip()
        body = match.group(2)
        args: dict[str, object] = {}
        for key, value in re.findall(r"<([a-zA-Z_][a-zA-Z0-9_]*)>(.*?)</\1>", body, flags=re.DOTALL):
            cleaned = value.strip()
            if cleaned:
                args[key.strip()] = cleaned
        tagged_calls.append((tool_name, args))
    return tagged_calls


def _build_tool_events(
    *,
    response_payload: dict[str, object],
    response_text: str,
    call_index_start: int,
) -> list[dict[str, object]]:
    events: list[dict[str, object]] = []
    message = response_payload.get("message")
    if isinstance(message, dict):
        tool_calls = message.get("tool_calls")
        if isinstance(tool_calls, list):
            for offset, call in enumerate(tool_calls, start=0):
                if not isinstance(call, dict):
                    continue
                function = call.get("function")
                function_dict = function if isinstance(function, dict) else {}
                raw_name = str(function_dict.get("name") or call.get("name") or "").strip()
                if not raw_name:
                    continue
                input_payload = _parse_tool_arguments(function_dict.get("arguments"))
                tool_use_id = str(call.get("id") or f"ollama_tool_call_{call_index_start + offset}").strip()
                events.append(
                    {
                        "type": "tool_call",
                        "name": _normalize_tool_name_for_event(raw_name),
                        "tool_use_id": tool_use_id,
                        "input": input_payload,
                    }
                )

    if not events:
        tagged_calls = _extract_tagged_tool_calls_from_text(response_text)
        for offset, (raw_name, args) in enumerate(tagged_calls, start=0):
            events.append(
                {
                    "type": "tool_call",
                    "name": _normalize_tool_name_for_event(raw_name),
                    "tool_use_id": f"ollama_tagged_tool_call_{call_index_start + offset}",
                    "input": args,
                }
            )
    return events


def _build_tool_breakdown_from_sequence(sequence: list[str]) -> dict[str, int]:
    breakdown: dict[str, int] = {}
    mapping = {
        "Bash": "shell_commands",
        "Read": "file_reads",
        "Grep": "search_actions",
        "WebSearch": "search_actions",
    }
    for name in sequence:
        key = mapping.get(str(name).strip())
        if not key:
            continue
        breakdown[key] = int(breakdown.get(key, 0)) + 1
    return breakdown


def _apply_tool_metrics(
    usage_metrics: dict[str, float | int | str],
    tool_invocations_raw: list[dict[str, object]],
    tool_invocation_sequence: list[str],
) -> dict[str, float | int | str]:
    merged = dict(usage_metrics)
    if "tool_calls" not in merged:
        merged["tool_calls"] = len(tool_invocations_raw)
    derived_breakdown = _build_tool_breakdown_from_sequence(tool_invocation_sequence)
    for key, value in derived_breakdown.items():
        if key not in merged:
            merged[key] = value
    # Keep appendix rows numeric and explicit even when no tool events were captured.
    for key in ("shell_commands", "file_reads", "search_actions"):
        if key not in merged:
            merged[key] = 0
    return merged


def _as_bool(value: str | None, *, default: bool = False) -> bool:
    if value is None:
        return default
    text = value.strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    return default


def _build_diff_repair_prompt(*, original_prompt: str, prior_response: str) -> str:
    return (
        f"{original_prompt}\n\n"
        "Your previous response did not include a valid unified git diff.\n"
        "Reply again with ONLY a valid unified diff patch that can be applied with `git apply`.\n"
        "Do not include prose, analysis, markdown fences, XML tags, or explanations.\n"
        "Do not emit any tool call syntax (for example <tool_call>, JSON tool messages, or function calls).\n"
        "If multiple files are needed, include all of them in one diff output.\n\n"
        "Previous response:\n"
        f"{prior_response}"
    )


def _extract_apply_repair_targets(patch: str) -> list[tuple[str, list[int]]]:
    targets: list[tuple[str, list[int]]] = []
    index_by_path: dict[str, int] = {}
    current_path: str | None = None

    for raw_line in patch.splitlines():
        if raw_line.startswith("+++ "):
            path_text = raw_line[4:].strip()
            if path_text == "/dev/null":
                current_path = None
                continue
            if path_text.startswith("b/"):
                path_text = path_text[2:]
            current_path = path_text
            existing_index = index_by_path.get(path_text)
            if existing_index is None:
                index_by_path[path_text] = len(targets)
                targets.append((path_text, []))
            continue

        if current_path is None or not raw_line.startswith("@@"):
            continue

        match = re.match(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@", raw_line)
        if not match:
            continue
        target_index = index_by_path.get(current_path)
        if target_index is None:
            continue
        start_line = max(int(match.group(1)), 1)
        targets[target_index][1].append(start_line)

    return targets


def _render_apply_repair_context(*, workspace: Path, patch: str) -> str:
    workspace_root = workspace.resolve()
    sections: list[str] = []
    max_files = 3
    max_lines_per_file = 220
    window_radius = 45

    for relative_path, start_lines in _extract_apply_repair_targets(patch)[:max_files]:
        candidate = (workspace_root / relative_path).resolve()
        try:
            candidate.relative_to(workspace_root)
        except ValueError:
            continue
        if not candidate.is_file():
            continue

        content = candidate.read_text(encoding="utf-8", errors="replace")
        lines = content.splitlines()
        if not lines:
            continue

        windows: list[tuple[int, int]] = []
        if len(lines) <= max_lines_per_file:
            windows = [(1, len(lines))]
        else:
            unique_starts = sorted({line for line in start_lines if line > 0})[:3]
            if not unique_starts:
                unique_starts = [1]

            for start_line in unique_starts:
                start = max(1, start_line - window_radius)
                end = min(len(lines), start_line + window_radius)
                if windows and start <= windows[-1][1] + 5:
                    prev_start, prev_end = windows[-1]
                    windows[-1] = (prev_start, max(prev_end, end))
                else:
                    windows.append((start, end))

            total_lines = sum(end - start + 1 for start, end in windows)
            if total_lines > max_lines_per_file:
                trimmed: list[tuple[int, int]] = []
                remaining = max_lines_per_file
                for start, end in windows:
                    if remaining <= 0:
                        break
                    window_size = end - start + 1
                    if window_size <= remaining:
                        trimmed.append((start, end))
                        remaining -= window_size
                        continue
                    trimmed.append((start, start + remaining - 1))
                    remaining = 0
                windows = trimmed

        rendered_chunks: list[str] = []
        for start, end in windows:
            rendered_chunks.append(f"[start of {relative_path} lines {start}-{end}]")
            rendered_chunks.extend(
                f"{line_number} {lines[line_number - 1]}"
                for line_number in range(start, end + 1)
            )
            rendered_chunks.append(f"[end of {relative_path} lines {start}-{end}]")

        if rendered_chunks:
            sections.append("\n".join(rendered_chunks))

    return "\n\n".join(sections)


def _build_apply_repair_prompt(
    *,
    original_prompt: str,
    prior_response: str,
    patch: str,
    apply_error: str,
    workspace: Path,
) -> str:
    workspace_context = _render_apply_repair_context(workspace=workspace, patch=patch)
    context_block = ""
    if workspace_context:
        context_block = (
            "\n\nCurrent workspace file content for files referenced by your patch:\n"
            f"{workspace_context[:16000]}"
        )
    return (
        f"{original_prompt}\n\n"
        "Your previous patch did not apply to the current workspace.\n"
        "Reply again with ONLY a corrected unified git diff that applies cleanly.\n"
        "Do not include prose, analysis, markdown fences, XML tags, or explanations.\n"
        "Do not emit any tool call syntax.\n\n"
        "Base your corrected hunks on the exact current workspace file content below.\n"
        "Do not invent placeholder code, omit context with comments, or remove unrelated lines.\n\n"
        "Patch apply error:\n"
        f"{apply_error}\n\n"
        "Previous assistant response:\n"
        f"{prior_response[:12000]}\n\n"
        "Previous extracted patch:\n"
        f"{patch[:12000]}"
        f"{context_block}"
    )


def _tool_event_for_command(
    *,
    command: str,
    exit_code: int,
    status: str = "completed",
) -> dict[str, object]:
    return {
        "type": "command_execution",
        "command": command,
        "status": status,
        "exit_code": exit_code,
    }


def _check_patch_applies(*, workspace: Path, patch: str) -> tuple[bool, str, list[dict[str, object]]]:
    tool_events: list[dict[str, object]] = []
    if not patch.strip():
        return False, "empty patch", tool_events
    try:
        git_check_command = ["git", "apply", "--check", "--recount", "--whitespace=nowarn", "-"]
        completed = subprocess.run(
            git_check_command,
            input=patch,
            text=True,
            capture_output=True,
            cwd=str(workspace),
            timeout=60,
            check=False,
        )
        tool_events.append(
            _tool_event_for_command(
                command=" ".join(git_check_command[:-1]),
                exit_code=int(completed.returncode),
            )
        )
    except Exception as exc:  # noqa: BLE001
        tool_events.append(
            _tool_event_for_command(
                command="git apply --check --recount --whitespace=nowarn",
                exit_code=1,
                status="failed",
            )
        )
        return False, str(exc), tool_events

    if completed.returncode == 0:
        patch_bin = shutil.which("patch")
        if patch_bin:
            patch_check_command = [patch_bin, "--dry-run", "-p1", "-f"]
            patch_check = subprocess.run(
                patch_check_command,
                input=patch,
                text=True,
                capture_output=True,
                cwd=str(workspace),
                timeout=60,
                check=False,
            )
            tool_events.append(
                _tool_event_for_command(
                    command=" ".join(patch_check_command),
                    exit_code=int(patch_check.returncode),
                )
            )
            # `git apply --check --recount` is the authoritative compatibility check for the
            # benchmark patches we emit. GNU `patch` can reject otherwise valid unified diffs
            # when hunk counts need recounting, so keep this as diagnostic-only telemetry.
        return True, "", tool_events
    message = completed.stderr.strip() or completed.stdout.strip() or "git apply --check failed"
    return False, message, tool_events


def _materialize_patch_from_workspace(
    *,
    workspace: Path,
    patch: str,
) -> tuple[bool, str, str, list[dict[str, object]]]:
    tool_events: list[dict[str, object]] = []
    apply_command = ["git", "apply", "--recount", "--whitespace=nowarn", "-"]
    completed = subprocess.run(
        apply_command,
        input=patch,
        text=True,
        capture_output=True,
        cwd=str(workspace),
        timeout=60,
        check=False,
    )
    tool_events.append(
        _tool_event_for_command(
            command=" ".join(apply_command[:-1]),
            exit_code=int(completed.returncode),
        )
    )
    if completed.returncode != 0:
        message = completed.stderr.strip() or completed.stdout.strip() or "git apply failed"
        return False, message, "", tool_events

    workspace_patch = capture_workspace_patch(workspace)
    if workspace_patch and not workspace_patch.endswith("\n"):
        workspace_patch = f"{workspace_patch}\n"
    return True, "", workspace_patch, tool_events


def _call_ollama_chat(
    *,
    base_url: str,
    body: dict[str, object],
    timeout_seconds: int,
    auth_bearer_token: str | None,
) -> dict[str, object]:
    endpoint = f"{base_url}/api/chat"
    data = json.dumps(body).encode("utf-8")
    raw_retry_count = os.environ.get("OLLAMA_RETRY_5XX", "").strip()
    retry_count = 2
    if raw_retry_count:
        try:
            retry_count = max(0, int(raw_retry_count))
        except ValueError:
            retry_count = 2
    raw_retry_backoff = os.environ.get("OLLAMA_RETRY_BACKOFF_SECONDS", "").strip()
    retry_backoff_seconds = 1.0
    if raw_retry_backoff:
        try:
            retry_backoff_seconds = max(0.0, float(raw_retry_backoff))
        except ValueError:
            retry_backoff_seconds = 1.0

    raw = ""
    last_http_error: dict[str, object] | None = None
    for attempt in range(retry_count + 1):
        request = urllib.request.Request(
            endpoint,
            data=data,
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        if auth_bearer_token:
            request.add_header("Authorization", f"Bearer {auth_bearer_token}")
        try:
            with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
                raw = response.read().decode("utf-8", errors="replace")
            break
        except urllib.error.HTTPError as exc:
            raw_error = exc.read().decode("utf-8", errors="replace")
            last_http_error = {
                "status_code": exc.code,
                "reason": str(exc.reason),
                "response_body": raw_error[:8000],
                "endpoint": endpoint,
                "attempt": attempt + 1,
                "max_attempts": retry_count + 1,
            }
            if exc.code >= 500 and attempt < retry_count:
                sleep_seconds = retry_backoff_seconds * (2 ** attempt)
                if sleep_seconds > 0:
                    time.sleep(sleep_seconds)
                continue
            fatal_error("ollama chat request failed", details=last_http_error)
            return {}
        except urllib.error.URLError as exc:
            fatal_error(
                "ollama daemon unreachable",
                details={
                    "error": str(exc.reason),
                    "endpoint": endpoint,
                    "hint": "Ensure Ollama daemon is running and cloud access is configured.",
                },
            )
            return {}
    else:
        fatal_error(
            "ollama chat request failed",
            details=last_http_error
            or {"endpoint": endpoint, "reason": "request retries exhausted"},
        )
        return {}

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        fatal_error(
            "invalid ollama response",
            details={"error": str(exc), "response_preview": raw[:2000]},
        )
        return {}

    if not isinstance(parsed, dict):
        fatal_error(
            "invalid ollama response",
            details={"response_type": type(parsed).__name__},
        )
        return {}
    return parsed


def main() -> None:
    args = parse_args()
    payload = read_payload_from_stdin()
    repo_config_path = _resolve_repo_ollama_config_path()
    existing_config_content = os.environ.get("OLLAMA_CONFIG_CONTENT", "")

    try:
        runtime_config = _build_ollama_runtime_config(
            existing_content=existing_config_content,
            repo_config_path=repo_config_path,
        )
    except (ValueError, json.JSONDecodeError, OSError) as exc:
        fatal_error(
            "invalid Ollama config",
            details={"error": str(exc)},
        )
        return

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
        return

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
                agent_name="ollama",
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
            return

    prompt = render_task_prompt(payload, wrapper_name="ollama")
    prompt_meta = prompt_template_metadata(payload)
    model_name = _resolve_model_name(payload, runtime_config)
    base_url = _resolve_base_url(runtime_config)
    timeout_seconds = _resolve_timeout_seconds(payload, runtime_config)
    auth_bearer_token = _resolve_auth_bearer_token()
    try:
        loop_result = _run_agent_loop(
            initial_prompt=prompt,
            model_name=model_name,
            payload=payload,
            runtime_config=runtime_config,
            base_url=base_url,
            timeout_seconds=timeout_seconds,
            auth_bearer_token=auth_bearer_token,
            workspace=workspace,
        )
    finally:
        stop_bitloops_task_daemon(task_daemon_handle)
    response_payload = loop_result["response_payload"]
    response_text = str(loop_result["response_text"])
    tool_events = list(loop_result["tool_events"])
    usage_metrics = merge_metric_metadata(loop_result["usage_metrics"])
    patch, patch_source = _resolve_final_patch(
        workspace=workspace,
        response_text=response_text,
    )
    raw_patch_paths: dict[str, str] = {}
    if patch.strip() and patch_source != "workspace_git_diff":
        patch_path = _persist_raw_patch(payload=payload, patch=patch, label="final")
        if patch_path:
            raw_patch_paths["final"] = patch_path

    tool_invocations_raw = extract_tool_invocations_raw(tool_events)
    tool_invocations_curated = extract_tool_invocations_curated(tool_invocations_raw)
    tool_invocation_sequence = extract_tool_invocation_sequence(tool_events)
    tool_invocation_counts = summarize_tool_invocation_counts(tool_invocation_sequence)
    tool_usage_breakdown = extract_tool_usage_breakdown(tool_events)
    usage_metrics = _apply_tool_metrics(
        usage_metrics,
        tool_invocations_raw=tool_invocations_raw,
        tool_invocation_sequence=tool_invocation_sequence,
    )

    if not patch.strip():
        allow_empty_patch = _as_bool(
            os.environ.get("BENCHKIT_ALLOW_EMPTY_OLLAMA_PATCH"),
            default=False,
        )
        if allow_empty_patch:
            emit_success(
                patch="",
                metadata={
                    "wrapper": "ollama",
                    "canonical_model_name": model_name,
                    "resolved_model_name": model_name,
                    "ollama_runtime_config": runtime_config,
                    "ollama_request": {
                        "base_url": base_url,
                        "endpoint": "/api/chat",
                        "stream": False,
                        "model": model_name,
                    },
                    "ollama_response_done": response_payload.get("done"),
                    "patch_source": patch_source,
                    "prompt_text": prompt,
                    "allow_empty_patch": True,
                    "agent_loop_turns": loop_result["turn_count"],
                    "raw_patch_paths": raw_patch_paths,
                    "empty_patch_reason": "no_patch_or_workspace_diff",
                    "tool_usage_breakdown": tool_usage_breakdown,
                    "tool_invocations_raw": tool_invocations_raw,
                    "tool_invocations_curated": tool_invocations_curated,
                    "tool_invocation_sequence": tool_invocation_sequence,
                    "tool_invocation_counts": tool_invocation_counts,
                    **prompt_meta,
                    **usage_metrics,
                    **bitloops_metadata,
                },
            )
            return
        fatal_error(
            "ollama produced no patch",
            details={
                "model_name": model_name,
                "patch_source": patch_source,
                "response_preview": response_text[:2000],
                "agent_loop_turns": loop_result["turn_count"],
                "raw_patch_paths": raw_patch_paths,
                "hint": (
                    "Ollama finished without producing workspace edits or a textual patch. "
                    "Use BENCHKIT_ALLOW_EMPTY_OLLAMA_PATCH=1 only for targeted smoke runs."
                ),
            },
        )
        return

    canonical_model_name = ""
    model = payload.get("model", {})
    if isinstance(model, dict):
        canonical_model_name = str(model.get("canonical_name", "")).strip()

    emit_success(
        patch=patch,
        metadata={
            "wrapper": "ollama",
            "canonical_model_name": canonical_model_name or model_name,
            "resolved_model_name": model_name,
            "ollama_runtime_config": runtime_config,
            "ollama_request": {
                "base_url": base_url,
                "endpoint": "/api/chat",
                "stream": False,
                "model": model_name,
            },
            "ollama_response_done": response_payload.get("done"),
            "patch_source": patch_source,
            "prompt_text": prompt,
            "agent_loop_turns": loop_result["turn_count"],
            "raw_patch_paths": raw_patch_paths,
            "tool_usage_breakdown": tool_usage_breakdown,
            "tool_invocations_raw": tool_invocations_raw,
            "tool_invocations_curated": tool_invocations_curated,
            "tool_invocation_sequence": tool_invocation_sequence,
            "tool_invocation_counts": tool_invocation_counts,
            **prompt_meta,
            **usage_metrics,
            **bitloops_metadata,
        },
    )


if __name__ == "__main__":
    main()
