from __future__ import annotations

from pathlib import Path
from typing import Any
import json

from .types import BenchmarkInstance

LANGUAGE_KEYS = (
    "language",
    "lang",
    "repo_language",
    "programming_language",
)

LANGUAGE_ALIASES = {
    "rs": "rust",
    "rustlang": "rust",
    "tokio-rs": "rust",
}


def load_instances(dataset_path: Path) -> list[BenchmarkInstance]:
    dataset_path = dataset_path.resolve()
    if not dataset_path.exists():
        raise FileNotFoundError(f"Dataset file not found: {dataset_path}")

    if dataset_path.suffix == ".jsonl":
        rows = _read_jsonl(dataset_path)
    elif dataset_path.suffix == ".json":
        payload = json.loads(dataset_path.read_text(encoding="utf-8"))
        if isinstance(payload, list):
            rows = payload
        elif isinstance(payload, dict) and isinstance(payload.get("data"), list):
            rows = payload["data"]
        else:
            raise ValueError("JSON dataset must be a list or {'data': list}")
    else:
        raise ValueError("Supported dataset formats: .jsonl or .json")

    instances: list[BenchmarkInstance] = []
    for row in rows:
        instance = _row_to_instance(row)
        instances.append(instance)
    return instances


def filter_instances(
    instances: list[BenchmarkInstance],
    language: str | None = None,
    include_repos: list[str] | None = None,
    include_instance_ids: list[str] | None = None,
    max_instances: int | None = None,
) -> list[BenchmarkInstance]:
    selected = instances

    if language:
        target = normalize_language_name(language)
        selected = [
            item
            for item in selected
            if normalize_language_name(item.language) == target
        ]

    if include_repos:
        allow = {repo.strip().lower() for repo in include_repos if repo.strip()}
        selected = [item for item in selected if item.repo.lower() in allow]

    if include_instance_ids:
        allow_ids = {task_id.strip() for task_id in include_instance_ids if task_id.strip()}
        selected = [item for item in selected if item.instance_id in allow_ids]

    if max_instances is not None:
        if max_instances < 1:
            raise ValueError("max_instances must be >= 1 when provided")
        selected = selected[:max_instances]

    return selected


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
    return rows


def _row_to_instance(row: dict[str, Any]) -> BenchmarkInstance:
    if "instance_id" not in row:
        raise ValueError("Dataset row missing 'instance_id'")
    if "repo" not in row:
        raise ValueError(f"Dataset row {row.get('instance_id')} missing 'repo'")
    if "base_commit" not in row:
        raise ValueError(f"Dataset row {row.get('instance_id')} missing 'base_commit'")

    language = resolve_language_from_row(row)
    statement = str(
        row.get("problem_statement")
        or row.get("problem")
        or row.get("prompt")
        or ""
    ).strip()

    metadata = {
        key: value
        for key, value in row.items()
        if key
        not in {
            "instance_id",
            "repo",
            "base_commit",
            "problem_statement",
            "problem",
            "prompt",
            "patch",
            "test_patch",
            "language",
            "lang",
            "repo_language",
            "programming_language",
        }
    }

    return BenchmarkInstance(
        instance_id=str(row["instance_id"]),
        repo=str(row["repo"]),
        base_commit=str(row["base_commit"]),
        problem_statement=statement,
        language=language,
        metadata=metadata,
    )


def resolve_language_from_row(row: dict[str, Any]) -> str:
    for key in LANGUAGE_KEYS:
        value = row.get(key)
        if isinstance(value, str) and value.strip():
            return normalize_language_name(value)
    return "unknown"


def normalize_language_name(raw: str) -> str:
    cleaned = raw.strip().lower()
    if not cleaned:
        return "unknown"
    return LANGUAGE_ALIASES.get(cleaned, cleaned)
