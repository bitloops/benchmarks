"""Summarize effective Ollama runtime config for plan output."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Any

from benchkit.common.config import RunConfig
from .runtime import (
    build_ollama_request_options,
    build_ollama_runtime_config,
    default_ollama_json_path,
    resolve_ollama_base_url,
    resolve_ollama_timeout_seconds,
)


def build_ollama_run_metadata(
    *,
    config: RunConfig,
    resolved_model_name: str,
    ollama_json_path: Path | None = None,
) -> dict[str, Any]:
    path = (ollama_json_path or default_ollama_json_path()).resolve()
    repo_root = path.parents[2]
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

    runtime_config = build_ollama_runtime_config(
        existing_content=os.environ.get("OLLAMA_CONFIG_CONTENT", ""),
        repo_config_path=path,
    )
    payload = {
        "model": {
            "name": resolved_model_name,
            "temperature": config.model.temperature,
            "max_tokens": config.model.max_tokens,
            "seed": config.model.seed,
        },
        "run": {
            "timeout_seconds": config.timeout_seconds,
        },
    }
    options = build_ollama_request_options(
        payload=payload,
        runtime_config=runtime_config,
        model_name=resolved_model_name,
    )

    base["base_url"] = resolve_ollama_base_url(runtime_config)
    base["timeout_seconds"] = resolve_ollama_timeout_seconds(payload, runtime_config)
    base["model"] = resolved_model_name
    base["temperature"] = options.get("temperature")
    base["num_predict"] = options.get("num_predict")
    base["seed"] = options.get("seed")
    return base


def format_ollama_plan_lines(meta: dict[str, Any]) -> list[str]:
    lines = [
        "",
        "Effective Ollama run config (same merge the run command will use):",
        f"  Config: {meta.get('config_path')}",
    ]
    rel = meta.get("config_path_repo_relative")
    if rel:
        lines.append(f"  Repo-relative: {rel}")
    if "error" in meta:
        lines.append(f"  Status: error ({meta['error']})")
        return lines

    lines.append(f"  SHA256: {meta.get('config_sha256')}")
    lines.append(f"  Base URL: {meta.get('base_url')}")
    lines.append(f"  Timeout seconds: {meta.get('timeout_seconds')}")
    lines.append(f"  Model: {meta.get('model')}")
    lines.append(f"  Temperature: {meta.get('temperature')}")
    lines.append(f"  num_predict: {meta.get('num_predict')}")
    lines.append(f"  Seed: {meta.get('seed') if meta.get('seed') is not None else 'none'}")
    return lines
