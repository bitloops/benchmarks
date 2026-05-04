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


def _build_chat_request_body(
    *,
    prompt: str,
    model_name: str,
    payload: dict[str, object],
    runtime_config: dict[str, object],
) -> dict[str, object]:
    system_prompt = (
        "You are a code-fix engine running in benchmark mode. "
        "You must output ONLY a unified git diff patch. "
        "Do not output analysis, markdown, tool calls, XML tags, or explanations. "
        "No surrounding code fences. If you cannot produce a patch, output an empty string."
    )
    body: dict[str, object] = {
        "model": model_name,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt},
        ],
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
    request_body = _build_chat_request_body(
        prompt=prompt,
        model_name=model_name,
        payload=payload,
        runtime_config=runtime_config,
    )
    try:
        response_payload = _call_ollama_chat(
            base_url=base_url,
            body=request_body,
            timeout_seconds=timeout_seconds,
            auth_bearer_token=auth_bearer_token,
        )
    finally:
        stop_bitloops_task_daemon(task_daemon_handle)
    response_text = _extract_response_text(response_payload)
    patch, patch_source = extract_git_patch(response_text)
    repair_attempted = False
    apply_repair_attempted = False
    patch_apply_check_passed = False
    patch_apply_error: str | None = None
    patch_materialized = False
    raw_patch_paths: dict[str, str] = {}
    failed_patch_path: str | None = None
    failed_patch_stage: str | None = None
    usage_metrics = merge_metric_metadata(extract_usage_metrics(response_payload))
    tool_events: list[dict[str, object]] = _build_tool_events(
        response_payload=response_payload,
        response_text=response_text,
        call_index_start=1,
    )
    if patch.strip():
        patch_path = _persist_raw_patch(payload=payload, patch=patch, label="initial")
        if patch_path:
            raw_patch_paths["initial"] = patch_path

    if not patch.strip():
        repair_attempted = True
        repair_prompt = _build_diff_repair_prompt(
            original_prompt=prompt,
            prior_response=response_text[:12000],
        )
        repair_request_body = _build_chat_request_body(
            prompt=repair_prompt,
            model_name=model_name,
            payload=payload,
            runtime_config=runtime_config,
        )
        repair_response_payload = _call_ollama_chat(
            base_url=base_url,
            body=repair_request_body,
            timeout_seconds=timeout_seconds,
            auth_bearer_token=auth_bearer_token,
        )
        usage_metrics = _sum_usage_metrics(
            usage_metrics,
            merge_metric_metadata(extract_usage_metrics(repair_response_payload)),
        )
        repair_response_text = _extract_response_text(repair_response_payload)
        tool_events.extend(
            _build_tool_events(
                response_payload=repair_response_payload,
                response_text=repair_response_text,
                call_index_start=len(tool_events) + 1,
            )
        )
        repaired_patch, repaired_patch_source = extract_git_patch(repair_response_text)
        if repaired_patch.strip():
            patch_path = _persist_raw_patch(payload=payload, patch=repaired_patch, label="repair")
            if patch_path:
                raw_patch_paths["repair"] = patch_path
            response_payload = repair_response_payload
            response_text = repair_response_text
            patch = repaired_patch
            patch_source = f"repair:{repaired_patch_source}"

    if patch.strip():
        patch_apply_check_passed, patch_apply_error_text, apply_check_events = _check_patch_applies(
            workspace=workspace,
            patch=patch,
        )
        tool_events.extend(apply_check_events)
        patch_apply_error = patch_apply_error_text or None
        if patch_apply_check_passed:
            materialize_ok, materialize_error, materialized_patch, materialize_events = (
                _materialize_patch_from_workspace(workspace=workspace, patch=patch)
            )
            tool_events.extend(materialize_events)
            patch_materialized = materialize_ok
            if materialize_ok and materialized_patch.strip():
                patch = materialized_patch
                patch_source = f"workspace_apply:{patch_source}"
            elif not materialize_ok:
                patch_apply_check_passed = False
                patch_apply_error = materialize_error

        if not patch_apply_check_passed:
            failed_patch_path = (
                raw_patch_paths.get("repair")
                if patch_source.startswith("repair:")
                else raw_patch_paths.get("initial")
            )
            failed_patch_stage = "repair" if patch_source.startswith("repair:") else "initial"
            apply_repair_attempted = True
            repair_attempted = True
            apply_repair_prompt = _build_apply_repair_prompt(
                original_prompt=prompt,
                prior_response=response_text,
                patch=patch,
                apply_error=patch_apply_error_text,
                workspace=workspace,
            )
            apply_repair_request_body = _build_chat_request_body(
                prompt=apply_repair_prompt,
                model_name=model_name,
                payload=payload,
                runtime_config=runtime_config,
            )
            apply_repair_response_payload = _call_ollama_chat(
                base_url=base_url,
                body=apply_repair_request_body,
                timeout_seconds=timeout_seconds,
                auth_bearer_token=auth_bearer_token,
            )
            usage_metrics = _sum_usage_metrics(
                usage_metrics,
                merge_metric_metadata(extract_usage_metrics(apply_repair_response_payload)),
            )
            apply_repair_response_text = _extract_response_text(apply_repair_response_payload)
            tool_events.extend(
                _build_tool_events(
                    response_payload=apply_repair_response_payload,
                    response_text=apply_repair_response_text,
                    call_index_start=len(tool_events) + 1,
                )
            )
            apply_repaired_patch, apply_repaired_patch_source = extract_git_patch(
                apply_repair_response_text
            )
            if apply_repaired_patch.strip():
                patch_path = _persist_raw_patch(
                    payload=payload,
                    patch=apply_repaired_patch,
                    label="apply-repair",
                )
                if patch_path:
                    raw_patch_paths["apply_repair"] = patch_path
                patch = apply_repaired_patch
                patch_source = f"apply_repair:{apply_repaired_patch_source}"
                response_payload = apply_repair_response_payload
                response_text = apply_repair_response_text
                patch_apply_check_passed, patch_apply_error_text, apply_check_events = _check_patch_applies(
                    workspace=workspace,
                    patch=patch,
                )
                tool_events.extend(apply_check_events)
                patch_apply_error = patch_apply_error_text or None
                if not patch_apply_check_passed:
                    failed_patch_path = raw_patch_paths.get("apply_repair")
                    failed_patch_stage = "apply_repair"
                if patch_apply_check_passed:
                    materialize_ok, materialize_error, materialized_patch, materialize_events = (
                        _materialize_patch_from_workspace(workspace=workspace, patch=patch)
                    )
                    tool_events.extend(materialize_events)
                    patch_materialized = materialize_ok
                    if materialize_ok and materialized_patch.strip():
                        patch = materialized_patch
                        patch_source = f"workspace_apply:{patch_source}"
                    elif not materialize_ok:
                        patch_apply_check_passed = False
                        patch_apply_error = materialize_error

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

    strict_apply = _as_bool(
        os.environ.get("BENCHKIT_OLLAMA_STRICT_APPLY"),
        default=False,
    )

    if patch.strip() and not patch_apply_check_passed:
        if strict_apply:
            fatal_error(
                "ollama produced non-applying patch in strict apply mode",
                details={
                    "model_name": model_name,
                    "patch_source": patch_source,
                    "patch_apply_error": patch_apply_error,
                    "apply_repair_attempted": apply_repair_attempted,
                    "failed_patch_path": failed_patch_path,
                    "failed_patch_stage": failed_patch_stage,
                    "raw_patch_paths": raw_patch_paths,
                },
            )
            return
        patch = ""
        patch_source = "empty_fallback_apply_check_failed"

    if not patch.strip():
        allow_empty_patch = _as_bool(
            os.environ.get("BENCHKIT_ALLOW_EMPTY_OLLAMA_PATCH"),
            default=True,
        )
        forced_empty_due_apply_failure = patch_source == "empty_fallback_apply_check_failed"
        if allow_empty_patch or forced_empty_due_apply_failure:
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
                    "repair_attempted": repair_attempted,
                    "apply_repair_attempted": apply_repair_attempted,
                    "patch_apply_check_passed": patch_apply_check_passed,
                    "patch_apply_error": patch_apply_error,
                    "patch_materialized": patch_materialized,
                    "failed_patch_path": failed_patch_path,
                    "failed_patch_stage": failed_patch_stage,
                    "raw_patch_paths": raw_patch_paths,
                    "empty_patch_reason": (
                        "apply_check_failed"
                        if patch_source == "empty_fallback_apply_check_failed"
                        else "no_patch"
                    ),
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
                "repair_attempted": repair_attempted,
                "apply_repair_attempted": apply_repair_attempted,
                "patch_apply_check_passed": patch_apply_check_passed,
                "patch_apply_error": patch_apply_error,
                "patch_materialized": patch_materialized,
                "failed_patch_path": failed_patch_path,
                "failed_patch_stage": failed_patch_stage,
                "raw_patch_paths": raw_patch_paths,
                "hint": (
                    "Use a stronger coding model or set "
                    "BENCHKIT_ALLOW_EMPTY_OLLAMA_PATCH=1 for targeted smoke runs."
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
            "repair_attempted": repair_attempted,
            "apply_repair_attempted": apply_repair_attempted,
            "patch_apply_check_passed": patch_apply_check_passed,
            "patch_apply_error": patch_apply_error,
            "patch_materialized": patch_materialized,
            "failed_patch_path": failed_patch_path,
            "failed_patch_stage": failed_patch_stage,
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
