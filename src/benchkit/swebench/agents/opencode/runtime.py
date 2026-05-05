from __future__ import annotations

import json
import os
from copy import deepcopy
from pathlib import Path

from ..common import resolve_timeout_seconds


def _repo_root() -> Path:
    import benchkit

    return Path(benchkit.__file__).resolve().parents[2]


def resolve_opencode_timeout_seconds(payload: dict[str, object]) -> int:
    return resolve_timeout_seconds(
        payload,
        env_var="OPENCODE_TIMEOUT_SECONDS",
        default_seconds=900,
    )


def normalize_opencode_provider_id(provider_id: str | None) -> str | None:
    normalized = str(provider_id or "").strip()
    if not normalized:
        return None
    if normalized == "fireworks":
        return "fireworks-ai"
    return normalized


def normalize_opencode_model_reference(model_reference: str) -> str:
    normalized = model_reference.strip()
    if not normalized or "/" not in normalized:
        return normalized

    provider_id, model_id = normalized.split("/", 1)
    canonical_provider_id = normalize_opencode_provider_id(provider_id)
    if not canonical_provider_id:
        return normalized
    return f"{canonical_provider_id}/{model_id.strip()}"


def resolve_repo_opencode_config_path() -> Path:
    return _repo_root() / "configs" / "opencode" / "opencode.json"


def default_ollama_json_path() -> Path:
    return _repo_root() / "configs" / "opencode" / "ollama.json"


def decode_opencode_config_content(
    raw_content: str,
    *,
    source_name: str,
) -> dict[str, object]:
    loaded = json.loads(raw_content)
    if not isinstance(loaded, dict):
        raise ValueError(f"{source_name} must decode to a JSON object")
    return loaded


def load_opencode_config_file(config_path: Path) -> dict[str, object]:
    return decode_opencode_config_content(
        config_path.read_text(encoding="utf-8"),
        source_name=str(config_path),
    )


def decode_ollama_json_object(raw_content: str, *, source_name: str) -> dict[str, object]:
    loaded = json.loads(raw_content)
    if not isinstance(loaded, dict):
        raise ValueError(f"{source_name} must decode to a JSON object")
    return loaded


def load_ollama_config_file(config_path: Path) -> dict[str, object]:
    return decode_ollama_json_object(
        config_path.read_text(encoding="utf-8"),
        source_name=str(config_path),
    )


def deep_merge_opencode_dicts(base: dict[str, object], overlay: dict[str, object]) -> dict[str, object]:
    merged: dict[str, object] = deepcopy(base)
    for key, value in overlay.items():
        current = merged.get(key)
        if isinstance(current, dict) and isinstance(value, dict):
            merged[key] = deep_merge_opencode_dicts(current, value)
            continue
        merged[key] = deepcopy(value)
    return merged


def deep_merge_ollama_dicts(base: dict[str, object], overlay: dict[str, object]) -> dict[str, object]:
    merged: dict[str, object] = deepcopy(base)
    for key, value in overlay.items():
        current = merged.get(key)
        if isinstance(current, dict) and isinstance(value, dict):
            merged[key] = deep_merge_ollama_dicts(current, value)
            continue
        merged[key] = deepcopy(value)
    return merged


def merge_opencode_config_content(
    existing_content: str,
    runtime_config: dict[str, object],
) -> dict[str, object]:
    if not existing_content.strip():
        return deepcopy(runtime_config)

    loaded = decode_opencode_config_content(
        existing_content,
        source_name="OPENCODE_CONFIG_CONTENT",
    )
    return deep_merge_opencode_dicts(loaded, runtime_config)


def build_opencode_invocation_config(
    *,
    existing_content: str,
    repo_config_path: Path | None = None,
) -> dict[str, object] | None:
    merged: dict[str, object] | None = None
    if existing_content.strip():
        merged = decode_opencode_config_content(
            existing_content,
            source_name="OPENCODE_CONFIG_CONTENT",
        )

    config_path = (repo_config_path or resolve_repo_opencode_config_path()).resolve()
    if config_path.exists():
        repo_config = load_opencode_config_file(config_path)
        merged = deep_merge_opencode_dicts(merged or {}, repo_config)

    return merged


def build_ollama_runtime_config(
    *,
    existing_content: str,
    repo_config_path: Path | None = None,
) -> dict[str, object]:
    merged: dict[str, object] = {}
    if existing_content.strip():
        merged = decode_ollama_json_object(
            existing_content,
            source_name="OLLAMA_CONFIG_CONTENT",
        )
    config_path = (repo_config_path or default_ollama_json_path()).resolve()
    if config_path.exists():
        repo_config = load_ollama_config_file(config_path)
        merged = deep_merge_ollama_dicts(merged, repo_config)
    return merged


def resolve_ollama_base_url(runtime_config: dict[str, object]) -> str:
    configured = str(runtime_config.get("base_url", "")).strip()
    env_override = os.environ.get("OLLAMA_BASE_URL", "").strip()
    base_url = env_override or configured or "http://localhost:11434"
    return base_url.rstrip("/")


def normalize_opencode_ollama_base_url(base_url: str) -> str:
    normalized = base_url.strip().rstrip("/")
    if not normalized:
        return "http://localhost:11434/v1"
    if normalized.endswith("/v1"):
        return normalized
    return f"{normalized}/v1"


def build_ollama_opencode_provider_overlay(
    *,
    model_name: str,
    existing_ollama_content: str = "",
    repo_ollama_config_path: Path | None = None,
) -> dict[str, object] | None:
    normalized_model_name = normalize_opencode_model_reference(model_name)
    if not normalized_model_name.startswith("ollama/"):
        return None

    _, model_id = normalized_model_name.split("/", 1)
    ollama_runtime_config = build_ollama_runtime_config(
        existing_content=existing_ollama_content,
        repo_config_path=repo_ollama_config_path or default_ollama_json_path(),
    )
    base_url = normalize_opencode_ollama_base_url(
        resolve_ollama_base_url(ollama_runtime_config)
    )

    return {
        "model": normalized_model_name,
        "small_model": normalized_model_name,
        "provider": {
            "ollama": {
                "npm": "@ai-sdk/openai-compatible",
                "name": "Ollama",
                "options": {
                    "baseURL": base_url,
                },
                "models": {
                    model_id: {
                        "name": model_id,
                    }
                },
            }
        },
    }
