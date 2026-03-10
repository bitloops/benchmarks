from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class BenchmarkInstance:
    instance_id: str
    repo: str
    base_commit: str
    problem_statement: str
    language: str = "unknown"
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_row(self) -> dict[str, Any]:
        return {
            "instance_id": self.instance_id,
            "repo": self.repo,
            "base_commit": self.base_commit,
            "problem_statement": self.problem_statement,
            "language": self.language,
            "metadata": self.metadata,
        }


@dataclass(slots=True)
class AgentResult:
    patch: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class PredictionRecord:
    instance_id: str
    model_name_or_path: str
    model_patch: str

    def to_row(self) -> dict[str, str]:
        return {
            "instance_id": self.instance_id,
            "model_name_or_path": self.model_name_or_path,
            "model_patch": self.model_patch,
        }
