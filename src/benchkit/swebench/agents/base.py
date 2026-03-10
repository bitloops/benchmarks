from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
import json
import subprocess
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
            "model": {
                "provider": context.model.provider,
                "name": context.model.name,
                "canonical_name": context.canonical_model_name,
                "temperature": context.model.temperature,
                "max_tokens": context.model.max_tokens,
            },
            "run": {
                "run_id": context.run_id,
                "attempt": context.attempt,
                "benchmark": context.benchmark,
                "workspace_root": str(context.workspace_root),
            },
        }
        command = self._build_command()

        start = time.time()
        completed = subprocess.run(
            command,
            input=json.dumps(payload),
            text=True,
            capture_output=True,
            timeout=context.timeout_seconds,
            cwd=str(context.workspace_root),
            check=False,
        )
        elapsed_ms = int((time.time() - start) * 1000)

        if completed.returncode != 0:
            stderr = completed.stderr.strip()
            raise RuntimeError(
                f"Adapter command failed (exit={completed.returncode}): {stderr}"
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
