"""Summarize committed Codex JSON for plan output and run_manifest.json."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

_PICK_KEYS = (
    "model",
    "model_reasoning_effort",
    "model_verbosity",
    "model_reasoning_summary",
    "timeout_seconds",
    "full_auto",
    "sandbox",
    "skip_git_repo_check",
    "extra_args",
)


def _repo_root() -> Path:
    import benchkit

    return Path(benchkit.__file__).resolve().parents[2]


def default_codex_json_path() -> Path:
    return _repo_root() / "configs" / "codex" / "codex.json"


def build_codex_run_metadata(
    *,
    codex_json_path: Path | None = None,
) -> dict[str, Any]:
    """
    Return a JSON-serializable summary of configs/codex/codex.json.

    Used for run_manifest and plan preflight. Always includes config_path;
    includes error when the file is missing or invalid JSON.
    """
    path = (codex_json_path or default_codex_json_path()).resolve()
    repo_root = _repo_root()
    try:
        rel = str(path.relative_to(repo_root))
    except ValueError:
        rel = None

    base: dict[str, Any] = {
        "config_source": "repo_json",
        "config_path": str(path),
        "config_path_repo_relative": rel,
    }

    if not path.exists():
        base["error"] = "file_not_found"
        return base

    raw = path.read_bytes()
    base["config_sha256"] = hashlib.sha256(raw).hexdigest()

    try:
        loaded = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        base["error"] = "invalid_json"
        base["error_detail"] = str(exc)
        return base

    if not isinstance(loaded, dict):
        base["error"] = "not_a_json_object"
        return base

    for key in _PICK_KEYS:
        if key in loaded:
            base[key] = loaded[key]
    return base


def format_codex_plan_lines(meta: dict[str, Any]) -> list[str]:
    """Human-readable lines for `benchkit.swebench.cli plan`."""
    lines = [
        "",
        "Codex repo config (runtime settings for codex runs; not from TOML):",
        f"  Config: {meta.get('config_path')}",
    ]
    rel = meta.get("config_path_repo_relative")
    if rel:
        lines.append(f"  Repo-relative: {rel}")
    if "error" in meta:
        lines.append(f"  Status: error ({meta['error']})")
        if detail := meta.get("error_detail"):
            lines.append(f"  Detail: {detail}")
        return lines

    lines.append(f"  SHA256: {meta.get('config_sha256')}")
    for key in (
        "model",
        "model_reasoning_effort",
        "model_verbosity",
        "model_reasoning_summary",
        "timeout_seconds",
        "full_auto",
        "sandbox",
        "skip_git_repo_check",
        "extra_args",
    ):
        if key in meta:
            lines.append(f"  {key}: {meta[key]}")
    return lines
