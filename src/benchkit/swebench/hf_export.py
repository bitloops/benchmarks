from __future__ import annotations

from dataclasses import dataclass
import importlib
from pathlib import Path
import sys
from typing import Any
import json
import os
import re

from .dataset import (
    BENCHMARK_MULTILINGUAL,
    BENCHMARK_PRO,
    resolve_language_from_row,
)

DEFAULT_MULTILINGUAL_DATASET = "SWE-bench/SWE-bench_Multilingual"
DEFAULT_PRO_DATASET = "ScaleAI/SWE-bench_Pro"
DEFAULT_DATASET = DEFAULT_MULTILINGUAL_DATASET


def default_dataset_for_benchmark(benchmark: str) -> str:
    if benchmark == BENCHMARK_PRO:
        return DEFAULT_PRO_DATASET
    return DEFAULT_MULTILINGUAL_DATASET

INSTANCE_ID_KEYS = ("instance_id", "id", "task_id", "sample_id")
REPO_KEYS = ("repo", "repository", "repo_name")
BASE_COMMIT_KEYS = ("base_commit", "base_sha", "commit", "base_revision")
PROBLEM_KEYS = ("problem_statement", "problem", "prompt", "issue", "instruction")

LANGUAGE_ALIASES = {
    "rs": "rust",
    "rustlang": "rust",
    "tokio-rs": "rust",
    "py": "python",
    "js": "javascript",
    "ts": "typescript",
    "c++": "cpp",
    "cplusplus": "cpp",
    "golang": "go",
}


@dataclass(slots=True)
class ExportStats:
    dataset: str
    dataset_config: str | None
    split: str
    revision: str | None
    output_path: Path
    total_rows_seen: int
    rows_written: int
    language_filter: str | None
    max_instances: int | None


def export_hf_swebench_multilingual(
    output_path: Path,
    split: str,
    dataset: str = DEFAULT_DATASET,
    dataset_config: str | None = None,
    revision: str | None = None,
    cache_dir: Path | None = None,
    streaming: bool = False,
    language: str | None = None,
    include_repos: list[str] | None = None,
    include_instance_ids: list[str] | None = None,
    max_instances: int | None = None,
    overwrite: bool = False,
    token_env: str = "HF_TOKEN",
) -> ExportStats:
    return export_hf_dataset(
        output_path=output_path,
        split=split,
        dataset=dataset,
        dataset_config=dataset_config,
        revision=revision,
        cache_dir=cache_dir,
        streaming=streaming,
        language=language,
        include_repos=include_repos,
        include_instance_ids=include_instance_ids,
        max_instances=max_instances,
        overwrite=overwrite,
        token_env=token_env,
        benchmark=BENCHMARK_MULTILINGUAL,
    )


def export_hf_dataset(
    output_path: Path,
    split: str,
    dataset: str,
    dataset_config: str | None = None,
    revision: str | None = None,
    cache_dir: Path | None = None,
    streaming: bool = False,
    language: str | None = None,
    include_repos: list[str] | None = None,
    include_instance_ids: list[str] | None = None,
    max_instances: int | None = None,
    overwrite: bool = False,
    token_env: str = "HF_TOKEN",
    benchmark: str = BENCHMARK_MULTILINGUAL,
) -> ExportStats:
    if max_instances is not None and max_instances < 1:
        raise ValueError("max_instances must be >= 1 when provided")

    output_path = output_path.resolve()
    if output_path.exists() and not overwrite:
        raise FileExistsError(
            f"Output already exists: {output_path}. Use overwrite=True to replace it."
        )
    output_path.parent.mkdir(parents=True, exist_ok=True)

    token = os.environ.get(token_env, "").strip() or None
    hf_dataset = _load_hf_dataset(
        dataset=dataset,
        dataset_config=dataset_config,
        split=split,
        revision=revision,
        cache_dir=cache_dir,
        streaming=streaming,
        token=token,
    )

    language_target = _normalize_language(language) if language else None
    repo_allow = {
        repo.strip().lower()
        for repo in (include_repos or [])
        if repo.strip()
    }
    task_allow = {
        task_id.strip()
        for task_id in (include_instance_ids or [])
        if task_id.strip()
    }
    total_rows_seen = 0
    rows_written = 0

    with output_path.open("w", encoding="utf-8") as handle:
        for raw_row in hf_dataset:
            total_rows_seen += 1
            normalized_row = normalize_hf_row(raw_row, benchmark=benchmark)
            row_language = _normalize_language(str(normalized_row.get("language", "unknown")))
            if language_target and row_language != language_target:
                continue
            if repo_allow and str(normalized_row.get("repo", "")).lower() not in repo_allow:
                continue
            if task_allow and str(normalized_row.get("instance_id", "")) not in task_allow:
                continue

            handle.write(json.dumps(normalized_row, ensure_ascii=False))
            handle.write("\n")
            rows_written += 1

            if max_instances is not None and rows_written >= max_instances:
                break

    return ExportStats(
        dataset=dataset,
        dataset_config=dataset_config,
        split=split,
        revision=revision,
        output_path=output_path,
        total_rows_seen=total_rows_seen,
        rows_written=rows_written,
        language_filter=language_target,
        max_instances=max_instances,
    )


def normalize_hf_row(
    raw_row: dict[str, Any],
    benchmark: str = BENCHMARK_MULTILINGUAL,
) -> dict[str, Any]:
    if not isinstance(raw_row, dict):
        raise ValueError("HF dataset row is not a JSON object")

    instance_id = _first_required_string(raw_row, INSTANCE_ID_KEYS, "instance_id")
    repo = _first_required_string(raw_row, REPO_KEYS, "repo")
    repo_label = _first_string(raw_row, ("repo_label", "repo_owner", "owner")) or _derive_repo_label(
        repo
    )
    base_commit = _first_required_string(raw_row, BASE_COMMIT_KEYS, "base_commit")
    problem_statement = _first_string(raw_row, PROBLEM_KEYS) or ""

    language = resolve_language_from_row(raw_row, benchmark=benchmark)
    if language == "unknown":
        language = _infer_language_from_instance_id(instance_id)

    normalized = dict(raw_row)
    normalized["instance_id"] = instance_id
    normalized["repo"] = repo
    normalized["repo_label"] = repo_label
    normalized["base_commit"] = base_commit
    normalized["problem_statement"] = problem_statement
    normalized["language"] = _normalize_language(language)
    return normalized


def _load_hf_dataset(
    dataset: str,
    dataset_config: str | None,
    split: str,
    revision: str | None,
    cache_dir: Path | None,
    streaming: bool,
    token: str | None,
) -> Any:
    datasets_module = _import_datasets_module()
    load_dataset = getattr(datasets_module, "load_dataset", None)
    if not callable(load_dataset):
        fallback_module = _import_datasets_without_workspace_shadowing()
        if fallback_module is not None:
            datasets_module = fallback_module
            load_dataset = getattr(datasets_module, "load_dataset", None)
    if not callable(load_dataset):
        raise RuntimeError(
            "Import 'datasets' does not expose load_dataset(). "
            "Install HF datasets with: pip install -e '.[hf]'. "
            "If already installed, ensure it is not shadowed by a local ./datasets directory."
        )

    kwargs: dict[str, Any] = {
        "path": dataset,
        "split": split,
        "streaming": streaming,
    }
    if dataset_config:
        kwargs["name"] = dataset_config
    if revision:
        kwargs["revision"] = revision
    if cache_dir:
        kwargs["cache_dir"] = str(cache_dir)
    if token:
        kwargs["token"] = token

    return load_dataset(**kwargs)


def _import_datasets_module() -> Any:
    try:
        return importlib.import_module("datasets")
    except ImportError:
        pass

    module = _import_datasets_without_workspace_shadowing()
    if module is not None:
        return module

    raise RuntimeError("Missing dependency 'datasets'. Install with: pip install -e '.[hf]'")


def _import_datasets_without_workspace_shadowing() -> Any | None:
    original_path = list(sys.path)
    original_module = sys.modules.get("datasets")
    cwd = str(Path.cwd().resolve())

    def _is_shadow_entry(entry: str) -> bool:
        if entry in {"", "."}:
            return True
        try:
            return str(Path(entry).resolve()) == cwd
        except Exception:  # noqa: BLE001
            return False

    try:
        sys.path = [entry for entry in sys.path if not _is_shadow_entry(entry)]
        if "datasets" in sys.modules:
            del sys.modules["datasets"]
        return importlib.import_module("datasets")
    except ImportError:
        return None
    finally:
        sys.path = original_path
        if "datasets" not in sys.modules and original_module is not None:
            sys.modules["datasets"] = original_module


def _first_required_string(
    row: dict[str, Any],
    keys: tuple[str, ...],
    target_name: str,
) -> str:
    value = _first_string(row, keys)
    if value:
        return value
    raise ValueError(
        f"Cannot normalize row: missing '{target_name}' (tried keys: {', '.join(keys)})"
    )


def _first_string(row: dict[str, Any], keys: tuple[str, ...]) -> str | None:
    for key in keys:
        if key not in row:
            continue
        value = row[key]
        if value is None:
            continue
        if isinstance(value, str):
            stripped = value.strip()
            if stripped:
                return stripped
            continue
        if isinstance(value, (int, float)):
            return str(value)
    return None


def _infer_language_from_instance_id(instance_id: str) -> str:
    match = re.match(r"^([a-zA-Z0-9_\-\+]+)__", instance_id)
    if not match:
        return "unknown"
    return _normalize_language(match.group(1))


def _derive_repo_label(repo: str) -> str:
    owner, _, _name = repo.partition("/")
    return owner.strip() or repo


def _normalize_language(raw: str) -> str:
    cleaned = raw.strip().lower()
    if not cleaned:
        return "unknown"
    return LANGUAGE_ALIASES.get(cleaned, cleaned)
