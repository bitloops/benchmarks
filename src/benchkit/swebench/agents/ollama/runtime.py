from __future__ import annotations

import json
import os
from copy import deepcopy
from pathlib import Path


def _repo_root() -> Path:
    import benchkit

    return Path(benchkit.__file__).resolve().parents[2]


def default_ollama_json_path() -> Path:
    return _repo_root() / "configs" / "ollama" / "ollama.json"


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


def deep_merge_ollama_dicts(base: dict[str, object], overlay: dict[str, object]) -> dict[str, object]:
    merged: dict[str, object] = deepcopy(base)
    for key, value in overlay.items():
        current = merged.get(key)
        if isinstance(current, dict) and isinstance(value, dict):
            merged[key] = deep_merge_ollama_dicts(current, value)
            continue
        merged[key] = deepcopy(value)
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


def resolve_ollama_timeout_seconds(payload: dict[str, object], runtime_config: dict[str, object]) -> int:
    env_timeout = os.environ.get("OLLAMA_TIMEOUT_SECONDS", "").strip()
    env_value = 0
    if env_timeout:
        try:
            env_value = int(env_timeout)
        except ValueError:
            env_value = 0

    config_value = 0
    raw_config_timeout = runtime_config.get("timeout_seconds")
    try:
        config_value = int(raw_config_timeout)
    except (TypeError, ValueError):
        config_value = 0

    run = payload.get("run", {})
    run_value = 0
    if isinstance(run, dict):
        raw_timeout = run.get("timeout_seconds")
        try:
            run_value = int(raw_timeout)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            run_value = 0

    return max(env_value, config_value, run_value, 900)


def resolve_ollama_base_url(runtime_config: dict[str, object]) -> str:
    configured = str(runtime_config.get("base_url", "")).strip()
    env_override = os.environ.get("OLLAMA_BASE_URL", "").strip()
    base_url = env_override or configured or "http://localhost:11434"
    return base_url.rstrip("/")


def resolve_ollama_model_name(payload: dict[str, object], runtime_config: dict[str, object]) -> str:
    model = payload.get("model", {})
    payload_name = ""
    if isinstance(model, dict):
        payload_name = str(model.get("name", "")).strip()
    env_name = os.environ.get("OLLAMA_MODEL", "").strip()
    config_name = str(runtime_config.get("model", "")).strip()
    return payload_name or env_name or config_name or "deepseek-v4-flash:cloud"


def resolve_ollama_auth_bearer_token() -> str | None:
    auth_token = os.environ.get("OLLAMA_AUTH_TOKEN", "").strip()
    return auth_token or None


def looks_like_cloud_model(model_name: str) -> bool:
    return model_name.strip().lower().endswith(":cloud")


def resolve_ollama_max_num_predict(model_name: str, runtime_config: dict[str, object]) -> int | None:
    env_value = os.environ.get("OLLAMA_MAX_PREDICT", "").strip()
    if env_value:
        try:
            parsed = int(env_value)
            return parsed if parsed > 0 else None
        except ValueError:
            return None

    raw_runtime_max = runtime_config.get("max_num_predict")
    try:
        parsed_runtime_max = int(raw_runtime_max)
    except (TypeError, ValueError):
        parsed_runtime_max = 0
    if parsed_runtime_max > 0:
        return parsed_runtime_max

    if looks_like_cloud_model(model_name):
        return 4096
    return None


def build_ollama_request_options(
    *,
    payload: dict[str, object],
    runtime_config: dict[str, object],
    model_name: str,
) -> dict[str, object]:
    options: dict[str, object] = {}
    model_cfg = payload.get("model", {})
    if isinstance(model_cfg, dict):
        temperature = model_cfg.get("temperature")
        max_tokens = model_cfg.get("max_tokens")
        seed = model_cfg.get("seed")
        if isinstance(temperature, (int, float)):
            options["temperature"] = float(temperature)
        if isinstance(max_tokens, int) and max_tokens > 0:
            options["num_predict"] = max_tokens
        if isinstance(seed, int):
            options["seed"] = seed

    runtime_options = runtime_config.get("options")
    if isinstance(runtime_options, dict):
        merged_options = dict(runtime_options)
        merged_options.update(options)
        options = merged_options

    max_num_predict = resolve_ollama_max_num_predict(model_name, runtime_config)
    if max_num_predict is not None:
        raw_predict = options.get("num_predict")
        predict_value = 0
        try:
            predict_value = int(raw_predict) if raw_predict is not None else 0
        except (TypeError, ValueError):
            predict_value = 0
        if predict_value <= 0 or predict_value > max_num_predict:
            options["num_predict"] = max_num_predict

    return options
