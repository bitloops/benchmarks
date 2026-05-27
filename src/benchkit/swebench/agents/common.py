#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable
from copy import deepcopy
import contextlib
import fcntl
import hashlib
import json
import os
import re
import shlex
import shutil
import signal
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.request

CANONICAL_METRIC_KEYS: tuple[str, ...] = (
    "token_input",
    "token_output",
    "reasoning_output_tokens",
    "total_tokens",
    "input_tokens",
    "output_tokens",
    "cached_input_tokens",
    "cached_output_tokens",
    "token_input_uncached",
    "token_output_uncached",
    "estimated_cost",
    "cache_creation_input_tokens",
    "cache_read_input_tokens",
    "total_input_processed_tokens",
    "total_processed_tokens",
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
BITLOOPS_GLOBAL_LOCK_ENV_VAR = "BENCHKIT_BITLOOPS_GLOBAL_LOCK_PATH"
BITLOOPS_DISABLE_GLOBAL_LOCK_ENV_VAR = "BENCHKIT_DISABLE_BITLOOPS_GLOBAL_LOCK"
BITLOOPS_TELEMETRY_OPTOUT_ENV_VAR = "BITLOOPS_TELEMETRY_OPTOUT"
BITLOOPS_PLATFORM_GATEWAY_TOKEN_ENV_VAR = "BITLOOPS_PLATFORM_GATEWAY_TOKEN"
BITLOOPS_TASK_DAEMON_STOP_TIMEOUT_SECONDS = 30
BITLOOPS_CONFIGURE_STATE_FILENAME = ".benchkit-bitloops-configure.json"
BITLOOPS_CONFIGURE_LOCK_FILENAME = ".benchkit-bitloops-configure.lock"


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


def _subprocess_env_for_cwd(
    env: dict[str, str] | None,
    cwd: str | None,
) -> dict[str, str] | None:
    if cwd is None:
        return env
    output = dict(os.environ if env is None else env)
    output["PWD"] = str(Path(cwd).resolve())
    return output


def call_command(
    command: list[str],
    timeout_seconds: int,
    *,
    env: dict[str, str] | None = None,
    cwd: str | None = None,
) -> tuple[str, str, int, int]:
    start = time.time()
    process = subprocess.Popen(
        command,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=_subprocess_env_for_cwd(env, cwd),
        cwd=cwd,
        start_new_session=True,
    )
    try:
        stdout, stderr = process.communicate(timeout=timeout_seconds)
    except subprocess.TimeoutExpired as exc:
        partial_stdout = exc.output if isinstance(exc.output, str) else ""
        partial_stderr = exc.stderr if isinstance(exc.stderr, str) else ""
        if hasattr(os, "killpg"):
            with contextlib.suppress(OSError):
                os.killpg(process.pid, signal.SIGTERM)
        else:
            with contextlib.suppress(OSError):
                process.terminate()
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            if hasattr(os, "killpg"):
                with contextlib.suppress(OSError):
                    os.killpg(process.pid, signal.SIGKILL)
            else:
                with contextlib.suppress(OSError):
                    process.kill()
            with contextlib.suppress(subprocess.TimeoutExpired, OSError):
                process.wait(timeout=2)
        stdout, stderr = process.communicate()
        raise subprocess.TimeoutExpired(
            command,
            timeout_seconds,
            output=stdout or partial_stdout,
            stderr=stderr or partial_stderr,
        ) from None
    elapsed_ms = int((time.time() - start) * 1000)
    return stdout, stderr, int(process.returncode or 0), elapsed_ms


class AgentCommandResult:
    def __init__(
        self,
        *,
        command: list[str],
        stdout: str,
        stderr: str,
        return_code: int,
        elapsed_ms: int,
    ) -> None:
        self.command = command
        self.stdout = stdout
        self.stderr = stderr
        self.return_code = return_code
        self.elapsed_ms = elapsed_ms


class AgentWrapperRunResult:
    def __init__(
        self,
        *,
        workspace: Path,
        bitloops_sandbox: dict[str, Any] | None,
        bitloops_env: dict[str, str] | None,
        command_env: dict[str, str] | None,
        bitloops_metadata: dict[str, Any],
        execution: AgentCommandResult,
        patch: str,
        patch_source: str,
        command_details: dict[str, Any],
    ) -> None:
        self.workspace = workspace
        self.bitloops_sandbox = bitloops_sandbox
        self.bitloops_env = bitloops_env
        self.command_env = command_env
        self.bitloops_metadata = bitloops_metadata
        self.execution = execution
        self.patch = patch
        self.patch_source = patch_source
        self.command_details = command_details


def add_bitloops_wrapper_args(parser: Any) -> Any:
    parser.add_argument(
        "--bitloops-init",
        action="store_true",
        help="Initialize Bitloops before running the agent command.",
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
        help="Embeddings runtime to apply in benchmark-generated Bitloops config.",
    )
    parser.add_argument(
        "--bitloops-no-embeddings",
        action="store_true",
        help="Disable embeddings setup in benchmark-generated Bitloops config.",
    )
    parser.add_argument(
        "--bitloops-no-summaries",
        action="store_true",
        help="Disable summaries setup in benchmark-generated Bitloops config.",
    )
    parser.add_argument(
        "--bitloops-summary-mode",
        choices=("auto", "off", "on"),
        help=(
            "Deprecated benchmark wrapper control. Prefer --bitloops-summaries-runtime "
            "and/or --bitloops-no-summaries."
        ),
    )
    parser.add_argument(
        "--bitloops-summaries-runtime",
        choices=("local", "platform"),
        help="Summaries runtime to configure during Bitloops init.",
    )
    parser.add_argument(
        "--bitloops-summary-embeddings-mode",
        choices=("on", "off"),
        help="Summary embeddings mode to forward to Bitloops init.",
    )
    parser.add_argument(
        "--bitloops-disable-devql-guidance",
        action="store_true",
        help="Disable Bitloops' repo-local DevQL guidance/bootstrap messages during init.",
    )
    parser.add_argument(
        "--bitloops-embedding-mode",
        choices=("off", "deterministic", "refresh_on_upgrade", "semantic_aware_once"),
        help="Repo-local Bitloops embedding mode override to apply after init.",
    )
    return parser


def _coerce_timeout_value(value: object) -> int:
    try:
        parsed = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0
    return parsed if parsed > 0 else 0


def resolve_timeout_seconds(
    payload: dict[str, object],
    *,
    env_var: str,
    default_seconds: int,
    extra_values: tuple[object, ...] = (),
) -> int:
    env_value = _coerce_timeout_value(os.environ.get(env_var, "").strip())

    run = payload.get("run", {})
    run_value = 0
    if isinstance(run, dict):
        run_value = _coerce_timeout_value(run.get("timeout_seconds"))

    candidates = [env_value, run_value, default_seconds]
    candidates.extend(_coerce_timeout_value(value) for value in extra_values)
    return max(candidates)


def resolve_bitloops_setup_timeout_seconds(payload: dict[str, object]) -> int:
    return resolve_timeout_seconds(
        payload,
        env_var="BITLOOPS_SETUP_TIMEOUT_SECONDS",
        default_seconds=1500,
    )


def _default_patch_extractor(stdout: str) -> tuple[str, str]:
    parsed_payload = parse_agent_payload(stdout)
    parsed_text = parse_agent_output(stdout, parsed_payload=parsed_payload)
    return extract_git_patch(parsed_text)


def run_agent_wrapper(
    *,
    payload: dict[str, Any],
    args: Any,
    agent_name: str,
    bitloops_setup_timeout_seconds: int,
    timeout_seconds: int | None = None,
    failure_message: str,
    command_runner: Callable[..., AgentCommandResult],
    command_env: dict[str, str] | None = None,
    failure_details: dict[str, Any] | None = None,
    post_command: Callable[[AgentCommandResult], dict[str, Any] | None] | None = None,
    patch_extractor: Callable[[str], tuple[str, str]] | None = None,
) -> AgentWrapperRunResult:
    workspace = resolve_workspace(payload)
    bitloops_sandbox = resolve_bitloops_sandbox(payload)
    bitloops_env = build_bitloops_task_environment(bitloops_sandbox)
    if getattr(args, "bitloops_init", False) and bitloops_env is None:
        bitloops_env = build_bitloops_telemetry_optout_environment()

    try:
        reset_workspace(workspace)
    except Exception as exc:
        fatal_error(
            "workspace reset failed",
            details={"error": str(exc), "workspace": str(workspace)},
        )

    bitloops_metadata: dict[str, Any] = {}
    task_daemon_handle = None
    if getattr(args, "bitloops_init", False):
        try:
            bitloops_metadata = setup_bitloops_for_workspace(
                agent_name=agent_name,
                timeout_seconds=bitloops_setup_timeout_seconds,
                sync=getattr(args, "bitloops_sync", "true") == "true",
                ingest=getattr(args, "bitloops_ingest", "true") == "true",
                embeddings_runtime=getattr(args, "bitloops_embeddings_runtime", None),
                no_embeddings=getattr(args, "bitloops_no_embeddings", False),
                no_summaries=getattr(args, "bitloops_no_summaries", False),
                summary_mode=getattr(args, "bitloops_summary_mode", None),
                summaries_runtime=getattr(args, "bitloops_summaries_runtime", None),
                summary_embeddings_mode=getattr(
                    args,
                    "bitloops_summary_embeddings_mode",
                    None,
                ),
                disable_devql_guidance=getattr(
                    args,
                    "bitloops_disable_devql_guidance",
                    False,
                ),
                embedding_mode=getattr(args, "bitloops_embedding_mode", None),
                sandbox=bitloops_sandbox,
                env=bitloops_env,
                cwd=str(workspace),
                return_task_daemon_handle=True,
            )
            task_daemon_handle = bitloops_metadata.pop("_bitloops_task_daemon_handle", None)
        except Exception as exc:
            stop_bitloops_task_daemon(task_daemon_handle)
            fatal_error(
                "bitloops setup failed",
                details={"error": str(exc), "workspace": str(workspace)},
            )

    effective_command_env = bitloops_env
    if command_env is not None:
        if effective_command_env is None:
            effective_command_env = dict(command_env)
        else:
            merged_command_env = dict(effective_command_env)
            merged_command_env.update(command_env)
            effective_command_env = merged_command_env
    effective_timeout_seconds = timeout_seconds or bitloops_setup_timeout_seconds
    try:
        execution = command_runner(
            timeout_seconds=effective_timeout_seconds,
            env=effective_command_env,
            cwd=str(workspace),
        )
    finally:
        stop_bitloops_task_daemon(task_daemon_handle)

    command_details = dict(failure_details or {})
    if post_command is not None:
        command_details.update(post_command(execution) or {})

    workspace_patch = capture_workspace_patch(workspace)
    if workspace_patch:
        patch = f"{workspace_patch}\n"
        patch_source = "workspace_git_diff"
    elif execution.return_code != 0:
        failure_summary = summarize_command_failure(execution.stdout, execution.stderr)
        fatal_error(
            failure_message,
            details={
                "return_code": execution.return_code,
                "command": execution.command,
                **command_details,
                **failure_summary,
            },
        )
    else:
        patch, patch_source = (patch_extractor or _default_patch_extractor)(execution.stdout)

    return AgentWrapperRunResult(
        workspace=workspace,
        bitloops_sandbox=bitloops_sandbox,
        bitloops_env=bitloops_env,
        command_env=effective_command_env,
        bitloops_metadata=bitloops_metadata,
        execution=execution,
        patch=patch,
        patch_source=patch_source,
        command_details=command_details,
    )


class BitloopsTaskDaemonHandle:
    def __init__(
        self,
        *,
        process: subprocess.Popen[str],
        port: int,
        stderr_log_path: Path,
        binary: str,
        env: dict[str, str],
        cwd: str | None,
    ) -> None:
        self.process = process
        self.port = port
        self.stderr_log_path = stderr_log_path
        self.binary = binary
        self.env = dict(env)
        self.cwd = cwd


def resolve_bitloops_sandbox(payload: dict[str, Any]) -> dict[str, Any] | None:
    run = payload.get("run", {})
    if not isinstance(run, dict):
        return None
    sandbox = run.get("bitloops_sandbox")
    if not isinstance(sandbox, dict):
        return None
    mode = str(sandbox.get("mode", "")).strip()
    if not mode:
        return None
    return sandbox


def build_bitloops_task_environment(sandbox: dict[str, Any] | None) -> dict[str, str] | None:
    if not isinstance(sandbox, dict):
        return None
    mode = str(sandbox.get("mode", "")).strip().lower()
    if mode != "per_task_daemon":
        return None

    env = build_bitloops_telemetry_optout_environment()
    original_home = str(env.get("HOME", "")).strip()
    if env.pop(BITLOOPS_PLATFORM_GATEWAY_TOKEN_ENV_VAR, None) is not None:
        env["BENCHKIT_BITLOOPS_STRIPPED_PLATFORM_GATEWAY_TOKEN"] = "1"
    sandbox_config_path = _resolve_bitloops_sandbox_daemon_config_path(sandbox)
    path_map = {
        "HOME": sandbox.get("home_root"),
        "USERPROFILE": sandbox.get("home_root"),
        "XDG_CONFIG_HOME": sandbox.get("xdg_config_home"),
        "XDG_STATE_HOME": sandbox.get("xdg_state_home"),
        "XDG_CACHE_HOME": sandbox.get("xdg_cache_home"),
        "XDG_DATA_HOME": sandbox.get("xdg_data_home"),
    }
    for key, value in path_map.items():
        if isinstance(value, str) and value.strip():
            env[key] = value.strip()
    if sandbox_config_path is not None:
        env["BITLOOPS_DAEMON_CONFIG_PATH_OVERRIDE"] = str(sandbox_config_path)
    sandbox_home = str(sandbox.get("home_root", "")).strip()
    if sandbox_home:
        # Keep shell startup lookup sandbox-local so zsh/bash tool calls
        # do not source host dotfiles with the rewritten sandbox HOME.
        env["ZDOTDIR"] = sandbox_home
    if original_home:
        env.setdefault("CARGO_HOME", str(Path(original_home) / ".cargo"))
        env.setdefault("RUSTUP_HOME", str(Path(original_home) / ".rustup"))
        # Keep Codex auth/session state anchored to the user's real Codex home.
        # In per-task sandbox mode, HOME is rewritten for Bitloops isolation.
        # Without CODEX_HOME, Codex falls back to sandbox HOME and loses auth.
        env.setdefault("CODEX_HOME", str(Path(original_home) / ".codex"))
        # Keep AWS SDK credential discovery anchored to the user's real home.
        # The task sandbox rewrites HOME/XDG roots for Bitloops, but Bedrock auth
        # still needs access to ~/.aws/{config,credentials}.
        env.setdefault("AWS_CONFIG_FILE", str(Path(original_home) / ".aws" / "config"))
        env.setdefault(
            "AWS_SHARED_CREDENTIALS_FILE",
            str(Path(original_home) / ".aws" / "credentials"),
        )
        if sandbox_home:
            _mirror_aws_auth_cache_into_sandbox(
                original_home=Path(original_home),
                sandbox_home=Path(sandbox_home),
            )
            _mirror_bitloops_auth_state_into_sandbox(
                original_home=Path(original_home),
                sandbox_home=Path(sandbox_home),
            )
            _ensure_macos_default_keychain_for_sandbox(
                original_home=Path(original_home),
                sandbox_home=Path(sandbox_home),
            )
        sandbox_xdg_config_home = str(sandbox.get("xdg_config_home", "")).strip()
        sandbox_xdg_data_home = str(sandbox.get("xdg_data_home", "")).strip()
        if sandbox_home and sandbox_xdg_config_home and sandbox_xdg_data_home:
            _mirror_opencode_state_into_sandbox(
                original_home=Path(original_home),
                sandbox_home=Path(sandbox_home),
                sandbox_xdg_config_home=Path(sandbox_xdg_config_home),
                sandbox_xdg_data_home=Path(sandbox_xdg_data_home),
            )
    env["BITLOOPS_BENCHKIT_SANDBOX_MODE"] = mode
    return env


def build_bitloops_telemetry_optout_environment(
    env: dict[str, str] | None = None,
) -> dict[str, str]:
    output = dict(os.environ if env is None else env)
    output[BITLOOPS_TELEMETRY_OPTOUT_ENV_VAR] = "1"
    return output


def _mirror_aws_auth_cache_into_sandbox(*, original_home: Path, sandbox_home: Path) -> None:
    aws_root = sandbox_home / ".aws"
    aws_root.mkdir(parents=True, exist_ok=True)
    for relative_dir in (
        Path(".aws") / "login" / "cache",
        Path(".aws") / "sso" / "cache",
    ):
        source = original_home / relative_dir
        destination = sandbox_home / relative_dir
        if not source.exists() or not source.is_dir():
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(source, destination, dirs_exist_ok=True)


def _resolve_bitloops_sandbox_daemon_config_path(sandbox: dict[str, Any] | None) -> Path | None:
    if not isinstance(sandbox, dict):
        return None
    home_root = str(sandbox.get("home_root", "")).strip()
    xdg_config_home = str(sandbox.get("xdg_config_home", "")).strip()

    candidates: list[Path] = []
    if sys.platform == "darwin" and home_root:
        candidates.append(
            Path(home_root) / "Library" / "Application Support" / "bitloops" / "config.toml"
        )
    if xdg_config_home:
        candidates.append(Path(xdg_config_home) / "bitloops" / "config.toml")
    if home_root:
        candidates.append(Path(home_root) / ".config" / "bitloops" / "config.toml")

    if not candidates:
        return None
    return candidates[0]


def _toml_string(value: str) -> str:
    return json.dumps(str(value))


def _replace_toml_table(content: str, table_name: str, body: str) -> str:
    table_text = f"[{table_name}]\n{body.rstrip()}\n"
    table_pattern = re.compile(
        rf"(?ms)^\[{re.escape(table_name)}\]\n.*?(?=^\[|\Z)"
    )
    if table_pattern.search(content):
        return table_pattern.sub(table_text, content, count=1)
    separator = "\n\n" if content.strip() else ""
    return f"{content.rstrip()}{separator}{table_text}"


def _seed_bitloops_cloud_inference_profiles(
    sandbox: dict[str, Any] | None,
    *,
    summary_mode: str | None,
    summaries_runtime: str | None = None,
    bind_semantic_inference: bool = False,
) -> Path | None:
    config_path = _resolve_bitloops_sandbox_daemon_config_path(sandbox)
    if config_path is None:
        return None

    config_root = config_path.parent
    platform_embeddings_binary = _resolve_bitloops_managed_tool_binary(
        "bitloops-platform-embeddings",
        config_root
        / "tools"
        / "bitloops-platform-embeddings"
        / "bitloops-platform-embeddings",
    )
    inference_binary = _resolve_bitloops_managed_tool_binary(
        "bitloops-inference",
        config_root
        / "tools"
        / "bitloops-inference"
        / "bitloops-inference",
    )
    relational_db_path = config_root / "stores" / "relational" / "relational.db"
    events_db_path = config_root / "stores" / "event" / "events.duckdb"
    blob_store_path = config_root / "stores" / "blob"
    effective_summary_mode = (
        summary_mode.strip().lower()
        if isinstance(summary_mode, str) and summary_mode.strip()
        else "off"
    )
    effective_summaries_runtime = (
        summaries_runtime.strip().lower()
        if isinstance(summaries_runtime, str) and summaries_runtime.strip()
        else None
    )

    config_path.parent.mkdir(parents=True, exist_ok=True)
    content = config_path.read_text(encoding="utf-8") if config_path.exists() else ""
    tables = {
        "runtime": "\n".join(
            [
                "local_dev = false",
            ]
        ),
        "stores": "",
        "stores.relational": f"sqlite_path = {_toml_string(str(relational_db_path))}",
        "stores.events": f"duckdb_path = {_toml_string(str(events_db_path))}",
        "stores.blob": f"local_path = {_toml_string(str(blob_store_path))}",
        "logging": "",
        "telemetry": "enabled = false",
        "dashboard": "",
        "dashboard.local_dashboard": "tls = false",
        "inference": "",
        "inference.runtimes": "",
        "inference.runtimes.bitloops_platform_embeddings": "\n".join(
            [
                f"command = {_toml_string(str(platform_embeddings_binary))}",
                'args = ["--api-key-env", "BITLOOPS_PLATFORM_GATEWAY_TOKEN"]',
                "startup_timeout_secs = 60",
                "request_timeout_secs = 300",
            ]
        ),
        "inference.runtimes.bitloops_inference": "\n".join(
            [
                f"command = {_toml_string(str(inference_binary))}",
                "args = []",
                "startup_timeout_secs = 60",
                "request_timeout_secs = 300",
            ]
        ),
        "inference.profiles": "",
        "inference.profiles.platform_code": "\n".join(
            [
                'task = "embeddings"',
                'driver = "bitloops_embeddings_ipc"',
                'runtime = "bitloops_platform_embeddings"',
                'model = "bge-m3"',
            ]
        ),
        "inference.profiles.guidance_llm": "\n".join(
            [
                'task = "text_generation"',
                'runtime = "bitloops_inference"',
                'driver = "bitloops_platform_chat"',
                'model = "ministral-3-3b-instruct"',
                'api_key = "${BITLOOPS_PLATFORM_GATEWAY_TOKEN}"',
                'temperature = "0.1"',
                "max_output_tokens = 4096",
            ]
        ),
        "inference.profiles.summary_llm": "\n".join(
            [
                'task = "text_generation"',
                'runtime = "bitloops_inference"',
                'driver = "bitloops_platform_chat"',
                'model = "ministral-3-3b-instruct"',
                'api_key = "${BITLOOPS_PLATFORM_GATEWAY_TOKEN}"',
                'temperature = "0.1"',
                "max_output_tokens = 200",
            ]
        ),
        "context_guidance": "",
        "context_guidance.inference": 'guidance_generation = "guidance_llm"',
        "semantic_clones": f'summary_mode = "{effective_summary_mode}"',
    }
    if bind_semantic_inference:
        semantic_inference = [
            'code_embeddings = "platform_code"',
        ]
        if effective_summary_mode != "off" and effective_summaries_runtime == "platform":
            semantic_inference.extend(
                [
                    'summary_generation = "summary_llm"',
                    'summary_embeddings = "platform_code"',
                ]
            )
        tables["semantic_clones.inference"] = "\n".join(semantic_inference)
    for table_name, body in tables.items():
        content = _replace_toml_table(content, table_name, body)
    config_path.write_text(content.rstrip() + "\n", encoding="utf-8")
    return config_path


def _resolve_bitloops_managed_tool_binary(tool_name: str, sandbox_binary: Path) -> Path:
    raw_host_home = os.environ.get("HOME", "").strip()
    host_home = Path(raw_host_home).expanduser() if raw_host_home else None
    candidates = []
    if host_home is not None:
        if sys.platform == "darwin":
            candidates.append(
                host_home
                / "Library"
                / "Application Support"
                / "bitloops"
                / "tools"
                / tool_name
                / tool_name
            )
        candidates.append(
            host_home / ".local" / "share" / "bitloops" / "tools" / tool_name / tool_name
        )
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return sandbox_binary


def _mirror_file_if_present(*, source: Path, destinations: tuple[Path, ...]) -> None:
    if not source.exists() or not source.is_file():
        return
    for destination in destinations:
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)


def _bitloops_auth_runtime_state_sources(original_home: Path) -> list[Path]:
    candidates = [
        original_home / ".local" / "state" / "bitloops" / "daemon" / "runtime.sqlite",
    ]
    if sys.platform == "darwin":
        candidates.append(
            original_home
            / "Library"
            / "Application Support"
            / "bitloops"
            / "stores"
            / "runtime"
            / "runtime.sqlite"
        )
    return list(dict.fromkeys(candidates))


def _mirror_bitloops_auth_state_into_sandbox(*, original_home: Path, sandbox_home: Path) -> None:
    rows: list[tuple[Any, ...]] = []
    for runtime_state_source in _bitloops_auth_runtime_state_sources(original_home):
        if not runtime_state_source.exists() or not runtime_state_source.is_file():
            continue

        try:
            source_connection = sqlite3.connect(
                f"file:{runtime_state_source}?mode=ro",
                uri=True,
                timeout=1.0,
            )
        except sqlite3.Error:
            continue

        try:
            rows = source_connection.execute(
                """
                SELECT document_kind, payload, updated_at
                FROM runtime_documents
                WHERE document_kind = ?
                """,
                ("workos_auth_session_state",),
            ).fetchall()
        except sqlite3.Error:
            rows = []
        finally:
            source_connection.close()

        if rows:
            break

    if not rows:
        return

    runtime_state_destination = (
        sandbox_home / ".local" / "state" / "bitloops" / "daemon" / "runtime.sqlite"
    )
    runtime_state_destination.parent.mkdir(parents=True, exist_ok=True)

    destination_connection = sqlite3.connect(runtime_state_destination)
    try:
        destination_connection.execute(
            """
            CREATE TABLE IF NOT EXISTS runtime_documents (
                document_kind TEXT PRIMARY KEY,
                payload TEXT NOT NULL,
                updated_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
            """
        )
        destination_connection.execute("DELETE FROM runtime_documents")
        destination_connection.executemany(
            """
            INSERT OR REPLACE INTO runtime_documents (document_kind, payload, updated_at)
            VALUES (?, ?, ?)
            """,
            rows,
        )
        destination_connection.commit()
    finally:
        destination_connection.close()


def _security_command_env_for_home(home: Path) -> dict[str, str]:
    env = dict(os.environ)
    home_text = str(home)
    env["HOME"] = home_text
    env["USERPROFILE"] = home_text
    return env


def _parse_security_default_keychain_output(stdout: str) -> str | None:
    text = str(stdout).strip()
    if not text:
        return None
    if text.startswith('"') and text.endswith('"') and len(text) >= 2:
        text = text[1:-1]
    return text.strip() or None


def _resolve_macos_default_keychain_for_home(home: Path) -> str | None:
    stdout, _stderr, return_code, _elapsed_ms = call_command(
        ["security", "default-keychain", "-d", "user"],
        5,
        env=_security_command_env_for_home(home),
    )
    if return_code != 0:
        return None
    return _parse_security_default_keychain_output(stdout)


def _ensure_macos_default_keychain_for_sandbox(
    *,
    original_home: Path,
    sandbox_home: Path,
) -> None:
    if sys.platform != "darwin":
        return
    sandbox_preferences = sandbox_home / "Library" / "Preferences"
    sandbox_preferences.mkdir(parents=True, exist_ok=True)
    if _resolve_macos_default_keychain_for_home(sandbox_home):
        return

    default_keychain = _resolve_macos_default_keychain_for_home(original_home)
    if not default_keychain:
        return

    call_command(
        ["security", "default-keychain", "-d", "user", "-s", default_keychain],
        5,
        env=_security_command_env_for_home(sandbox_home),
    )


def _mirror_opencode_state_into_sandbox(
    *,
    original_home: Path,
    sandbox_home: Path,
    sandbox_xdg_config_home: Path,
    sandbox_xdg_data_home: Path,
) -> None:
    auth_source = original_home / ".local" / "share" / "opencode" / "auth.json"
    _mirror_file_if_present(
        source=auth_source,
        destinations=(
            sandbox_xdg_data_home / "opencode" / "auth.json",
            sandbox_home / ".local" / "share" / "opencode" / "auth.json",
        ),
    )

    config_source = original_home / ".config" / "opencode" / "opencode.json"
    _mirror_file_if_present(
        source=config_source,
        destinations=(
            sandbox_xdg_config_home / "opencode" / "opencode.json",
            sandbox_home / ".config" / "opencode" / "opencode.json",
        ),
    )


def _ensure_sandbox_directories(sandbox: dict[str, Any]) -> None:
    for key in (
        "sandbox_root",
        "home_root",
        "xdg_config_home",
        "xdg_state_home",
        "xdg_cache_home",
        "xdg_data_home",
    ):
        value = sandbox.get(key)
        if isinstance(value, str) and value.strip():
            Path(value).mkdir(parents=True, exist_ok=True)


def _allocate_localhost_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        sock.listen(1)
        return int(sock.getsockname()[1])


def _wait_for_task_daemon_ready(
    *,
    timeout: int,
    port: int,
    process: subprocess.Popen[str],
    stderr_log_path: Path,
) -> None:
    deadline = time.time() + timeout
    probe_url = f"http://127.0.0.1:{port}/devql/sdl"
    while time.time() < deadline:
        return_code = process.poll()
        if return_code is not None:
            stderr_tail = ""
            if stderr_log_path.exists():
                with contextlib.suppress(OSError):
                    lines = stderr_log_path.read_text(encoding="utf-8", errors="replace").splitlines()
                    stderr_tail = "\n".join(lines[-20:]).strip()
            detail = f"; stderr tail:\n{stderr_tail}" if stderr_tail else ""
            raise RuntimeError(
                f"task daemon exited before becoming ready (exit={return_code}){detail}"
            )
        try:
            with urllib.request.urlopen(probe_url, timeout=1):
                return
        except OSError:
            pass
        time.sleep(0.2)
    raise TimeoutError(f"timed out waiting for task daemon readiness on port {port}")


def _resolve_bitloops_runtime_db_path(env: dict[str, str] | None) -> Path | None:
    if not env:
        return None
    home = str(env.get("HOME", "")).strip()
    if not home:
        return None
    return Path(home) / ".local" / "state" / "bitloops" / "daemon" / "runtime.sqlite"


def _load_runtime_document(
    runtime_db_path: Path | None,
    *,
    document_kind: str,
) -> dict[str, Any] | None:
    if runtime_db_path is None or not runtime_db_path.exists():
        return None
    try:
        connection = sqlite3.connect(f"file:{runtime_db_path}?mode=ro", uri=True, timeout=1.0)
    except sqlite3.Error:
        return None
    try:
        row = connection.execute(
            "SELECT payload FROM runtime_documents WHERE document_kind = ?",
            (document_kind,),
        ).fetchone()
    except sqlite3.Error:
        return None
    finally:
        connection.close()

    if row is None or not row[0]:
        return None
    try:
        payload = json.loads(str(row[0]))
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def _bitloops_authorization_metadata(
    runtime_db_path: Path | None,
    *,
    env: dict[str, str] | None = None,
    binary: str | None = None,
    cwd: str | None = None,
    timeout_seconds: int = 15,
) -> dict[str, Any]:
    auth_state = _load_runtime_document(
        runtime_db_path,
        document_kind="workos_auth_session_state",
    )
    gateway_token_present = bool(
        str((env or {}).get(BITLOOPS_PLATFORM_GATEWAY_TOKEN_ENV_VAR, "")).strip()
    )
    login_token_probe_attempted = bool(binary and env is not None)
    login_token_probe_ok = False
    login_token_probe_elapsed_ms: int | None = None
    login_token_probe_return_code: int | None = None
    login_token_probe_timed_out = False
    if login_token_probe_attempted:
        try:
            stdout, _stderr, return_code, elapsed_ms = call_command(
                [str(binary), "login", "token"],
                timeout_seconds,
                env=env,
                cwd=cwd,
            )
            login_token_probe_elapsed_ms = elapsed_ms
            login_token_probe_return_code = return_code
            login_token_probe_ok = return_code == 0 and bool(stdout.strip())
        except subprocess.TimeoutExpired:
            login_token_probe_timed_out = True

    authorization_source = None
    if auth_state is not None:
        authorization_source = "runtime_document"
    elif login_token_probe_ok:
        authorization_source = "login_token_probe"
    elif gateway_token_present and not login_token_probe_attempted:
        authorization_source = f"env:{BITLOOPS_PLATFORM_GATEWAY_TOKEN_ENV_VAR}"
    return {
        "bitloops_cloud_authorized": authorization_source is not None,
        "bitloops_cloud_authorization_source": authorization_source,
        "bitloops_platform_gateway_token_present": gateway_token_present,
        "bitloops_login_token_probe_attempted": login_token_probe_attempted,
        "bitloops_login_token_probe_ok": login_token_probe_ok,
        "bitloops_login_token_probe_elapsed_ms": login_token_probe_elapsed_ms,
        "bitloops_login_token_probe_return_code": login_token_probe_return_code,
        "bitloops_login_token_probe_timed_out": login_token_probe_timed_out,
        "bitloops_auth_runtime_db_path": (
            str(runtime_db_path) if runtime_db_path is not None else None
        ),
    }


def _bitloops_init_environment_with_fresh_platform_token(
    *,
    binary: str,
    env: dict[str, str],
    cwd: str | None,
    timeout_seconds: int = 15,
) -> tuple[dict[str, str], dict[str, Any]]:
    init_env = dict(env)
    metadata: dict[str, Any] = {
        "bitloops_init_platform_token_probe_attempted": True,
        "bitloops_init_platform_token_probe_ok": False,
        "bitloops_init_platform_token_probe_elapsed_ms": None,
        "bitloops_init_platform_token_probe_return_code": None,
        "bitloops_init_platform_token_probe_timed_out": False,
        "bitloops_init_platform_token_injected": False,
    }
    try:
        stdout, _stderr, return_code, elapsed_ms = call_command(
            [binary, "login", "token"],
            timeout_seconds,
            env=env,
            cwd=cwd,
        )
    except subprocess.TimeoutExpired:
        metadata["bitloops_init_platform_token_probe_timed_out"] = True
        return init_env, metadata

    token = stdout.strip()
    metadata["bitloops_init_platform_token_probe_elapsed_ms"] = elapsed_ms
    metadata["bitloops_init_platform_token_probe_return_code"] = return_code
    metadata["bitloops_init_platform_token_probe_ok"] = return_code == 0 and bool(token)
    if return_code == 0 and token:
        init_env[BITLOOPS_PLATFORM_GATEWAY_TOKEN_ENV_VAR] = token
        metadata["bitloops_init_platform_token_injected"] = True
    return init_env, metadata


def _bitloops_relational_db_path_for_session(
    latest_session: dict[str, Any],
) -> Path | None:
    daemon_config_root = str(latest_session.get("daemon_config_root", "")).strip()
    if not daemon_config_root:
        return None
    return Path(daemon_config_root) / "stores" / "relational" / "relational.db"


def _count_relational_rows(
    relational_db_path: Path | None,
    sql: str,
) -> int | None:
    if relational_db_path is None or not relational_db_path.exists():
        return None
    try:
        connection = sqlite3.connect(
            f"file:{relational_db_path}?mode=ro",
            uri=True,
            timeout=1.0,
        )
    except sqlite3.Error:
        return None
    try:
        row = connection.execute(sql).fetchone()
    except sqlite3.Error:
        return None
    finally:
        connection.close()
    if row is None:
        return None
    try:
        return int(row[0])
    except (TypeError, ValueError):
        return None


def _summary_materialization_ready_for_session(
    latest_session: dict[str, Any],
) -> tuple[bool, dict[str, Any]]:
    selections = latest_session.get("selections")
    run_summaries = isinstance(selections, dict) and bool(selections.get("run_summaries"))
    run_summary_embeddings = isinstance(selections, dict) and bool(
        selections.get("run_summary_embeddings")
    )
    relational_db_path = _bitloops_relational_db_path_for_session(latest_session)
    summary_rows = _count_relational_rows(
        relational_db_path,
        "SELECT COUNT(*) FROM symbol_semantics_current",
    )
    summary_embedding_rows = _count_relational_rows(
        relational_db_path,
        (
            "SELECT COUNT(*) FROM symbol_embeddings_current "
            "WHERE representation_kind = 'summary'"
        ),
    )
    metadata = {
        "summary_materialization_required": run_summaries,
        "summary_embeddings_required": run_summary_embeddings,
        "summary_relational_db_path": (
            str(relational_db_path) if relational_db_path is not None else None
        ),
        "summary_rows": summary_rows,
        "summary_embedding_rows": summary_embedding_rows,
    }
    summaries_ready = not run_summaries or (summary_rows is not None and summary_rows > 0)
    summary_embedding_coverage_complete = (
        summary_rows is not None
        and summary_embedding_rows is not None
        and summary_embedding_rows >= summary_rows
    )
    summary_embedding_rows_missing = (
        max(summary_rows - summary_embedding_rows, 0)
        if summary_rows is not None and summary_embedding_rows is not None
        else None
    )
    summary_embeddings_ready = (
        not run_summary_embeddings
        or (
            summary_embedding_rows is not None
            and summary_embedding_rows > 0
            and (not run_summaries or summary_embedding_coverage_complete)
        )
    )
    metadata["summary_embedding_coverage_complete"] = summary_embedding_coverage_complete
    metadata["summary_embedding_rows_missing"] = summary_embedding_rows_missing
    metadata["summary_materialization_ready"] = summaries_ready
    metadata["summary_embeddings_ready"] = summary_embeddings_ready
    return summaries_ready and summary_embeddings_ready, metadata


def _embeddings_gate_config_path_candidates(
    latest_session: dict[str, Any],
    *,
    repo_root: str,
) -> list[str]:
    candidates: list[str] = []

    daemon_config_root = str(latest_session.get("daemon_config_root", "")).strip()
    if daemon_config_root:
        candidates.append(str((Path(daemon_config_root) / "config.toml").resolve()))

    candidates.append(str((Path(repo_root) / "config.toml").resolve()))
    candidates.append(str((Path(repo_root) / ".bitloops" / "config.toml").resolve()))
    return list(dict.fromkeys(candidates))


def _resolve_embeddings_gate_entry(
    embeddings_state: dict[str, Any] | None,
    *,
    latest_session: dict[str, Any],
    repo_root: str,
) -> dict[str, Any] | None:
    if not isinstance(embeddings_state, dict):
        return None

    entries = embeddings_state.get("entries")
    if not isinstance(entries, dict) or not entries:
        return None

    candidates = set(
        _embeddings_gate_config_path_candidates(
            latest_session,
            repo_root=repo_root,
        )
    )
    for key, entry in entries.items():
        if not isinstance(entry, dict):
            continue
        entry_config_path = str(entry.get("config_path", "")).strip()
        entry_key = str(key).strip()
        if entry_config_path in candidates or entry_key in candidates:
            return entry

    if len(entries) == 1:
        only_entry = next(iter(entries.values()))
        return only_entry if isinstance(only_entry, dict) else None

    return None


def _bitloops_embeddings_ready_for_session(
    latest_session: dict[str, Any],
    *,
    embeddings_state: dict[str, Any] | None,
    repo_root: str,
) -> tuple[bool, dict[str, Any]]:
    selections = latest_session.get("selections", {})
    run_code_embeddings = (
        isinstance(selections, dict) and bool(selections.get("run_code_embeddings"))
    )
    embeddings_requested = False
    if isinstance(selections, dict):
        embeddings_requested = selections.get("embeddings_bootstrap") is not None

    completion_seq = latest_session.get("embeddings_bootstrap_completion_seq")
    active_task_id = latest_session.get("embeddings_bootstrap_task_id")
    if not embeddings_requested:
        embeddings_requested = completion_seq is not None or bool(active_task_id)

    relational_db_path = _bitloops_relational_db_path_for_session(latest_session)
    code_embedding_rows = _count_relational_rows(
        relational_db_path,
        (
            "SELECT COUNT(*) FROM symbol_embeddings_current "
            "WHERE representation_kind = 'code'"
        ),
    )
    identity_embedding_rows = _count_relational_rows(
        relational_db_path,
        (
            "SELECT COUNT(*) FROM symbol_embeddings_current "
            "WHERE representation_kind = 'identity'"
        ),
    )
    code_embedding_counts_match = (
        code_embedding_rows is not None
        and identity_embedding_rows is not None
        and code_embedding_rows == identity_embedding_rows
    )
    code_embeddings_ready = (
        not run_code_embeddings
        or embeddings_requested
        or (
            code_embedding_rows is not None
            and identity_embedding_rows is not None
            and code_embedding_rows > 0
            and identity_embedding_rows > 0
            and code_embedding_counts_match
        )
    )

    metadata: dict[str, Any] = {
        "embeddings_bootstrap_requested": embeddings_requested,
        "embeddings_bootstrap_completion_seq": completion_seq,
        "embeddings_bootstrap_task_id": active_task_id,
        "code_embeddings_required": run_code_embeddings,
        "code_embedding_rows": code_embedding_rows,
        "identity_embedding_rows": identity_embedding_rows,
        "code_embedding_counts_match": code_embedding_counts_match,
        "code_embeddings_ready": code_embeddings_ready,
    }
    if not embeddings_requested:
        metadata["embeddings_gate_readiness"] = "not_requested"
        metadata["embeddings_gate_blocked"] = False
        return code_embeddings_ready, metadata

    gate_entry = _resolve_embeddings_gate_entry(
        embeddings_state,
        latest_session=latest_session,
        repo_root=repo_root,
    )
    if gate_entry is None:
        gate_ready = completion_seq is not None
        metadata["embeddings_gate_readiness"] = "ready" if gate_ready else "unknown"
        metadata["embeddings_gate_blocked"] = not gate_ready
        metadata["embeddings_gate_last_error"] = None
        metadata["embeddings_gate_profile_name"] = None
        metadata["embeddings_gate_last_updated_unix"] = None
        metadata["embeddings_gate_source"] = "completion_seq_fallback" if gate_ready else "missing"
        return gate_ready, metadata

    readiness = str(gate_entry.get("readiness", "")).strip().lower()
    metadata["embeddings_gate_readiness"] = readiness or None
    metadata["embeddings_gate_blocked"] = readiness != "ready"
    metadata["embeddings_gate_last_error"] = gate_entry.get("last_error")
    metadata["embeddings_gate_profile_name"] = gate_entry.get("profile_name")
    metadata["embeddings_gate_last_updated_unix"] = gate_entry.get("last_updated_unix")
    metadata["embeddings_gate_active_task_id"] = gate_entry.get("active_task_id")
    metadata["embeddings_gate_source"] = "embeddings_bootstrap_state"
    return readiness == "ready", metadata


def _active_bitloops_summary_inference_commands(repo_root: str | None) -> list[str]:
    marker = str(repo_root or "").strip()
    if not marker:
        return []

    stdout = ""
    for command in (["ps", "-axo", "command="], ["ps", "-eo", "command="]):
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=5,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            continue
        if result.returncode == 0:
            stdout = result.stdout
            break
    if not stdout:
        return []

    active: list[str] = []
    for line in stdout.splitlines():
        command_line = line.strip()
        if (
            marker in command_line
            and "bitloops-inference" in command_line
            and " run " in f" {command_line} "
            and "--profile summary_llm" in command_line
        ):
            active.append(command_line)
    return active


def _sync_task_completed_successfully(task: dict[str, Any]) -> bool:
    progress = task.get("progress")
    result = task.get("result")
    return (
        str(task.get("status", "")).strip() == "completed"
        and isinstance(progress, dict)
        and str(progress.get("type", "")).strip() == "sync"
        and isinstance(progress.get("value"), dict)
        and str(progress["value"].get("phase", "")).strip() == "complete"
        and isinstance(result, dict)
        and str(result.get("type", "")).strip() == "sync"
        and isinstance(result.get("value"), dict)
        and bool(result["value"].get("success"))
    )


def _follow_up_sync_satisfied(
    *,
    latest_session: dict[str, Any],
    repo_tasks: list[dict[str, Any]],
) -> tuple[bool, dict[str, Any] | None]:
    if latest_session.get("follow_up_sync_required") is False:
        return True, None
    if latest_session.get("follow_up_sync_required") is not True:
        return False, None

    session_submitted_at = int(latest_session.get("submitted_at_unix") or 0)
    follow_up_tasks = [
        task
        for task in repo_tasks
        if str(task.get("kind", "")).strip() == "sync"
        and str(task.get("source", "")).strip() == "watcher"
        and int(task.get("submitted_at_unix") or 0) >= session_submitted_at
        and _sync_task_completed_successfully(task)
    ]
    if not follow_up_tasks:
        return False, None
    follow_up_tasks.sort(key=lambda item: int(item.get("submitted_at_unix") or 0))
    return True, follow_up_tasks[-1]


def _init_devql_tasks_ready(
    *,
    latest_session: dict[str, Any],
    repo_tasks: list[dict[str, Any]],
) -> tuple[bool, dict[str, Any]]:
    init_session_id = str(latest_session.get("init_session_id", "")).strip()
    init_tasks = [
        task
        for task in repo_tasks
        if str(task.get("source", "")).strip() == "init"
        and (
            not init_session_id
            or str(task.get("init_session_id", "")).strip() == init_session_id
        )
    ]
    status_counts: dict[str, int] = {}
    completed_kinds: set[str] = set()
    incomplete_tasks: list[dict[str, Any]] = []
    for task in init_tasks:
        status = str(task.get("status", "")).strip() or "<missing>"
        kind = str(task.get("kind", "")).strip() or "<missing>"
        status_counts[f"{kind}:{status}"] = status_counts.get(f"{kind}:{status}", 0) + 1
        if status == "completed":
            completed_kinds.add(kind)
        else:
            incomplete_tasks.append(
                {
                    "task_id": task.get("task_id"),
                    "kind": kind,
                    "status": status,
                    "error": task.get("error"),
                }
            )

    selections = latest_session.get("selections")
    required_kinds: set[str] = set()
    if isinstance(selections, dict):
        if bool(selections.get("run_ingest")):
            required_kinds.add("ingest")
        if selections.get("embeddings_bootstrap") is not None:
            required_kinds.add("embeddings_bootstrap")
    if latest_session.get("embeddings_bootstrap_task_id"):
        required_kinds.add("embeddings_bootstrap")

    missing_kinds = sorted(kind for kind in required_kinds if kind not in completed_kinds)
    failed_tasks = [
        task
        for task in incomplete_tasks
        if str(task.get("status", "")).strip() == "failed"
    ]
    metadata = {
        "init_queue_task_status_counts": status_counts,
        "init_queue_required_kinds": sorted(required_kinds),
        "init_queue_missing_kinds": missing_kinds,
        "init_queue_incomplete_tasks": incomplete_tasks,
        "init_queue_failed_tasks": failed_tasks,
    }
    return not incomplete_tasks and not missing_kinds, metadata


def _bitloops_init_ready_via_runtime_state(
    *,
    runtime_db_path: Path | None,
    repo_root: str | None,
) -> tuple[bool, dict[str, Any] | None]:
    repo_root_value = str(repo_root or "").strip()
    if not repo_root_value:
        return False, None

    queue_state = _load_runtime_document(
        runtime_db_path,
        document_kind="devql_task_queue_state",
    )
    init_state = _load_runtime_document(
        runtime_db_path,
        document_kind="init_session_state",
    )
    enrichment_state = _load_runtime_document(
        runtime_db_path,
        document_kind="enrichment_queue_state",
    )
    embeddings_state = _load_runtime_document(
        runtime_db_path,
        document_kind="embeddings_bootstrap_state",
    )
    if queue_state is None or init_state is None:
        return False, None

    tasks = queue_state.get("tasks", [])
    sessions = init_state.get("sessions", [])
    if not isinstance(tasks, list) or not isinstance(sessions, list):
        return False, None

    all_repo_tasks = [
        task for task in tasks
        if isinstance(task, dict)
        and str(task.get("repo_root", "")).strip() == repo_root_value
    ]
    repo_tasks = [
        task for task in all_repo_tasks
        if isinstance(task, dict)
        and str(task.get("source", "")).strip() == "init"
        and str(task.get("kind", "")).strip() == "sync"
    ]
    if not repo_tasks:
        return False, None
    repo_tasks.sort(key=lambda item: int(item.get("submitted_at_unix") or 0))
    latest_task = repo_tasks[-1]
    latest_init_session_id = str(latest_task.get("init_session_id", "")).strip()

    repo_sessions = [
        session for session in sessions
        if isinstance(session, dict)
        and str(session.get("repo_root", "")).strip() == repo_root_value
        and (
            not latest_init_session_id
            or str(session.get("init_session_id", "")).strip() == latest_init_session_id
        )
    ]
    if not repo_sessions:
        return False, None
    repo_sessions.sort(key=lambda item: int(item.get("submitted_at_unix") or 0))
    latest_session = repo_sessions[-1]

    progress = latest_task.get("progress")
    result = latest_task.get("result")
    selections = latest_session.get("selections")
    sync_complete = _sync_task_completed_successfully(latest_task)
    (
        follow_up_sync_satisfied,
        follow_up_sync_task,
    ) = _follow_up_sync_satisfied(
        latest_session=latest_session,
        repo_tasks=all_repo_tasks,
    )
    init_queue_ready, init_queue_metadata = _init_devql_tasks_ready(
        latest_session=latest_session,
        repo_tasks=all_repo_tasks,
    )
    initial_sync_recorded = latest_session.get("initial_sync_completion_seq") is not None
    no_background_jobs = True
    if isinstance(enrichment_state, dict):
        jobs = enrichment_state.get("jobs")
        if isinstance(jobs, list):
            no_background_jobs = len(jobs) == 0

    embeddings_ready, embeddings_metadata = _bitloops_embeddings_ready_for_session(
        latest_session,
        embeddings_state=embeddings_state,
        repo_root=repo_root_value,
    )
    active_summary_inference_commands = _active_bitloops_summary_inference_commands(
        repo_root_value
    )
    no_active_summary_inference = not active_summary_inference_commands
    summary_materialized, summary_metadata = _summary_materialization_ready_for_session(
        latest_session
    )
    progress_value = progress.get("value", {}) if isinstance(progress, dict) else {}
    result_value = result.get("value", {}) if isinstance(result, dict) else {}
    metadata = {
        "repo_root": repo_root_value,
        "runtime_db_path": str(runtime_db_path) if runtime_db_path is not None else None,
        "init_session_id": latest_session.get("init_session_id"),
        "sync_task_id": latest_task.get("task_id"),
        "sync_status": latest_task.get("status"),
        "sync_phase": progress_value.get("phase"),
        "paths_total": progress_value.get("pathsTotal"),
        "paths_completed": progress_value.get("pathsCompleted"),
        "paths_remaining": progress_value.get("pathsRemaining"),
        "parse_errors": progress_value.get("parseErrors"),
        "sync_success": result_value.get("success"),
        "follow_up_sync_required": latest_session.get("follow_up_sync_required"),
        "follow_up_sync_satisfied": follow_up_sync_satisfied,
        "follow_up_sync_task_id": (
            follow_up_sync_task.get("task_id")
            if isinstance(follow_up_sync_task, dict)
            else None
        ),
        "initial_sync_completion_seq": latest_session.get("initial_sync_completion_seq"),
        "active_summary_inference_processes": len(active_summary_inference_commands),
        **init_queue_metadata,
        **embeddings_metadata,
        **summary_metadata,
    }

    if not init_queue_ready and init_queue_metadata.get("init_queue_failed_tasks"):
        return False, metadata

    ready = (
        sync_complete
        and follow_up_sync_satisfied
        and initial_sync_recorded
        and (
            not isinstance(selections, dict)
            or bool(selections.get("run_sync", False))
        )
        and init_queue_ready
        and no_background_jobs
        and embeddings_ready
        and no_active_summary_inference
        and summary_materialized
        and no_active_summary_inference
    )
    if not ready:
        return False, metadata

    return True, metadata


def _run_command_with_runtime_ready_shortcut(
    *,
    command: list[str],
    timeout_seconds: int,
    env: dict[str, str] | None,
    cwd: str | None,
) -> tuple[str, str, int, int, dict[str, Any] | None]:
    start = time.time()
    runtime_db_path = _resolve_bitloops_runtime_db_path(env)
    process = subprocess.Popen(
        command,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        cwd=cwd,
    )
    while True:
        if process.poll() is not None:
            stdout, stderr = process.communicate()
            elapsed_ms = int((time.time() - start) * 1000)
            return stdout, stderr, int(process.returncode or 0), elapsed_ms, None

        ready, metadata = _bitloops_init_ready_via_runtime_state(
            runtime_db_path=runtime_db_path,
            repo_root=cwd,
        )
        if ready:
            with contextlib.suppress(OSError):
                process.terminate()
            try:
                stdout, stderr = process.communicate(timeout=2)
            except subprocess.TimeoutExpired:
                with contextlib.suppress(OSError):
                    process.kill()
                stdout, stderr = process.communicate()
            elapsed_ms = int((time.time() - start) * 1000)
            return stdout, stderr, 0, elapsed_ms, metadata

        if (time.time() - start) >= timeout_seconds:
            with contextlib.suppress(OSError):
                process.terminate()
            try:
                stdout, stderr = process.communicate(timeout=2)
            except subprocess.TimeoutExpired:
                with contextlib.suppress(OSError):
                    process.kill()
                stdout, stderr = process.communicate()
            raise subprocess.TimeoutExpired(
                command,
                timeout_seconds,
                output=stdout,
                stderr=stderr,
            )
        time.sleep(1.0)


def _wait_for_bitloops_runtime_ready(
    *,
    timeout_seconds: int,
    env: dict[str, str] | None,
    cwd: str | None,
    required: bool,
) -> dict[str, Any]:
    if not required:
        return {
            "bitloops_runtime_ready_wait_attempted": False,
            "bitloops_runtime_ready": None,
            "bitloops_runtime_ready_wait_elapsed_ms": 0,
            "bitloops_runtime_ready_stable_polls": 0,
            "bitloops_runtime_ready_metadata": None,
        }

    start = time.time()
    runtime_db_path = _resolve_bitloops_runtime_db_path(env)
    stable_polls_required = max(
        1,
        int(os.environ.get("BITLOOPS_RUNTIME_READY_STABLE_POLLS", "3")),
    )
    poll_interval_seconds = max(
        0.1,
        float(os.environ.get("BITLOOPS_RUNTIME_READY_POLL_INTERVAL_SECONDS", "1")),
    )
    ready_streak = 0
    last_metadata: dict[str, Any] | None = None

    while (time.time() - start) < timeout_seconds:
        ready, metadata = _bitloops_init_ready_via_runtime_state(
            runtime_db_path=runtime_db_path,
            repo_root=cwd,
        )
        last_metadata = metadata
        if (
            isinstance(metadata, dict)
            and metadata.get("init_queue_failed_tasks")
        ):
            raise RuntimeError(
                "Bitloops init task failed while waiting for runtime readiness: "
                + json.dumps(metadata, ensure_ascii=False)
            )
        if ready:
            ready_streak += 1
            if ready_streak >= stable_polls_required:
                return {
                    "bitloops_runtime_ready_wait_attempted": True,
                    "bitloops_runtime_ready": True,
                    "bitloops_runtime_ready_wait_elapsed_ms": int(
                        (time.time() - start) * 1000
                    ),
                    "bitloops_runtime_ready_stable_polls": ready_streak,
                    "bitloops_runtime_ready_metadata": metadata,
                }
        else:
            ready_streak = 0
        time.sleep(poll_interval_seconds)

    raise TimeoutError(
        "timed out waiting for Bitloops runtime readiness after init: "
        + json.dumps(
            {
                "runtime_db_path": str(runtime_db_path) if runtime_db_path else None,
                "repo_root": cwd,
                "last_metadata": last_metadata,
                "stable_polls_required": stable_polls_required,
            },
            ensure_ascii=False,
        )
    )


def _bitloops_init_status_is_terminal(status: str) -> bool:
    return status.strip().lower() in {"completed", "completed_with_warnings", "failed"}


def _bitloops_init_status_is_success(status: str) -> bool:
    return status.strip().lower() in {"completed", "completed_with_warnings"}


def _parse_bitloops_init_status_payload(stdout: str) -> dict[str, Any] | None:
    text = str(stdout).strip()
    if not text:
        return None
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def _run_command_with_init_status_shortcut(
    *,
    command: list[str],
    timeout_seconds: int,
    env: dict[str, str] | None,
    cwd: str | None,
) -> tuple[str, str, int, int, dict[str, Any] | None]:
    start = time.time()
    process = subprocess.Popen(
        command,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        cwd=cwd,
    )
    status_command = [command[0], "init", "status", "--json"]
    last_status_payload: dict[str, Any] | None = None
    status_poll_timeout_seconds = max(1, min(timeout_seconds, 10))

    while True:
        if process.poll() is not None:
            stdout, stderr = process.communicate()
            elapsed_ms = int((time.time() - start) * 1000)
            return stdout, stderr, int(process.returncode or 0), elapsed_ms, None

        try:
            (
                status_stdout,
                status_stderr,
                status_code,
                _status_elapsed_ms,
            ) = call_command(
                status_command,
                status_poll_timeout_seconds,
                env=env,
                cwd=cwd,
            )
        except subprocess.TimeoutExpired:
            status_stdout = ""
            status_stderr = ""
            status_code = -1
        if status_code == 0:
            last_status_payload = _parse_bitloops_init_status_payload(status_stdout)
            session = (
                last_status_payload.get("session")
                if isinstance(last_status_payload, dict)
                else None
            )
            if isinstance(session, dict):
                session_status = str(session.get("status") or "").strip().lower()
                if _bitloops_init_status_is_terminal(session_status):
                    with contextlib.suppress(OSError):
                        process.terminate()
                    try:
                        stdout, stderr = process.communicate(timeout=2)
                    except subprocess.TimeoutExpired:
                        with contextlib.suppress(OSError):
                            process.kill()
                        stdout, stderr = process.communicate()
                    elapsed_ms = int((time.time() - start) * 1000)
                    if not _bitloops_init_status_is_success(session_status):
                        return_code = int(process.returncode or 1)
                        terminal_error = str(session.get("terminalError") or "").strip()
                        if terminal_error and not stderr.strip():
                            stderr = terminal_error
                    else:
                        return_code = 0
                    metadata = {
                        "status_command": status_command,
                        "current_init_session_id": last_status_payload.get(
                            "currentInitSessionId"
                        ),
                        "session": session,
                        "status_stderr": status_stderr.strip(),
                    }
                    return stdout, stderr, return_code, elapsed_ms, metadata

        if (time.time() - start) >= timeout_seconds:
            with contextlib.suppress(OSError):
                process.terminate()
            try:
                stdout, stderr = process.communicate(timeout=2)
            except subprocess.TimeoutExpired:
                with contextlib.suppress(OSError):
                    process.kill()
                stdout, stderr = process.communicate()
            raise subprocess.TimeoutExpired(
                command,
                timeout_seconds,
                output=stdout,
                stderr=stderr,
            )
        time.sleep(1.0)


def start_bitloops_task_daemon(
    *,
    binary: str,
    timeout: int,
    env: dict[str, str],
    sandbox: dict[str, Any],
    cwd: str | None = None,
) -> BitloopsTaskDaemonHandle:
    _ensure_sandbox_directories(sandbox)
    port = _allocate_localhost_port()
    stderr_log_path = Path(str(sandbox.get("daemon_stderr_log_path") or "")).expanduser()
    if not stderr_log_path.is_absolute():
        stderr_log_path = (Path.cwd() / stderr_log_path).resolve()
    stderr_log_path.parent.mkdir(parents=True, exist_ok=True)
    stderr_handle = stderr_log_path.open("w", encoding="utf-8")
    command = [
        binary,
        "daemon",
        "start",
        "--no-telemetry",
        "--http",
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
    ]
    try:
        process = subprocess.Popen(
            command,
            text=True,
            stdout=subprocess.DEVNULL,
            stderr=stderr_handle,
            env=env,
            cwd=cwd,
        )
    except Exception:
        stderr_handle.close()
        raise

    try:
        _wait_for_task_daemon_ready(
            timeout=timeout,
            port=port,
            process=process,
            stderr_log_path=stderr_log_path,
        )
    except Exception:
        with contextlib.suppress(OSError):
            process.terminate()
        with contextlib.suppress(subprocess.TimeoutExpired):
            process.wait(timeout=5)
        if process.poll() is None:
            with contextlib.suppress(OSError):
                process.kill()
        stderr_handle.close()
        raise

    stderr_handle.close()
    return BitloopsTaskDaemonHandle(
        process=process,
        port=port,
        stderr_log_path=stderr_log_path,
        binary=binary,
        env=env,
        cwd=cwd,
    )


def _stop_bitloops_task_daemon_via_cli(handle: BitloopsTaskDaemonHandle | None) -> None:
    if handle is None:
        return
    binary = str(getattr(handle, "binary", "")).strip()
    env = getattr(handle, "env", None)
    cwd = getattr(handle, "cwd", None)
    if not binary or not isinstance(env, dict):
        return
    with contextlib.suppress(Exception):
        call_command(
            [binary, "daemon", "stop"],
            BITLOOPS_TASK_DAEMON_STOP_TIMEOUT_SECONDS,
            env=env,
            cwd=cwd,
        )


def stop_bitloops_task_daemon(handle: BitloopsTaskDaemonHandle | None) -> None:
    if handle is None:
        return
    _stop_bitloops_task_daemon_via_cli(handle)
    if handle.process.poll() is not None:
        return
    with contextlib.suppress(OSError):
        handle.process.terminate()
    with contextlib.suppress(subprocess.TimeoutExpired):
        handle.process.wait(timeout=5)
    if handle.process.poll() is None:
        with contextlib.suppress(OSError):
            handle.process.kill()
        with contextlib.suppress(subprocess.TimeoutExpired):
            handle.process.wait(timeout=2)


def _resolve_bitloops_global_lock_path() -> Path:
    raw = os.environ.get(BITLOOPS_GLOBAL_LOCK_ENV_VAR, "").strip()
    if raw:
        path = Path(raw).expanduser()
    else:
        path = Path(tempfile.gettempdir()) / "benchkit-bitloops-global.lock"
    if not path.is_absolute():
        path = (Path.cwd() / path).resolve()
    return path


@contextlib.contextmanager
def _acquire_bitloops_global_lock(
    *,
    lock_path: Path,
    timeout_seconds: int,
) -> Any:
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    wait_started = time.time()
    with lock_path.open("a+", encoding="utf-8") as handle:
        while True:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if (time.time() - wait_started) >= timeout_seconds:
                    raise TimeoutError(
                        f"timed out waiting for Bitloops global lock: {lock_path}"
                    )
                time.sleep(0.1)
        wait_elapsed_ms = int((time.time() - wait_started) * 1000)
        try:
            yield wait_elapsed_ms
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _ensure_bitloops_daemon_started(
    *,
    binary: str,
    timeout: int,
    env: dict[str, str] | None = None,
    cwd: str | None = None,
) -> dict[str, Any]:
    status_command = [binary, "status"]
    status_stdout, status_stderr, status_code, status_elapsed_ms = call_command(
        status_command,
        timeout,
        env=env,
        cwd=cwd,
    )
    daemon_running = status_code == 0 and _bitloops_daemon_is_running(status_stdout)

    daemon_start_attempted = False
    daemon_bootstrap_attempted = False
    daemon_start_mode = "already_running" if daemon_running else "not_running"
    daemon_start_elapsed_ms = 0
    daemon_start_command: list[str] | None = None
    daemon_bootstrap_command: list[str] | None = None

    if not daemon_running:
        daemon_start_attempted = True
        start_command = [binary, "start", "--telemetry=false", "--detached"]
        daemon_start_command = start_command
        start_stdout, start_stderr, start_code, start_elapsed_ms = call_command(
            start_command,
            timeout,
            env=env,
            cwd=cwd,
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
            ) = call_command(
                bootstrap_command,
                timeout,
                env=env,
                cwd=cwd,
            )
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

    return {
        "bitloops_daemon_was_running": daemon_running,
        "bitloops_daemon_start_attempted": daemon_start_attempted,
        "bitloops_daemon_bootstrap_attempted": daemon_bootstrap_attempted,
        "bitloops_daemon_start_mode": daemon_start_mode,
        "bitloops_status_command": status_command,
        "bitloops_start_command": daemon_start_command,
        "bitloops_bootstrap_command": daemon_bootstrap_command,
        "bitloops_status_elapsed_ms": status_elapsed_ms,
        "bitloops_daemon_start_elapsed_ms": daemon_start_elapsed_ms,
    }


def _run_bitloops_configure(
    *,
    binary: str,
    timeout: int,
    config_path: Path | None,
    env: dict[str, str] | None = None,
    cwd: str | None = None,
) -> dict[str, Any]:
    if config_path is None:
        return {
            "bitloops_configure_applied": False,
            "bitloops_configure_command": None,
            "bitloops_configure_file_path": None,
            "bitloops_configure_elapsed_ms": 0,
        }

    command = [binary, "configure", "--file", str(config_path), "--no-start"]
    stdout, stderr, return_code, elapsed_ms = call_command(
        command,
        timeout,
        env=env,
        cwd=cwd,
    )
    if return_code != 0:
        raise RuntimeError(
            "bitloops configure failed: "
            + _serialize_command_failure(
                command=command,
                stdout=stdout,
                stderr=stderr,
                return_code=return_code,
            )
        )
    return {
        "bitloops_configure_applied": True,
        "bitloops_configure_command": command,
        "bitloops_configure_file_path": str(config_path),
        "bitloops_configure_elapsed_ms": elapsed_ms,
        "bitloops_configure_return_code": return_code,
    }


def _bitloops_configure_state_path(config_path: Path) -> Path:
    return config_path.parent / BITLOOPS_CONFIGURE_STATE_FILENAME


def _bitloops_configure_lock_path(config_path: Path) -> Path:
    return config_path.parent / BITLOOPS_CONFIGURE_LOCK_FILENAME


def _hash_bitloops_config_file(config_path: Path) -> str | None:
    try:
        return hashlib.sha256(config_path.read_bytes()).hexdigest()
    except FileNotFoundError:
        return None


def _read_bitloops_configure_state(state_path: Path) -> dict[str, Any] | None:
    if not state_path.exists():
        return None
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return state if isinstance(state, dict) else None


def _write_bitloops_configure_state(
    state_path: Path,
    *,
    config_path: Path,
    config_hash: str,
    command: list[str],
) -> None:
    state_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = state_path.with_name(f"{state_path.name}.tmp.{os.getpid()}")
    payload = {
        "version": 1,
        "config_hash": config_hash,
        "config_path": str(config_path),
        "command": command,
        "configured_at_ms": int(time.time() * 1000),
    }
    temporary_path.write_text(
        json.dumps(payload, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary_path, state_path)


@contextlib.contextmanager
def _acquire_bitloops_configure_lock(
    *,
    lock_path: Path,
    timeout_seconds: int,
) -> Any:
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    wait_started = time.time()
    with lock_path.open("a+", encoding="utf-8") as handle:
        while True:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if (time.time() - wait_started) >= timeout_seconds:
                    raise TimeoutError(
                        f"timed out waiting for Bitloops configure lock: {lock_path}"
                    )
                time.sleep(0.1)
        wait_elapsed_ms = int((time.time() - wait_started) * 1000)
        try:
            yield wait_elapsed_ms
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _run_bitloops_configure_once(
    *,
    binary: str,
    timeout: int,
    config_path: Path | None,
    env: dict[str, str] | None = None,
    cwd: str | None = None,
) -> dict[str, Any]:
    if config_path is None:
        return {
            "bitloops_configure_applied": False,
            "bitloops_configure_command": None,
            "bitloops_configure_file_path": None,
            "bitloops_configure_elapsed_ms": 0,
            "bitloops_configure_config_hash": None,
            "bitloops_configure_state_path": None,
            "bitloops_configure_lock_path": None,
            "bitloops_configure_lock_wait_elapsed_ms": 0,
            "bitloops_configure_skipped_reason": "no_config_file",
            "bitloops_configure_previous_config_hash": None,
            "bitloops_configure_return_code": None,
        }

    command = [binary, "configure", "--file", str(config_path), "--no-start"]
    config_hash = _hash_bitloops_config_file(config_path)
    if config_hash is None:
        metadata = _run_bitloops_configure(
            binary=binary,
            timeout=timeout,
            config_path=config_path,
            env=env,
            cwd=cwd,
        )
        metadata.update(
            {
                "bitloops_configure_config_hash": None,
                "bitloops_configure_state_path": None,
                "bitloops_configure_lock_path": None,
                "bitloops_configure_lock_wait_elapsed_ms": 0,
                "bitloops_configure_skipped_reason": None,
                "bitloops_configure_previous_config_hash": None,
            }
        )
        return metadata

    state_path = _bitloops_configure_state_path(config_path)
    lock_path = _bitloops_configure_lock_path(config_path)
    with _acquire_bitloops_configure_lock(
        lock_path=lock_path,
        timeout_seconds=timeout,
    ) as lock_wait_elapsed_ms:
        state = _read_bitloops_configure_state(state_path)
        previous_config_hash = (
            str(state.get("config_hash", "")).strip()
            if isinstance(state, dict)
            else ""
        )
        if previous_config_hash == config_hash:
            return {
                "bitloops_configure_applied": False,
                "bitloops_configure_command": None,
                "bitloops_configure_file_path": str(config_path),
                "bitloops_configure_elapsed_ms": 0,
                "bitloops_configure_config_hash": config_hash,
                "bitloops_configure_state_path": str(state_path),
                "bitloops_configure_lock_path": str(lock_path),
                "bitloops_configure_lock_wait_elapsed_ms": lock_wait_elapsed_ms,
                "bitloops_configure_skipped_reason": "already_configured",
                "bitloops_configure_previous_config_hash": previous_config_hash,
                "bitloops_configure_return_code": None,
            }
        if previous_config_hash:
            raise RuntimeError(
                "bitloops configure refused: generated daemon config changed for "
                f"existing daemon state at {state_path} "
                f"(previous={previous_config_hash}, current={config_hash}). "
                "Use a new daemon sandbox/root or remove the previous daemon state "
                "before reconfiguring."
            )

        metadata = _run_bitloops_configure(
            binary=binary,
            timeout=timeout,
            config_path=config_path,
            env=env,
            cwd=cwd,
        )
        _write_bitloops_configure_state(
            state_path,
            config_path=config_path,
            config_hash=config_hash,
            command=command,
        )
        metadata.update(
            {
                "bitloops_configure_config_hash": config_hash,
                "bitloops_configure_state_path": str(state_path),
                "bitloops_configure_lock_path": str(lock_path),
                "bitloops_configure_lock_wait_elapsed_ms": lock_wait_elapsed_ms,
                "bitloops_configure_skipped_reason": None,
                "bitloops_configure_previous_config_hash": None,
            }
        )
        return metadata


def _run_bitloops_init(
    *,
    binary: str,
    timeout: int,
    agent_name: str,
    sync: bool,
    ingest: bool,
    embeddings_runtime: str | None = None,
    summaries_runtime: str | None = None,
    summary_embeddings_mode: str | None = None,
    no_embeddings: bool = False,
    no_summaries: bool = False,
    disable_devql_guidance: bool = False,
    env: dict[str, str] | None = None,
    cwd: str | None = None,
) -> dict[str, Any]:
    include_ingest_flag = True
    include_no_summaries_flag = no_summaries
    effective_embeddings_runtime = None if no_embeddings else embeddings_runtime
    effective_summaries_runtime = None if no_summaries else summaries_runtime
    effective_summary_embeddings_mode = (
        None
        if no_summaries
        else (
            summary_embeddings_mode.strip().lower()
            if isinstance(summary_embeddings_mode, str) and summary_embeddings_mode.strip()
            else None
        )
    )
    if effective_summary_embeddings_mode not in {None, "on", "off"}:
        raise ValueError("bitloops summary embeddings mode must be one of: on, off")
    init_fallback_used = False
    init_command: list[str] = []
    init_stdout = ""
    init_stderr = ""
    init_code = 1
    init_elapsed_ms = 0
    init_db_lock_retry_count = 0
    max_db_lock_retries = max(0, int(os.environ.get("BITLOOPS_INIT_DB_LOCK_RETRIES", "3")))
    db_lock_retry_delay_ms = max(
        0,
        int(os.environ.get("BITLOOPS_INIT_DB_LOCK_RETRY_DELAY_MS", "500")),
    )
    runtime_ready_shortcut_metadata: dict[str, Any] | None = None
    init_status_shortcut_metadata: dict[str, Any] | None = None

    while True:
        init_command = [
            binary,
            "init",
            "--agent",
            agent_name,
            f"--sync={'true' if sync else 'false'}",
        ]
        if include_ingest_flag:
            init_command.append(f"--ingest={'true' if ingest else 'false'}")
        if no_embeddings:
            init_command.append("--no-embeddings")
        elif effective_embeddings_runtime:
            init_command.extend(["--embeddings-runtime", effective_embeddings_runtime])
        if include_no_summaries_flag:
            init_command.append("--no-summaries")
        elif effective_summaries_runtime:
            init_command.extend(["--summaries-runtime", effective_summaries_runtime])
        if effective_summary_embeddings_mode is not None:
            init_command.extend(
                ["--summary-embeddings-mode", effective_summary_embeddings_mode]
            )
        if disable_devql_guidance:
            init_command.append("--disable-devql-guidance")

        use_per_task_daemon_shortcut = (
            isinstance(env, dict)
            and str(env.get("BITLOOPS_BENCHKIT_SANDBOX_MODE", "")).strip().lower()
            == "per_task_daemon"
        )
        if use_per_task_daemon_shortcut and sync:
            (
                init_stdout,
                init_stderr,
                init_code,
                current_init_elapsed_ms,
                runtime_ready_shortcut_metadata,
            ) = _run_command_with_runtime_ready_shortcut(
                command=init_command,
                timeout_seconds=timeout,
                env=env,
                cwd=cwd,
            )
        elif use_per_task_daemon_shortcut:
            (
                init_stdout,
                init_stderr,
                init_code,
                current_init_elapsed_ms,
                init_status_shortcut_metadata,
            ) = _run_command_with_init_status_shortcut(
                command=init_command,
                timeout_seconds=timeout,
                env=env,
                cwd=cwd,
            )
        else:
            (
                init_stdout,
                init_stderr,
                init_code,
                current_init_elapsed_ms,
            ) = call_command(
                init_command,
                timeout,
                env=env,
                cwd=cwd,
            )
        init_elapsed_ms += current_init_elapsed_ms
        if init_code == 0:
            break

        fallback_applied = False
        if include_ingest_flag and _bitloops_init_rejects_ingest_flag(init_stdout, init_stderr):
            include_ingest_flag = False
            fallback_applied = True
        if include_no_summaries_flag and _bitloops_init_rejects_no_summaries_flag(
            init_stdout,
            init_stderr,
        ):
            include_no_summaries_flag = False
            _apply_bitloops_repo_semantic_modes(
                summary_mode="off",
                cwd=cwd,
            )
            fallback_applied = True
        if effective_summaries_runtime and _bitloops_init_rejects_summaries_runtime_flag(
            init_stdout,
            init_stderr,
        ):
            effective_summaries_runtime = None
            fallback_applied = True

        if not fallback_applied:
            if (
                _bitloops_init_hit_database_lock(init_stdout, init_stderr)
                and init_db_lock_retry_count < max_db_lock_retries
            ):
                init_db_lock_retry_count += 1
                sleep_seconds = (
                    db_lock_retry_delay_ms * (2 ** (init_db_lock_retry_count - 1))
                ) / 1000.0
                if sleep_seconds > 0:
                    time.sleep(sleep_seconds)
                continue
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

    return {
        "bitloops_init_command": init_command,
        "bitloops_init_fallback_used": init_fallback_used,
        "bitloops_init_elapsed_ms": init_elapsed_ms,
        "bitloops_init_db_lock_retry_count": init_db_lock_retry_count,
        "bitloops_init_db_lock_retry_used": init_db_lock_retry_count > 0,
        "bitloops_disable_devql_guidance": disable_devql_guidance,
        "bitloops_init_runtime_ready_shortcut_used": runtime_ready_shortcut_metadata is not None,
        "bitloops_init_runtime_ready_shortcut": runtime_ready_shortcut_metadata,
        "bitloops_init_status_command": (
            init_status_shortcut_metadata.get("status_command")
            if isinstance(init_status_shortcut_metadata, dict)
            else None
        ),
        "bitloops_init_status_shortcut_used": init_status_shortcut_metadata is not None,
        "bitloops_init_status_shortcut": init_status_shortcut_metadata,
    }


def setup_bitloops_for_workspace(
    *,
    agent_name: str,
    bitloops_bin: str | None = None,
    timeout_seconds: int | None = None,
    sync: bool = True,
    ingest: bool = True,
    embeddings_runtime: str | None = None,
    no_embeddings: bool = False,
    no_summaries: bool = False,
    summary_mode: str | None = None,
    summaries_runtime: str | None = None,
    summary_embeddings_mode: str | None = None,
    disable_devql_guidance: bool = False,
    embedding_mode: str | None = None,
    sandbox: dict[str, Any] | None = None,
    env: dict[str, str] | None = None,
    cwd: str | None = None,
    task_daemon_handle: BitloopsTaskDaemonHandle | None = None,
    return_task_daemon_handle: bool = False,
) -> dict[str, Any]:
    binary = (bitloops_bin or os.environ.get("BITLOOPS_BIN", "bitloops")).strip() or "bitloops"
    timeout = timeout_seconds or int(os.environ.get("BITLOOPS_SETUP_TIMEOUT_SECONDS", "1500"))
    env = build_bitloops_telemetry_optout_environment(env)
    requested_summary_mode = (
        summary_mode.strip().lower() if isinstance(summary_mode, str) and summary_mode.strip() else None
    )
    requested_summaries_runtime = (
        summaries_runtime.strip().lower()
        if isinstance(summaries_runtime, str) and summaries_runtime.strip()
        else None
    )
    requested_summary_embeddings_mode = (
        summary_embeddings_mode.strip().lower()
        if isinstance(summary_embeddings_mode, str) and summary_embeddings_mode.strip()
        else None
    )
    if requested_summary_mode not in {None, "auto", "off", "on"}:
        raise ValueError(
            "bitloops summary mode must be one of: auto, off, on"
        )
    if requested_summaries_runtime not in {None, "local", "platform"}:
        raise ValueError(
            "bitloops summaries runtime must be one of: local, platform"
        )
    if requested_summary_embeddings_mode not in {None, "on", "off"}:
        raise ValueError("bitloops summary embeddings mode must be one of: on, off")
    if no_summaries and requested_summary_mode == "on":
        raise ValueError(
            "Conflicting Bitloops summaries controls: --bitloops-no-summaries "
            "cannot be combined with --bitloops-summary-mode on."
        )
    if no_summaries and requested_summaries_runtime is not None:
        raise ValueError(
            "Conflicting Bitloops summaries controls: --bitloops-no-summaries "
            "cannot be combined with --bitloops-summaries-runtime."
        )
    if no_summaries and requested_summary_embeddings_mode == "on":
        raise ValueError(
            "Conflicting Bitloops summaries controls: --bitloops-no-summaries "
            "cannot be combined with --bitloops-summary-embeddings-mode on."
        )
    if no_summaries:
        effective_summary_mode = "off"
    elif requested_summary_mode is not None:
        effective_summary_mode = requested_summary_mode
    elif (
        requested_summaries_runtime is not None
        or requested_summary_embeddings_mode == "on"
    ):
        # Runtime/mode flags explicitly opt summaries back in.
        effective_summary_mode = "auto"
    else:
        effective_summary_mode = "off"
    effective_no_summaries = effective_summary_mode == "off"
    setup_started = time.time()
    (
        git_detached_head,
        git_checkout_attempted,
        git_branch_checkout_command,
        git_checked_out_branch,
        git_checkout_elapsed_ms,
    ) = _ensure_git_branch_for_bitloops_sync(
        timeout_seconds=timeout,
        env=env,
        cwd=cwd,
    )
    requested_embeddings_runtime = (
        embeddings_runtime.strip() if isinstance(embeddings_runtime, str) and embeddings_runtime.strip() else None
    )
    effective_no_embeddings = no_embeddings or requested_embeddings_runtime is None
    effective_summaries_runtime = requested_summaries_runtime
    if no_summaries:
        effective_summaries_runtime = None
    elif effective_summary_mode != "off" and effective_summaries_runtime is None:
        # Backward-compatible bridge for deprecated --bitloops-summary-mode controls.
        effective_summaries_runtime = requested_embeddings_runtime
    effective_summary_embeddings_mode = (
        "off" if effective_no_summaries else requested_summary_embeddings_mode
    )
    effective_embedding_mode = (
        "off" if effective_no_embeddings else (embedding_mode or "semantic_aware_once")
    )
    sandbox_mode = str((sandbox or {}).get("mode", "")).strip().lower()
    bind_cloud_semantic_profiles = (
        sandbox_mode == "per_task_daemon"
        and requested_embeddings_runtime == "platform"
        and not effective_no_embeddings
    )
    repo_config_path = None
    if isinstance(cwd, str) and cwd.strip():
        repo_config_path = _apply_bitloops_repo_semantic_modes(
            summary_mode=effective_summary_mode,
            embedding_mode=effective_embedding_mode,
            embedding_profile="platform_code" if bind_cloud_semantic_profiles else None,
            summary_generation_profile=(
                "summary_llm"
                if bind_cloud_semantic_profiles
                and not effective_no_summaries
                and effective_summaries_runtime == "platform"
                else None
            ),
            summary_embedding_profile=(
                "platform_code"
                if bind_cloud_semantic_profiles
                and not effective_no_summaries
                and effective_summaries_runtime == "platform"
                else None
            ),
            cwd=cwd,
        )

    daemon_metadata: dict[str, Any]
    init_metadata: dict[str, Any]
    runtime_ready_metadata: dict[str, Any]
    authorization_metadata: dict[str, Any] = {}
    init_platform_token_metadata: dict[str, Any] = {}
    configure_metadata: dict[str, Any] = {
        "bitloops_configure_applied": False,
        "bitloops_configure_command": None,
        "bitloops_configure_file_path": None,
        "bitloops_configure_elapsed_ms": 0,
        "bitloops_configure_config_hash": None,
        "bitloops_configure_state_path": None,
        "bitloops_configure_lock_path": None,
        "bitloops_configure_lock_wait_elapsed_ms": 0,
        "bitloops_configure_skipped_reason": None,
        "bitloops_configure_previous_config_hash": None,
        "bitloops_configure_return_code": None,
    }
    if sandbox_mode == "per_task_daemon":
        sandbox_env = env or build_bitloops_task_environment(sandbox)
        if sandbox_env is None:
            raise RuntimeError("per-task Bitloops sandbox requested without a valid environment")
        handle: BitloopsTaskDaemonHandle | None = None
        created_task_daemon_handle = False
        try:
            sandbox_config_path = _seed_bitloops_cloud_inference_profiles(
                sandbox,
                summary_mode=effective_summary_mode,
                summaries_runtime=effective_summaries_runtime,
                bind_semantic_inference=bind_cloud_semantic_profiles,
            )
            configure_metadata = _run_bitloops_configure_once(
                binary=binary,
                timeout=timeout,
                config_path=sandbox_config_path,
                env=sandbox_env,
                cwd=cwd,
            )
            init_env, init_platform_token_metadata = (
                _bitloops_init_environment_with_fresh_platform_token(
                    binary=binary,
                    env=sandbox_env,
                    cwd=cwd,
                )
            )
            handle = task_daemon_handle or start_bitloops_task_daemon(
                binary=binary,
                timeout=timeout,
                env=sandbox_env,
                sandbox=sandbox,
                cwd=cwd,
            )
            created_task_daemon_handle = task_daemon_handle is None
            init_metadata = _run_bitloops_init(
                binary=binary,
                timeout=timeout,
                agent_name=agent_name,
                sync=sync,
                ingest=ingest,
                embeddings_runtime=requested_embeddings_runtime,
                summaries_runtime=effective_summaries_runtime,
                summary_embeddings_mode=effective_summary_embeddings_mode,
                no_embeddings=effective_no_embeddings,
                no_summaries=effective_no_summaries,
                disable_devql_guidance=disable_devql_guidance,
                env=init_env,
                cwd=cwd,
            )
            runtime_ready_metadata = _wait_for_bitloops_runtime_ready(
                timeout_seconds=timeout,
                env=sandbox_env,
                cwd=cwd,
                required=sync,
            )
            authorization_metadata = _bitloops_authorization_metadata(
                _resolve_bitloops_runtime_db_path(sandbox_env),
                env=sandbox_env,
                binary=binary,
                cwd=cwd,
            )
        except Exception:
            if created_task_daemon_handle:
                stop_bitloops_task_daemon(handle)
            raise
        assert handle is not None
        daemon_metadata = {
            "bitloops_daemon_was_running": False,
            "bitloops_daemon_start_attempted": True,
            "bitloops_daemon_bootstrap_attempted": True,
            "bitloops_daemon_start_mode": "per_task_foreground",
            "bitloops_status_command": [binary, "status"],
            "bitloops_status_elapsed_ms": 0,
            "bitloops_start_command": [
                binary,
                "daemon",
                "start",
                "--no-telemetry",
                "--http",
                "--host",
                "127.0.0.1",
                "--port",
                str(handle.port),
            ],
            "bitloops_bootstrap_command": None,
            "bitloops_daemon_start_elapsed_ms": 0,
            "bitloops_global_lock_enabled": False,
            "bitloops_global_lock_path": None,
            "bitloops_global_lock_acquired": False,
            "bitloops_global_lock_wait_elapsed_ms": 0,
            "bitloops_setup_serialized": False,
            "bitloops_task_daemon_enabled": True,
            "bitloops_task_daemon_port": handle.port,
            "bitloops_task_daemon_pid": handle.process.pid,
            "bitloops_task_daemon_stderr_log_path": str(handle.stderr_log_path),
            "bitloops_task_sandbox_mode": "per_task_daemon",
            "bitloops_task_sandbox_root": sandbox.get("sandbox_root"),
            "bitloops_task_home_root": sandbox.get("home_root"),
            "bitloops_task_xdg_config_home": sandbox.get("xdg_config_home"),
            "bitloops_task_xdg_state_home": sandbox.get("xdg_state_home"),
            "bitloops_task_xdg_cache_home": sandbox.get("xdg_cache_home"),
            "bitloops_task_xdg_data_home": sandbox.get("xdg_data_home"),
            "bitloops_task_daemon_cloud_config_path": (
                str(sandbox_config_path) if sandbox_config_path is not None else None
            ),
        }
        if return_task_daemon_handle and created_task_daemon_handle:
            daemon_metadata["_bitloops_task_daemon_handle"] = handle
        if not authorization_metadata["bitloops_cloud_authorized"]:
            if created_task_daemon_handle:
                stop_bitloops_task_daemon(handle)
            raise RuntimeError(
                "Bitloops Cloud authorisation was not recorded in the per-task "
                "runtime after `bitloops init`. Complete the Bitloops login prompt "
                "during setup, then retry the benchmark instance."
            )
    else:
        global_lock_enabled = not env_flag(BITLOOPS_DISABLE_GLOBAL_LOCK_ENV_VAR, default=False)
        global_lock_path = _resolve_bitloops_global_lock_path() if global_lock_enabled else None
        global_lock_wait_elapsed_ms = 0
        global_lock_acquired = False
        if global_lock_enabled and global_lock_path is not None:
            with _acquire_bitloops_global_lock(
                lock_path=global_lock_path,
                timeout_seconds=timeout,
            ) as wait_elapsed_ms:
                global_lock_wait_elapsed_ms = wait_elapsed_ms
                global_lock_acquired = True
                daemon_metadata = _ensure_bitloops_daemon_started(
                    binary=binary,
                    timeout=timeout,
                    env=env,
                    cwd=cwd,
                )
                init_metadata = _run_bitloops_init(
                    binary=binary,
                    timeout=timeout,
                    agent_name=agent_name,
                    sync=sync,
                    ingest=ingest,
                    embeddings_runtime=requested_embeddings_runtime,
                    summaries_runtime=effective_summaries_runtime,
                    summary_embeddings_mode=effective_summary_embeddings_mode,
                    no_embeddings=effective_no_embeddings,
                    no_summaries=effective_no_summaries,
                    disable_devql_guidance=disable_devql_guidance,
                    env=env,
                    cwd=cwd,
                )
                runtime_ready_metadata = _wait_for_bitloops_runtime_ready(
                    timeout_seconds=timeout,
                    env=env,
                    cwd=cwd,
                    required=False,
                )
        else:
            daemon_metadata = _ensure_bitloops_daemon_started(
                binary=binary,
                timeout=timeout,
                env=env,
                cwd=cwd,
            )
            init_metadata = _run_bitloops_init(
                binary=binary,
                timeout=timeout,
                agent_name=agent_name,
                sync=sync,
                ingest=ingest,
                embeddings_runtime=requested_embeddings_runtime,
                summaries_runtime=effective_summaries_runtime,
                summary_embeddings_mode=effective_summary_embeddings_mode,
                no_embeddings=effective_no_embeddings,
                no_summaries=effective_no_summaries,
                disable_devql_guidance=disable_devql_guidance,
                env=env,
                cwd=cwd,
            )
            runtime_ready_metadata = _wait_for_bitloops_runtime_ready(
                timeout_seconds=timeout,
                env=env,
                cwd=cwd,
                required=False,
            )
        daemon_metadata.setdefault("bitloops_global_lock_enabled", global_lock_enabled)
        daemon_metadata.setdefault(
            "bitloops_global_lock_path",
            str(global_lock_path) if global_lock_path else None,
        )
        daemon_metadata.setdefault("bitloops_global_lock_acquired", global_lock_acquired)
        daemon_metadata.setdefault(
            "bitloops_global_lock_wait_elapsed_ms",
            global_lock_wait_elapsed_ms,
        )
        daemon_metadata.setdefault("bitloops_setup_serialized", global_lock_enabled)
        daemon_metadata.setdefault("bitloops_task_daemon_enabled", False)
        daemon_metadata.setdefault("bitloops_task_daemon_port", None)
        daemon_metadata.setdefault("bitloops_task_daemon_pid", None)
        daemon_metadata.setdefault("bitloops_task_daemon_stderr_log_path", None)
        daemon_metadata.setdefault("bitloops_task_sandbox_mode", "shared_daemon")
        daemon_metadata.setdefault("bitloops_task_sandbox_root", None)
        daemon_metadata.setdefault("bitloops_task_home_root", None)
        daemon_metadata.setdefault("bitloops_task_xdg_config_home", None)
        daemon_metadata.setdefault("bitloops_task_xdg_state_home", None)
        daemon_metadata.setdefault("bitloops_task_xdg_cache_home", None)
        daemon_metadata.setdefault("bitloops_task_xdg_data_home", None)

    return {
        "bitloops_enabled": True,
        "bitloops_agent": agent_name,
        "bitloops_sync": sync,
        "bitloops_ingest": ingest,
        "bitloops_embeddings_runtime": requested_embeddings_runtime,
        "bitloops_no_embeddings": effective_no_embeddings,
        "bitloops_no_summaries": effective_no_summaries,
        "bitloops_summary_mode": effective_summary_mode,
        "bitloops_summaries_runtime": effective_summaries_runtime,
        "bitloops_summary_embeddings_mode": effective_summary_embeddings_mode,
        "bitloops_disable_devql_guidance": disable_devql_guidance,
        "bitloops_embedding_mode": effective_embedding_mode,
        "bitloops_git_detached_head": git_detached_head,
        "bitloops_git_checkout_attempted": git_checkout_attempted,
        "bitloops_git_checkout_command": git_branch_checkout_command,
        "bitloops_git_checked_out_branch": git_checked_out_branch,
        "bitloops_git_checkout_elapsed_ms": git_checkout_elapsed_ms,
        "bitloops_repo_config_path": str(repo_config_path) if repo_config_path else None,
        "bitloops_setup_elapsed_ms": int((time.time() - setup_started) * 1000),
        **configure_metadata,
        **daemon_metadata,
        **init_metadata,
        **init_platform_token_metadata,
        **runtime_ready_metadata,
        **authorization_metadata,
    }


def _apply_bitloops_repo_semantic_modes(
    *,
    summary_mode: str | None = None,
    embedding_mode: str | None = None,
    embedding_profile: str | None = None,
    summary_generation_profile: str | None = None,
    summary_embedding_profile: str | None = None,
    cwd: str | None = None,
) -> Path | None:
    semantic_updates = {
        key: value
        for key, value in {
            "summary_mode": summary_mode,
            "embedding_mode": embedding_mode,
        }.items()
        if isinstance(value, str) and value.strip()
    }
    inference_updates = {
        key: value
        for key, value in {
            "code_embeddings": embedding_profile,
            "summary_generation": summary_generation_profile,
            "summary_embeddings": summary_embedding_profile,
        }.items()
        if isinstance(value, str) and value.strip()
    }
    if not semantic_updates and not inference_updates:
        return None

    workspace_root = Path(cwd).expanduser() if isinstance(cwd, str) and cwd.strip() else Path.cwd()
    config_path = workspace_root / "config.toml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    content = config_path.read_text(encoding="utf-8") if config_path.exists() else ""
    if semantic_updates:
        content = _upsert_toml_table_values(content, "semantic_clones", semantic_updates)
    if inference_updates:
        content = _upsert_toml_table_values(
            content,
            "semantic_clones.inference",
            inference_updates,
        )
    config_path.write_text(content, encoding="utf-8")
    return config_path


def _upsert_toml_table_values(
    content: str,
    table_name: str,
    values: dict[str, str],
) -> str:
    table_pattern = re.compile(
        rf"(?ms)^\[{re.escape(table_name)}\]\n(?P<body>.*?)(?=^\[|\Z)"
    )
    match = table_pattern.search(content)
    if match:
        body = match.group("body")
        for key, value in values.items():
            key_pattern = re.compile(rf"(?m)^{re.escape(key)}\s*=.*$")
            line = f'{key} = "{value}"'
            if key_pattern.search(body):
                body = key_pattern.sub(line, body, count=1)
            else:
                if body and not body.endswith("\n"):
                    body += "\n"
                body += f"{line}\n"
        return content[: match.start("body")] + body + content[match.end("body") :]

    if content and not content.endswith("\n"):
        content += "\n"
    if content:
        content += "\n"
    content += f"[{table_name}]\n"
    for key, value in values.items():
        content += f'{key} = "{value}"\n'
    return content


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


def _bitloops_init_rejects_no_summaries_flag(stdout: str, stderr: str) -> bool:
    text = "\n".join((stdout, stderr)).strip().lower()
    return "unexpected argument '--no-summaries'" in text


def _bitloops_init_rejects_summaries_runtime_flag(stdout: str, stderr: str) -> bool:
    text = "\n".join((stdout, stderr)).strip().lower()
    return "unexpected argument '--summaries-runtime'" in text


def _bitloops_init_hit_database_lock(stdout: str, stderr: str) -> bool:
    text = "\n".join((stdout, stderr)).strip().lower()
    return (
        "database is locked" in text
        or "error code 5" in text
        or "sqlite_busy" in text
    )


def _ensure_git_branch_for_bitloops_sync(
    *,
    timeout_seconds: int,
    env: dict[str, str] | None = None,
    cwd: str | None = None,
) -> tuple[bool, bool, list[str] | None, str | None, int]:
    detached = _git_head_is_detached(timeout_seconds, env=env, cwd=cwd)
    if not detached:
        return False, False, None, None, 0

    elapsed_ms_total = 0
    short_sha = "detached"
    stdout, _stderr, code, elapsed_ms = call_command(
        ["git", "rev-parse", "--short=12", "HEAD"],
        timeout_seconds,
        env=env,
        cwd=cwd,
    )
    elapsed_ms_total += elapsed_ms
    if code == 0 and stdout.strip():
        short_sha = stdout.strip()

    branch_name = f"benchkit-bitloops-{short_sha}"
    create_command = ["git", "switch", "-c", branch_name]
    create_stdout, create_stderr, create_code, create_elapsed_ms = call_command(
        create_command,
        timeout_seconds,
        env=env,
        cwd=cwd,
    )
    elapsed_ms_total += create_elapsed_ms
    if create_code == 0:
        return True, True, create_command, branch_name, elapsed_ms_total

    switch_command = ["git", "switch", branch_name]
    switch_stdout, switch_stderr, switch_code, switch_elapsed_ms = call_command(
        switch_command,
        timeout_seconds,
        env=env,
        cwd=cwd,
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


def _git_head_is_detached(
    timeout_seconds: int,
    *,
    env: dict[str, str] | None = None,
    cwd: str | None = None,
) -> bool:
    _stdout, _stderr, code, _elapsed_ms = call_command(
        ["git", "symbolic-ref", "--quiet", "--short", "HEAD"],
        timeout_seconds,
        env=env,
        cwd=cwd,
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


def _extract_terminal_agent_message(payload: Any) -> str:
    events = payload if isinstance(payload, list) else [payload]
    for event in reversed(events):
        if not isinstance(event, dict):
            continue
        event_type = _normalize_event_type(event.get("type"))
        if event_type == "text":
            text = _extract_opencode_text_event_text(event)
            if text:
                return text
        if event_type in {"item_completed", "item_started", "item"}:
            item = event.get("item")
            if not isinstance(item, dict):
                continue
            item_type = _normalize_event_type(item.get("type"))
            if item_type != "agent_message":
                continue
            text = first_non_empty_text(item.get("text"))
            if text:
                return text
        if event_type == "message_updated":
            text = _extract_message_updated_text(event)
            if text:
                return text
        if event_type == "result":
            text = first_non_empty_text(event.get("result"))
            if text:
                return text
    for event in reversed(events):
        if not isinstance(event, dict):
            continue
        if _normalize_event_type(event.get("type")) != "message_part_updated":
            continue
        text = _extract_message_part_text(event)
        if text:
            return text
    return ""


def _extract_message_updated_text(event: dict[str, Any]) -> str:
    properties = event.get("properties")
    if not isinstance(properties, dict):
        return ""
    message = properties.get("info")
    if not isinstance(message, dict):
        return ""
    message_info = message.get("info")
    if not isinstance(message_info, dict):
        return ""
    role = str(message_info.get("role") or "").strip().lower()
    if role != "assistant":
        return ""
    return _extract_message_parts_text(message.get("parts"))


def _extract_message_part_text(event: dict[str, Any]) -> str:
    properties = event.get("properties")
    if not isinstance(properties, dict):
        return ""
    part = properties.get("part")
    if not isinstance(part, dict):
        return ""
    if _normalize_event_type(part.get("type")) != "text":
        return ""
    return first_non_empty_text(part.get("text"))


def _extract_opencode_text_event_text(event: dict[str, Any]) -> str:
    part = event.get("part")
    if not isinstance(part, dict):
        return ""
    if _normalize_event_type(part.get("type")) != "text":
        return ""
    return first_non_empty_text(part.get("text"))


def _extract_message_parts_text(parts: Any) -> str:
    if not isinstance(parts, list):
        return ""
    for part in reversed(parts):
        if not isinstance(part, dict):
            continue
        if _normalize_event_type(part.get("type")) != "text":
            continue
        text = first_non_empty_text(part.get("text"))
        if text:
            return text
    return ""


def parse_agent_output(raw_stdout: str, parsed_payload: Any | None = None) -> str:
    text = raw_stdout.strip()
    if not text:
        return ""
    parsed = parsed_payload if parsed_payload is not None else _try_parse_json(text)
    if parsed is not None:
        terminal_text = _extract_terminal_agent_message(parsed)
        text = terminal_text or first_non_empty_text(parsed) or ""
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


def _extract_opencode_step_finish_usage_metrics(payload: Any) -> dict[str, float | int | str]:
    events = payload if isinstance(payload, list) else [payload]
    totals: dict[str, int | float] = {
        "token_input": 0,
        "token_output": 0,
        "reasoning_output_tokens": 0,
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 0,
        "total_tokens": 0,
        "estimated_cost": 0.0,
    }
    seen_token_block = False

    for event in events:
        if not isinstance(event, dict):
            continue
        if event.get("type") != "step_finish":
            continue
        part = event.get("part")
        if not isinstance(part, dict):
            continue
        tokens = part.get("tokens")
        if not isinstance(tokens, dict):
            continue

        seen_token_block = True
        cache = tokens.get("cache")
        if not isinstance(cache, dict):
            cache = {}

        input_tokens = _coerce_number(tokens.get("input")) or 0
        output_tokens = _coerce_number(tokens.get("output")) or 0
        reasoning_tokens = _coerce_number(tokens.get("reasoning")) or 0
        cache_write_tokens = _coerce_number(cache.get("write")) or 0
        cache_read_tokens = _coerce_number(cache.get("read")) or 0
        event_total = _coerce_number(tokens.get("total"))
        if event_total is None:
            event_total = (
                float(input_tokens)
                + float(cache_write_tokens)
                + float(cache_read_tokens)
                + float(output_tokens)
                + float(reasoning_tokens)
            )

        totals["token_input"] += input_tokens
        totals["token_output"] += output_tokens
        totals["reasoning_output_tokens"] += reasoning_tokens
        totals["cache_creation_input_tokens"] += cache_write_tokens
        totals["cache_read_input_tokens"] += cache_read_tokens
        totals["total_tokens"] += event_total
        totals["estimated_cost"] += _coerce_number(part.get("cost")) or 0.0

    if not seen_token_block:
        return {}

    metrics: dict[str, float | int | str] = {
        "token_metrics_source": "opencode_step_finish_sum",
    }
    for key, value in totals.items():
        if isinstance(value, float) and value.is_integer() and key != "estimated_cost":
            metrics[key] = int(value)
        else:
            metrics[key] = value
    return metrics


def _infer_token_usage_semantics(
    payload: Any,
    *,
    has_opencode_step_finish_metrics: bool,
) -> str:
    if has_opencode_step_finish_metrics:
        return "opencode_step_finish"

    events = payload if isinstance(payload, list) else [payload]
    if isinstance(payload, dict) and any(
        key in payload for key in ("prompt_eval_count", "promptEvalCount", "eval_count", "evalCount")
    ):
        return "ollama_response"
    for event in events:
        if not isinstance(event, dict):
            continue
        event_type = str(event.get("type") or "").strip().lower()
        if event_type == "turn.completed":
            return "codex_turn_completed"
        if event_type == "message.updated":
            properties = event.get("properties")
            if isinstance(properties, dict):
                info = properties.get("info")
                if isinstance(info, dict):
                    nested_info = info.get("info")
                    if isinstance(nested_info, dict) and isinstance(nested_info.get("tokens"), dict):
                        return "opencode_message_updated"
        if event_type == "result":
            usage = event.get("usage")
            if not isinstance(usage, dict):
                continue
            if any(
                key in usage
                for key in (
                    "input_tokens",
                    "output_tokens",
                    "cache_creation_input_tokens",
                    "cache_read_input_tokens",
                )
            ):
                return "claude_result_usage"
            if any(key in usage for key in ("inputTokens", "outputTokens")):
                return "cursor_result_usage"
        if event_type in {"done", "response"}:
            if any(key in event for key in ("prompt_eval_count", "eval_count")):
                return "ollama_response"

    return "generic"


def _coerce_token_int(value: Any) -> int | None:
    number = _coerce_number(value)
    if number is None:
        return None
    return int(float(number))


def _sum_token_values(*values: Any) -> int | None:
    present = [_coerce_token_int(value) for value in values]
    filtered = [value for value in present if value is not None]
    if not filtered:
        return None
    return int(sum(filtered))


def _apply_canonical_token_metrics(metrics: dict[str, float | int | str]) -> None:
    semantics = str(metrics.get("token_usage_semantics") or "").strip().lower() or "generic"

    token_input = _coerce_token_int(metrics.get("token_input"))
    token_output = _coerce_token_int(metrics.get("token_output"))
    token_input_uncached = _coerce_token_int(metrics.get("token_input_uncached"))
    cached_input_tokens = _coerce_token_int(metrics.get("cached_input_tokens"))
    cache_read_input_tokens = _coerce_token_int(metrics.get("cache_read_input_tokens"))
    cache_creation_input_tokens = _coerce_token_int(metrics.get("cache_creation_input_tokens"))

    if semantics == "codex_turn_completed":
        if token_input_uncached is None and token_input is not None and cached_input_tokens is not None:
            token_input_uncached = max(0, int(token_input - cached_input_tokens))
        input_tokens = token_input_uncached if token_input_uncached is not None else token_input
        if cache_read_input_tokens is None:
            if cached_input_tokens is not None:
                cache_read_input_tokens = cached_input_tokens
            elif token_input is not None:
                cache_read_input_tokens = 0
        if cache_creation_input_tokens is None and (
            token_input is not None or token_output is not None
        ):
            cache_creation_input_tokens = 0
    else:
        input_tokens = token_input
        if cache_read_input_tokens is None and (token_input is not None or token_output is not None):
            cache_read_input_tokens = 0
        if cache_creation_input_tokens is None and (
            token_input is not None or token_output is not None
        ):
            cache_creation_input_tokens = 0

    output_tokens = token_output
    total_input_processed_tokens = _sum_token_values(
        input_tokens,
        cache_read_input_tokens,
        cache_creation_input_tokens,
    )
    total_processed_tokens = _sum_token_values(
        total_input_processed_tokens,
        output_tokens,
    )

    if input_tokens is not None:
        metrics["input_tokens"] = input_tokens
    if output_tokens is not None:
        metrics["output_tokens"] = output_tokens
    if cache_read_input_tokens is not None:
        metrics["cache_read_input_tokens"] = cache_read_input_tokens
    if cache_creation_input_tokens is not None:
        metrics["cache_creation_input_tokens"] = cache_creation_input_tokens
    if total_input_processed_tokens is not None:
        metrics["total_input_processed_tokens"] = total_input_processed_tokens
    if total_processed_tokens is not None:
        metrics["total_processed_tokens"] = total_processed_tokens


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

    opencode_step_finish_metrics = _extract_opencode_step_finish_usage_metrics(payload)
    token_usage_semantics = _infer_token_usage_semantics(
        payload,
        has_opencode_step_finish_metrics=bool(opencode_step_finish_metrics),
    )

    token_input_paths = [
        ("usage", "input_tokens"),
        ("usage", "inputTokens"),
        ("usage", "prompt_tokens"),
        ("usage", "promptTokens"),
        ("usage", "prompt_eval_count"),
        ("usage", "promptEvalCount"),
        ("usage", "tokensIn",),
        ("properties", "info", "info", "tokens", "input"),
        ("info", "info", "tokens", "input"),
        ("tokens", "input"),
        ("prompt_eval_count",),
        ("promptEvalCount",),
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
        ("usage", "eval_count"),
        ("usage", "evalCount"),
        ("usage", "tokensOut",),
        ("properties", "info", "info", "tokens", "output"),
        ("info", "info", "tokens", "output"),
        ("tokens", "output"),
        ("eval_count",),
        ("evalCount",),
        ("output_tokens",),
        ("outputTokens",),
        ("modelUsage", "*", "outputTokens"),
        ("model_usage", "*", "output_tokens"),
    ]
    reasoning_output_paths = [
        ("usage", "reasoning_output_tokens"),
        ("usage", "reasoningOutputTokens"),
        ("reasoning_output_tokens",),
        ("reasoningOutputTokens",),
        ("usage", "output_tokens_details", "reasoning_tokens"),
        ("usage", "outputTokensDetails", "reasoningTokens"),
        ("output_tokens_details", "reasoning_tokens"),
        ("outputTokensDetails", "reasoningTokens"),
    ]
    total_tokens_paths = [
        ("usage", "total_tokens"),
        ("usage", "totalTokens"),
        ("total_tokens",),
        ("totalTokens",),
        ("modelUsage", "*", "totalTokens"),
        ("model_usage", "*", "total_tokens"),
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
        ("properties", "info", "info", "cost"),
        ("info", "info", "cost"),
        ("cost",),
        ("modelUsage", "*", "costUSD"),
        ("model_usage", "*", "cost_usd"),
    ]
    cache_creation_input_paths = [
        ("usage", "cache_creation_input_tokens"),
        ("usage", "cacheCreationInputTokens"),
        ("properties", "info", "info", "tokens", "cache", "write"),
        ("info", "info", "tokens", "cache", "write"),
        ("cache_creation_input_tokens",),
        ("cacheCreationInputTokens",),
    ]
    cached_input_paths = [
        ("usage", "cached_input_tokens"),
        ("usage", "cachedInputTokens"),
        ("properties", "info", "info", "tokens", "cache", "read"),
        ("info", "info", "tokens", "cache", "read"),
        ("cached_input_tokens",),
        ("cachedInputTokens",),
        ("usage", "input_tokens_details", "cached_tokens"),
        ("usage", "inputTokensDetails", "cachedTokens"),
        ("input_tokens_details", "cached_tokens"),
        ("inputTokensDetails", "cachedTokens"),
    ]
    cached_output_paths = [
        ("usage", "cached_output_tokens"),
        ("usage", "cachedOutputTokens"),
        ("cached_output_tokens",),
        ("cachedOutputTokens",),
        ("usage", "output_tokens_details", "cached_tokens"),
        ("usage", "outputTokensDetails", "cachedTokens"),
        ("output_tokens_details", "cached_tokens"),
        ("outputTokensDetails", "cachedTokens"),
    ]
    cache_read_input_paths = [
        ("usage", "cache_read_input_tokens"),
        ("usage", "cacheReadInputTokens"),
        ("properties", "info", "info", "tokens", "cache", "read"),
        ("info", "info", "tokens", "cache", "read"),
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

    if opencode_step_finish_metrics:
        token_input = _coerce_number(opencode_step_finish_metrics.get("token_input"))
        token_output = _coerce_number(opencode_step_finish_metrics.get("token_output"))
        token_input_source = "opencode_step_finish_sum"
        token_output_source = "opencode_step_finish_sum"
        reasoning_output_tokens = _coerce_number(
            opencode_step_finish_metrics.get("reasoning_output_tokens")
        )
        total_tokens = _coerce_number(opencode_step_finish_metrics.get("total_tokens"))
        estimated_cost = _coerce_number(opencode_step_finish_metrics.get("estimated_cost"))
        cached_input_tokens = None
        cached_output_tokens = None
        cache_creation_input_tokens = _coerce_number(
            opencode_step_finish_metrics.get("cache_creation_input_tokens")
        )
        cache_read_input_tokens = _coerce_number(
            opencode_step_finish_metrics.get("cache_read_input_tokens")
        )
        cache_creation_ephemeral_5m_input_tokens = None
        cache_creation_ephemeral_1h_input_tokens = None
    else:
        token_input, token_input_source = _extract_number_with_source(payload, token_input_paths)
        token_output, token_output_source = _extract_number_with_source(payload, token_output_paths)
        reasoning_output_tokens, _ = _extract_number_with_source(payload, reasoning_output_paths)
        total_tokens, _ = _extract_number_with_source(payload, total_tokens_paths)
        estimated_cost, _ = _extract_number_with_source(payload, estimated_cost_paths)
        cached_input_tokens, _ = _extract_number_with_source(payload, cached_input_paths)
        cached_output_tokens, _ = _extract_number_with_source(payload, cached_output_paths)
        cache_creation_input_tokens, _ = _extract_number_with_source(payload, cache_creation_input_paths)
        cache_read_input_tokens, _ = _extract_number_with_source(payload, cache_read_input_paths)
        cache_creation_ephemeral_5m_input_tokens, _ = _extract_number_with_source(
            payload,
            cache_write_5m_paths,
        )
        cache_creation_ephemeral_1h_input_tokens, _ = _extract_number_with_source(
            payload,
            cache_write_1h_paths,
        )
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
    if reasoning_output_tokens is not None:
        metrics["reasoning_output_tokens"] = reasoning_output_tokens
    if total_tokens is not None:
        metrics["total_tokens"] = total_tokens
    if estimated_cost is not None:
        metrics["estimated_cost"] = estimated_cost
    if cached_input_tokens is not None:
        metrics["cached_input_tokens"] = cached_input_tokens
    if cached_output_tokens is not None:
        metrics["cached_output_tokens"] = cached_output_tokens
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
    if token_input is not None and cached_input_tokens is not None:
        metrics["token_input_uncached"] = max(
            0,
            int(float(token_input) - float(cached_input_tokens)),
        )
    if token_output is not None and cached_output_tokens is not None:
        metrics["token_output_uncached"] = max(
            0,
            int(float(token_output) - float(cached_output_tokens)),
        )
    if total_tokens is None and token_input is not None and token_output is not None:
        metrics["total_tokens"] = int(float(token_input) + float(token_output))
    metrics["token_usage_semantics"] = token_usage_semantics
    _apply_canonical_token_metrics(metrics)

    candidate_keys = {
        "input_tokens",
        "inputTokens",
        "output_tokens",
        "outputTokens",
        "reasoning_output_tokens",
        "reasoningOutputTokens",
        "total_tokens",
        "totalTokens",
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
        "cached_input_tokens",
        "cachedInputTokens",
        "cached_output_tokens",
        "cachedOutputTokens",
        "cached_tokens",
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
                "reasoning_output_tokens": _collect_metric_sources(
                    payload,
                    paths=[
                        ("usage", "reasoning_output_tokens"),
                        ("usage", "reasoningOutputTokens"),
                        ("usage", "output_tokens_details", "reasoning_tokens"),
                        ("usage", "outputTokensDetails", "reasoningTokens"),
                        ("reasoning_output_tokens",),
                        ("reasoningOutputTokens",),
                    ],
                ),
                "total_tokens": _collect_metric_sources(
                    payload,
                    paths=[
                        ("usage", "total_tokens"),
                        ("usage", "totalTokens"),
                        ("modelUsage", "*", "totalTokens"),
                        ("model_usage", "*", "total_tokens"),
                        ("total_tokens",),
                        ("totalTokens",),
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
                "cached_input_tokens": _collect_metric_sources(
                    payload,
                    paths=[
                        ("usage", "cached_input_tokens"),
                        ("usage", "cachedInputTokens"),
                        ("usage", "input_tokens_details", "cached_tokens"),
                        ("usage", "inputTokensDetails", "cachedTokens"),
                        ("cached_input_tokens",),
                        ("cachedInputTokens",),
                    ],
                ),
            },
            "token_metrics_source": token_metrics_source,
            "token_usage_semantics": token_usage_semantics,
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

    for source in metric_sources:
        if not isinstance(source, dict):
            continue
        token_usage_semantics = source.get("token_usage_semantics")
        if isinstance(token_usage_semantics, str) and token_usage_semantics.strip():
            merged["token_usage_semantics"] = token_usage_semantics.strip()
            break

    _apply_canonical_token_metrics(merged)

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
                        "reasoning_output_tokens",
                        "total_tokens",
                        "estimated_cost",
                        "cache_creation_input_tokens",
                        "cache_read_input_tokens",
                        "cache_creation_ephemeral_5m_input_tokens",
                        "cache_creation_ephemeral_1h_input_tokens",
                        "input_tokens",
                        "output_tokens",
                        "total_input_processed_tokens",
                        "total_processed_tokens",
                        "tool_calls",
                        "hook_metrics_path",
                        "token_metrics_source",
                        "token_usage_semantics",
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

    tagged_patch = _extract_from_patch_tags(text)
    if tagged_patch:
        text = tagged_patch

    diff_index = text.find("diff --git ")
    if diff_index >= 0:
        return _ensure_trailing_newline(
            _normalize_unified_diff(text[diff_index:].strip())
        ), "diff_header"

    header_match = re.search(r"(?m)^---\s.+\n\+\+\+\s.+$", text)
    if header_match:
        start = header_match.start()
        return _ensure_trailing_newline(
            _normalize_unified_diff(text[start:].strip())
        ), "unified_header"

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


def _extract_from_patch_tags(text: str) -> str:
    blocks = re.findall(r"<patch>\s*(.*?)\s*</patch>", text, flags=re.DOTALL | re.IGNORECASE)
    if not blocks:
        return ""
    for block in blocks:
        stripped = block.strip()
        if "diff --git " in stripped or re.search(r"(?m)^---\s.+\n\+\+\+\s.+$", stripped):
            return stripped
    return blocks[0].strip()


def _normalize_unified_diff(text: str) -> str:
    normalized_lines: list[str] = []
    in_hunk = False
    for line in text.splitlines():
        stripped_line = line.strip().lower()
        if stripped_line in {"<patch>", "</patch>"}:
            continue
        if line.startswith("diff --git "):
            in_hunk = False
            normalized_lines.append(line)
            continue
        if line.startswith("--- ") or line.startswith("+++ "):
            in_hunk = False
            normalized_lines.append(line)
            continue
        if line.startswith("@@"):
            in_hunk = True
            normalized_lines.append(line)
            continue
        if in_hunk:
            if line == "":
                normalized_lines.append(" ")
                continue
            if line.startswith((" ", "+", "-", "\\")):
                normalized_lines.append(line)
                continue
            normalized_lines.append(f" {line}")
            continue
        normalized_lines.append(line)
    repaired_lines: list[str] = []
    hunk_header_pattern = re.compile(
        r"^@@ -(?P<old_start>\d+)(?:,(?P<old_count>\d+))? "
        r"\+(?P<new_start>\d+)(?:,(?P<new_count>\d+))? @@(?P<suffix>.*)$"
    )

    index = 0
    while index < len(normalized_lines):
        line = normalized_lines[index]
        match = hunk_header_pattern.match(line)
        if not match:
            repaired_lines.append(line)
            index += 1
            continue

        old_start = int(match.group("old_start"))
        new_start = int(match.group("new_start"))
        suffix = match.group("suffix") or ""
        hunk_lines: list[str] = []
        old_count = 0
        new_count = 0
        index += 1
        while index < len(normalized_lines):
            candidate = normalized_lines[index]
            if hunk_header_pattern.match(candidate) or candidate.startswith(
                ("diff --git ", "--- ", "+++ ")
            ):
                break
            hunk_lines.append(candidate)
            if candidate.startswith("\\"):
                index += 1
                continue
            if candidate.startswith("+"):
                new_count += 1
            elif candidate.startswith("-"):
                old_count += 1
            else:
                old_count += 1
                new_count += 1
            index += 1

        repaired_lines.append(
            f"@@ -{old_start},{old_count} +{new_start},{new_count} @@{suffix}"
        )
        repaired_lines.extend(hunk_lines)
    return "\n".join(repaired_lines).strip()


def _ensure_trailing_newline(text: str) -> str:
    return f"{text}\n" if text and not text.endswith("\n") else text


_MINIMAL_PROMPT_INTRO = "Investigate and fix the following issue by editing files directly in the workspace."


def _payload_run_condition(payload: dict[str, Any]) -> str:
    run = payload.get("run", {})
    if not isinstance(run, dict):
        return ""
    return str(run.get("condition", "")).strip().lower()


def _resolve_prompt_protocol(payload: dict[str, Any]) -> str:
    if _payload_run_condition(payload) == "with_bitloops":
        return "minimal_bitloops_devql"
    return "minimal"


def prompt_template_metadata(payload: dict[str, Any]) -> dict[str, Any]:
    protocol = _resolve_prompt_protocol(payload)
    version = "minimal_bitloops_devql_v1" if protocol == "minimal_bitloops_devql" else "minimal_v3"
    hash_input = f"{version}|protocol={protocol}"
    return {
        "prompt_protocol": protocol,
        "prompt_template_version": version,
        "prompt_template_hash": hashlib.sha256(hash_input.encode("utf-8")).hexdigest()[:16],
    }


_BITLOOPS_DEVQL_PROMPT = """Before using normal repository exploration tools, run a shell command that starts with:
bitloops devql query '{ selectArtefacts(by: { search: "<task keywords>" }) { count artefacts(first: 10) { path symbolFqn canonicalKind startLine endLine score } } }'

Use the returned paths and symbols to guide your investigation. You may fall back to normal file/search tools after that DevQL query."""


def render_task_prompt(payload: dict[str, Any], wrapper_name: str) -> str:
    problem = str(payload.get("problem_statement", "")).strip()
    _ = wrapper_name  # Signature kept for wrapper compatibility.
    bitloops_instruction = (
        f"\n\n{_BITLOOPS_DEVQL_PROMPT}"
        if _resolve_prompt_protocol(payload) == "minimal_bitloops_devql"
        else ""
    )
    return (
        f"{_MINIMAL_PROMPT_INTRO}\n\n"
        "Do not commit your changes; just leave the edited files in place."
        f"{bitloops_instruction}\n\n"
        f"{problem}"
    )


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
            event_type = _normalize_event_type(node.get("type"))
            if event_type in {"result", "turn_completed"}:
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

    if not usage_blocks:
        for event in _collect_tool_use_events(payload):
            tool_name = _extract_tool_name(event)
            if not tool_name:
                continue
            key = tool_name.strip().lower()
            if not key:
                continue
            totals["tool_calls"] += 1
            if any(token in key for token in ("terminal", "shell", "bash", "cmd")):
                totals["shell_commands"] += 1
            if any(token in key for token in ("search", "grep", "find")):
                totals["search_actions"] += 1
            if any(token in key for token in ("file", "read", "open", "view")):
                totals["file_reads"] += 1

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
    event_type = _normalize_event_type(payload.get("type")) or ""
    if event_type in {"tool_result", "tool_result_delta"}:
        return None
    if event_type == "command_execution":
        return "Bash"

    direct_name = _pick_tool_string(payload, ("tool", "tool_name", "toolName", "name"))
    if event_type in {"tool", "tool_use", "tool_use_delta", "server_tool_use", "tool_call", "toolcall"}:
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
    normalized = {
        "bash": "Bash",
        "read": "Read",
        "edit": "Edit",
        "grep": "Grep",
        "glob": "Glob",
        "webfetch": "WebFetch",
        "websearch": "WebSearch",
    }.get(text.lower())
    if normalized:
        return normalized
    return text


def _normalize_event_type(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip().lower().replace("-", "_").replace(".", "_")
    return text or None


def _extract_tool_use_id(payload: dict[str, Any]) -> str | None:
    for key in ("tool_use_id", "toolUseId", "callID", "call_id"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    event_id = payload.get("id")
    if isinstance(event_id, str) and event_id.strip().startswith("toolu_"):
        return event_id.strip()
    event_type = _normalize_event_type(payload.get("type"))
    if event_type == "command_execution":
        if isinstance(event_id, str) and event_id.strip():
            return event_id.strip()
    return None


def _extract_tool_input_payload(payload: dict[str, Any]) -> Any:
    event_type = _normalize_event_type(payload.get("type"))
    if event_type == "command_execution":
        command = payload.get("command")
        if isinstance(command, str) and command.strip():
            command_payload: dict[str, Any] = {"command": command.strip()}
            status = payload.get("status")
            if isinstance(status, str) and status.strip():
                command_payload["status"] = status.strip()
            exit_code = payload.get("exit_code")
            if isinstance(exit_code, int):
                command_payload["exit_code"] = exit_code
            return command_payload
    if event_type == "tool":
        state = payload.get("state")
        if isinstance(state, dict) and isinstance(state.get("input"), dict):
            return state.get("input")
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
    opencode_tool_indexes: dict[str, int] = {}

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            node_id = id(node)
            if node_id in seen_ids:
                return
            seen_ids.add(node_id)
            if _is_tool_use_invocation(node):
                if _normalize_event_type(node.get("type")) == "tool":
                    tool_use_id = _extract_tool_use_id(node)
                    if tool_use_id:
                        existing_index = opencode_tool_indexes.get(tool_use_id)
                        if existing_index is not None:
                            output[existing_index] = node
                        else:
                            opencode_tool_indexes[tool_use_id] = len(output)
                            output.append(node)
                    else:
                        output.append(node)
                else:
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

    if event_type == "command_execution":
        status = str(payload.get("status") or "").strip().lower()
        if status in {"pending", "in_progress", "running"}:
            return False
        return True

    if event_type == "tool":
        state = payload.get("state")
        if not isinstance(state, dict):
            return False
        status = str(state.get("status") or "").strip().lower()
        return status not in {"", "pending", "error"}

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
    status = input_payload.get("status")
    if isinstance(status, str) and status.strip():
        record["status"] = status.strip()
    exit_code = _coerce_int(input_payload.get("exit_code"))
    if exit_code is not None:
        record["exit_code"] = exit_code


def _populate_edit_curated(record: dict[str, Any], input_payload: dict[str, Any]) -> None:
    path = _pick_from_dict(input_payload, ("file_path", "path", "file"))
    if isinstance(path, str) and path.strip():
        record["path"] = path.strip()

    old_text = _pick_from_dict(input_payload, ("old_string", "oldString", "oldText", "old"))
    new_text = _pick_from_dict(input_payload, ("new_string", "newString", "newText", "new"))
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
