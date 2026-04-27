from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
import json
import os
import subprocess
import sys
import threading
import time

from benchkit.common.config import AgentConfig, ModelConfig
from benchkit.swebench.types import AgentResult, BenchmarkInstance


@dataclass(slots=True)
class RunContext:
    attempt: int
    timeout_seconds: int
    workspace_root: Path
    model: ModelConfig
    canonical_model_name: str
    run_id: str
    benchmark: str
    condition: str | None = None
    prompt_context: str | None = None
    bitloops_sandbox: dict[str, Any] | None = None
    attempt_dir: Path | None = None


@dataclass(slots=True)
class AdapterCall:
    command: list[str] = field(default_factory=list)
    timeout_seconds: int = 900


class AgentAdapter(ABC):
    def __init__(self, config: AgentConfig):
        self.config = config
        self.command_base_dir = Path.cwd().resolve()

    @property
    @abstractmethod
    def adapter_id(self) -> str:
        raise NotImplementedError

    @abstractmethod
    def generate_patch(self, instance: BenchmarkInstance, context: RunContext) -> AgentResult:
        raise NotImplementedError


class NoopAgentAdapter(AgentAdapter):
    @property
    def adapter_id(self) -> str:
        return "noop"

    def generate_patch(self, instance: BenchmarkInstance, context: RunContext) -> AgentResult:
        _ = (instance, context)
        return AgentResult(
            patch="",
            metadata={"mode": "dry-run", "reason": "noop adapter returns empty patch"},
        )


class JsonCommandAgentAdapter(AgentAdapter):
    @property
    def adapter_id(self) -> str:
        return self.config.id

    def _build_command(self) -> list[str]:
        command = list(self.config.command)
        if not command:
            raise ValueError(
                f"agent.command is required for adapter '{self.config.id}'. "
                "Provide a wrapper command that reads JSON stdin and prints JSON stdout."
            )
        command.extend(self.config.extra_args)
        return _resolve_relative_command_paths(command, base_dir=self.command_base_dir)

    def generate_patch(self, instance: BenchmarkInstance, context: RunContext) -> AgentResult:
        payload = {
            "instance_id": instance.instance_id,
            "repo": instance.repo,
            "base_commit": instance.base_commit,
            "problem_statement": instance.problem_statement,
            "language": instance.language,
            "metadata": instance.metadata,
            "prompt_context": context.prompt_context,
            "model": {
                "provider": context.model.provider,
                "name": context.model.name,
                "canonical_name": context.canonical_model_name,
                "temperature": context.model.temperature,
                "max_tokens": context.model.max_tokens,
                "seed": context.model.seed,
            },
            "run": {
                "run_id": context.run_id,
                "attempt": context.attempt,
                "benchmark": context.benchmark,
                "condition": context.condition,
                "timeout_seconds": context.timeout_seconds,
                "workspace_root": str(context.workspace_root),
                "attempt_dir": (
                    str(context.attempt_dir.resolve())
                    if isinstance(context.attempt_dir, Path)
                    else None
                ),
                "bitloops_sandbox": context.bitloops_sandbox,
            },
        }
        command = self._build_command()

        start = time.time()
        heartbeat = _AgentHeartbeat(
            adapter_id=self.config.id,
            instance_id=instance.instance_id,
            attempt=context.attempt,
            timeout_seconds=context.timeout_seconds,
        )
        heartbeat.start()
        try:
            completed = subprocess.run(
                command,
                input=json.dumps(payload),
                text=True,
                capture_output=True,
                timeout=context.timeout_seconds,
                cwd=str(context.workspace_root),
                check=False,
            )
        finally:
            heartbeat.stop()
        elapsed_ms = int((time.time() - start) * 1000)

        if completed.returncode != 0:
            stderr = completed.stderr.strip()
            stdout = completed.stdout.strip()
            detail = stderr or _summarize_failed_adapter_stdout(stdout)
            raise RuntimeError(
                f"Adapter command failed (exit={completed.returncode}): {detail}"
            )

        stdout = completed.stdout.strip()
        if not stdout:
            raise RuntimeError("Adapter command produced empty stdout")

        response = json.loads(stdout)
        patch = str(response.get("patch", ""))
        metadata: dict[str, Any] = dict(response.get("metadata", {}))
        metadata.setdefault("adapter_command", command)
        metadata.setdefault("elapsed_ms", elapsed_ms)

        return AgentResult(patch=patch, metadata=metadata)


def _summarize_failed_adapter_stdout(stdout: str) -> str:
    if not stdout.strip():
        return ""

    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError:
        return stdout.strip()

    if isinstance(payload, dict):
        if "error" in payload:
            return json.dumps(payload)
        result = payload.get("result")
        if isinstance(result, str) and result.strip():
            return result.strip()
        return json.dumps(payload)

    return stdout.strip()


def _resolve_relative_command_paths(command: list[str], base_dir: Path) -> list[str]:
    resolved: list[str] = []
    for token in command:
        if not token:
            resolved.append(token)
            continue

        candidate = Path(token)
        if candidate.is_absolute():
            resolved.append(token)
            continue

        # Resolve local relative script/binary paths while leaving plain command
        # names (e.g. `python3`, `claude`) untouched.
        looks_like_path = (
            token.startswith(".")
            or "/" in token
            or "\\" in token
        )
        if not looks_like_path:
            resolved.append(token)
            continue

        absolute = (base_dir / candidate).resolve()
        resolved.append(str(absolute) if absolute.exists() else token)
    return resolved


def _heartbeat_interval_seconds() -> float | None:
    raw = os.environ.get("BENCHKIT_AGENT_HEARTBEAT_SECONDS", "20").strip().lower()
    if raw in {"", "0", "off", "false", "no"}:
        return None
    try:
        value = float(raw)
    except ValueError:
        return 20.0
    return value if value > 0 else None


class _AgentHeartbeat:
    def __init__(
        self,
        adapter_id: str,
        instance_id: str,
        attempt: int,
        timeout_seconds: int,
    ) -> None:
        self.adapter_id = adapter_id
        self.instance_id = instance_id
        self.attempt = attempt
        self.timeout_seconds = timeout_seconds
        self.interval_seconds = _heartbeat_interval_seconds()
        self._start = time.time()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._emit_start()
        if self.interval_seconds is None:
            return
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=0.2)
        elapsed = int(time.time() - self._start)
        self._emit(f"finished in {elapsed}s")

    def _run(self) -> None:
        assert self.interval_seconds is not None
        while not self._stop_event.wait(self.interval_seconds):
            elapsed = int(time.time() - self._start)
            self._emit(f"still running ({elapsed}s elapsed)")

    def _emit_start(self) -> None:
        timeout_note = (
            f", timeout={self.timeout_seconds}s" if self.timeout_seconds > 0 else ""
        )
        self._emit(f"started{timeout_note}")

    def _emit(self, message: str) -> None:
        sys.stderr.write(
            "[benchkit] "
            f"agent={self.adapter_id} attempt={self.attempt} "
            f"instance={self.instance_id}: {message}\n"
        )
        sys.stderr.flush()
