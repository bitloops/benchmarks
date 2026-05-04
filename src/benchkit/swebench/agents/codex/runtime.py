from __future__ import annotations

import json
import os
from copy import deepcopy
from pathlib import Path

from ..common import env_flag, resolve_timeout_seconds


def _repo_root() -> Path:
    import benchkit

    return Path(benchkit.__file__).resolve().parents[2]


def resolve_repo_codex_config_path() -> Path:
    return _repo_root() / "configs" / "codex" / "codex.json"


def decode_codex_config_content(
    raw_content: str,
    *,
    source_name: str,
) -> dict[str, object]:
    loaded = json.loads(raw_content)
    if not isinstance(loaded, dict):
        raise ValueError(f"{source_name} must decode to a JSON object")
    return loaded


def load_codex_config_file(config_path: Path) -> dict[str, object]:
    return decode_codex_config_content(
        config_path.read_text(encoding="utf-8"),
        source_name=str(config_path),
    )


def deep_merge_codex_dicts(base: dict[str, object], overlay: dict[str, object]) -> dict[str, object]:
    merged: dict[str, object] = deepcopy(base)
    for key, value in overlay.items():
        current = merged.get(key)
        if isinstance(current, dict) and isinstance(value, dict):
            merged[key] = deep_merge_codex_dicts(current, value)
            continue
        merged[key] = deepcopy(value)
    return merged


def build_codex_runtime_config(
    *,
    existing_content: str,
    repo_config_path: Path | None = None,
) -> dict[str, object]:
    merged: dict[str, object] = {}
    if existing_content.strip():
        merged = decode_codex_config_content(
            existing_content,
            source_name="CODEX_CONFIG_CONTENT",
        )
    config_path = (repo_config_path or resolve_repo_codex_config_path()).resolve()
    if config_path.exists():
        repo_config = load_codex_config_file(config_path)
        merged = deep_merge_codex_dicts(merged, repo_config)
    return merged


def resolve_codex_config_timeout_seconds(runtime_config: dict[str, object]) -> int:
    raw = runtime_config.get("timeout_seconds")
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return 0
    return value if value > 0 else 0


def resolve_codex_timeout_seconds(
    payload: dict[str, object],
    runtime_config: dict[str, object],
) -> int:
    config_value = resolve_codex_config_timeout_seconds(runtime_config)
    return resolve_timeout_seconds(
        payload,
        env_var="CODEX_TIMEOUT_SECONDS",
        default_seconds=900,
        extra_values=(config_value,),
    )


def as_codex_bool(value: object, *, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    return default


def resolve_codex_full_auto(runtime_config: dict[str, object]) -> bool:
    if "CODEX_FULL_AUTO" in os.environ:
        return env_flag("CODEX_FULL_AUTO", True)
    return as_codex_bool(runtime_config.get("full_auto"), default=True)


def resolve_codex_skip_git_repo_check(runtime_config: dict[str, object]) -> bool:
    if "CODEX_SKIP_GIT_REPO_CHECK" in os.environ:
        return env_flag("CODEX_SKIP_GIT_REPO_CHECK", False)
    return as_codex_bool(runtime_config.get("skip_git_repo_check"), default=False)


def resolve_codex_sandbox_mode(runtime_config: dict[str, object]) -> str:
    sandbox = os.environ.get("CODEX_SANDBOX", "").strip()
    if sandbox:
        return sandbox
    configured = runtime_config.get("sandbox")
    if isinstance(configured, str) and configured.strip():
        return configured.strip()
    return "workspace-write"


def normalize_codex_reasoning_effort(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip().lower()
    if normalized not in {"none", "minimal", "low", "medium", "high", "xhigh"}:
        return None
    return normalized


def normalize_codex_verbosity(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip().lower()
    if normalized not in {"low", "medium", "high"}:
        return None
    return normalized


def normalize_codex_reasoning_summary(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip().lower()
    if normalized not in {"auto", "concise", "detailed", "none"}:
        return None
    return normalized


def normalize_codex_extra_args(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for item in value:
        if not isinstance(item, str):
            continue
        cleaned = item.strip()
        if cleaned:
            out.append(cleaned)
    return out
