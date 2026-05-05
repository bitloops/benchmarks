"""Summarize committed OpenCode JSON for plan output and run_manifest.json."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

from benchkit.common.config import RunConfig

from .runtime import (
    build_ollama_runtime_config,
    default_ollama_json_path,
    normalize_opencode_model_reference,
    normalize_opencode_ollama_base_url,
    resolve_ollama_base_url,
    resolve_repo_opencode_config_path,
)

_SAMPLING_KEYS = ("temperature", "seed", "max_tokens")

def default_opencode_json_path() -> Path:
    return resolve_repo_opencode_config_path()


def _pick_agent_sampling(agent_cfg: object) -> dict[str, Any] | None:
    if not isinstance(agent_cfg, dict):
        return None
    picked: dict[str, Any] = {}
    for key in _SAMPLING_KEYS:
        if key not in agent_cfg:
            continue
        picked[key] = agent_cfg[key]
    return picked or None


def _provider_sampling_snapshot(data: dict[str, Any]) -> dict[str, Any] | None:
    provider = data.get("provider")
    if not isinstance(provider, dict):
        return None
    out: dict[str, Any] = {}
    for provider_id, block in provider.items():
        if not isinstance(block, dict):
            continue
        models = block.get("models")
        if not isinstance(models, dict):
            continue
        for model_id, meta in models.items():
            if not isinstance(meta, dict):
                continue
            opts = meta.get("options")
            if not isinstance(opts, dict):
                continue
            snap = {k: opts[k] for k in _SAMPLING_KEYS if k in opts}
            if snap:
                out.setdefault(str(provider_id), {})[str(model_id)] = snap
    return out or None


def build_opencode_run_metadata(
    *,
    opencode_json_path: Path | None = None,
) -> dict[str, Any]:
    """
    Return a JSON-serializable summary of configs/opencode/opencode.json.

    Used for run_manifest and plan preflight. Always includes config_path;
    includes error when the file is missing or invalid JSON.
    """
    path = (opencode_json_path or default_opencode_json_path()).resolve()
    repo_root = path.parents[2]
    try:
        rel = str(path.relative_to(repo_root))
    except ValueError:
        rel = None

    base: dict[str, Any] = {
        "sampling_source": "repo_json",
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

    base["declared_model"] = loaded.get("model")
    base["declared_small_model"] = loaded.get("small_model")

    agents = loaded.get("agent")
    if isinstance(agents, dict):
        for name in ("build", "plan"):
            cfg = agents.get(name)
            sampling = _pick_agent_sampling(cfg)
            if sampling is not None:
                base[f"agent_{name}_sampling"] = sampling

    prov = _provider_sampling_snapshot(loaded)
    if prov is not None:
        base["provider_model_sampling"] = prov

    return base


def format_opencode_plan_lines(meta: dict[str, Any]) -> list[str]:
    """Human-readable lines for `benchkit.swebench.cli plan`."""
    lines = [
        "",
        "OpenCode repo config (sampling for opencode runs; not from TOML):",
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
    if "declared_model" in meta:
        lines.append(f"  JSON model: {meta.get('declared_model')}")
    if "declared_small_model" in meta:
        lines.append(f"  JSON small_model: {meta.get('declared_small_model')}")
    if "agent_build_sampling" in meta:
        lines.append(f"  agent.build sampling: {meta['agent_build_sampling']}")
    if "agent_plan_sampling" in meta:
        lines.append(f"  agent.plan sampling: {meta['agent_plan_sampling']}")
    if "provider_model_sampling" in meta:
        lines.append(f"  Provider model sampling: {meta['provider_model_sampling']}")
    return lines


def build_ollama_provider_run_metadata(
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
    normalized_model_name = normalize_opencode_model_reference(resolved_model_name)
    provider_model_id = normalized_model_name
    if normalized_model_name.startswith("ollama/"):
        provider_model_id = normalized_model_name.split("/", 1)[1]

    base_url = resolve_ollama_base_url(runtime_config)
    base["base_url"] = base_url
    base["openai_compatible_base_url"] = normalize_opencode_ollama_base_url(base_url)
    base["model"] = normalized_model_name
    base["provider_model_id"] = provider_model_id
    base["provider"] = str(config.model.provider)
    return base


def format_ollama_provider_plan_lines(meta: dict[str, Any]) -> list[str]:
    lines = [
        "",
        "Effective Ollama provider config (same provider overlay the run command will use):",
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
    lines.append(
        "  OpenAI-compatible base URL: "
        f"{meta.get('openai_compatible_base_url')}"
    )
    lines.append(f"  Model: {meta.get('model')}")
    lines.append(f"  Provider model id: {meta.get('provider_model_id')}")
    return lines
