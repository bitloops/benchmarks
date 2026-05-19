from __future__ import annotations

from dataclasses import dataclass, field
import sys
import tomllib
from pathlib import Path
from typing import Any

DEFAULT_ATTEMPTS = 1
DEFAULT_MAX_WORKERS = 2
DEFAULT_TIMEOUT_SECONDS = 900
DEFAULT_WORKSPACE_TIMEOUT_SECONDS = 600
DEFAULT_PREPARE_WORKSPACE = False
DEFAULT_TEMPERATURE = 0.0
DEFAULT_MAX_TOKENS = 32000
DEFAULT_SWEBENCH_MULTILINGUAL_DATASET_NAME = "SWE-bench/SWE-bench_Multilingual"
DEFAULT_SWEBENCH_PRO_DATASET_NAME = "ScaleAI/SWE-bench_Pro"
DEFAULT_CONTEXTBENCH_DATASET_NAME = "Contextbench/ContextBench"
DEFAULT_PROMPT_PROTOCOL = "minimal"


def _default_agent_python_bin() -> str:
    executable = str(getattr(sys, "executable", "") or "").strip()
    if executable:
        return executable
    return "python3"


@dataclass(slots=True)
class ModelConfig:
    provider: str
    name: str
    temperature: float = DEFAULT_TEMPERATURE
    max_tokens: int = DEFAULT_MAX_TOKENS
    seed: int | None = None


@dataclass(slots=True)
class AgentConfig:
    id: str
    command: list[str] = field(default_factory=list)
    extra_args: list[str] = field(default_factory=list)


@dataclass(slots=True)
class RunConfig:
    config_mode: str | None
    benchmark: str
    dataset_path: Path
    split: str | None
    language: str | None
    condition: str
    include_repos: list[str]
    include_instance_ids: list[str]
    max_instances: int | None
    attempts: int
    max_workers: int
    timeout_seconds: int
    output_root: Path
    prepare_workspace: bool
    workspace_isolation_mode: str
    bitloops_enabled: bool
    bitloops_sandbox_mode: str
    repo_url_template: str
    git_bin: str
    workspace_root: Path | None
    workspace_timeout_seconds: int
    agent: AgentConfig
    model: ModelConfig
    model_map: dict[str, dict[str, str]]
    prompt_context: str | None
    evaluation: "EvaluationConfig"
    source_path: Path
    prompt_protocol: str = DEFAULT_PROMPT_PROTOCOL


@dataclass(slots=True)
class EvaluationConfig:
    enabled: bool = False
    python_bin: str = "python3"
    swebench_repo: Path | None = None
    dataset_name: str | None = None
    split: str | None = None
    max_workers: int = 4
    timeout_seconds: int = 7200
    command_template: list[str] = field(default_factory=list)
    result_json_path_template: str | None = None
    extra_args: list[str] = field(default_factory=list)
    pro_dockerhub_username: str = "jefzda"
    pro_scripts_dir: Path | None = None
    pro_use_local_docker: bool = True
    pro_block_network: bool = False
    pro_docker_platform: str | None = None
    contextbench_repo: Path | None = None
    contextbench_cache_dir: Path | None = None


def _require(mapping: dict[str, Any], key: str, section: str) -> Any:
    if key not in mapping:
        raise ValueError(f"Missing required key '{key}' in section '{section}'")
    return mapping[key]


def load_run_config(config_path: Path, mode: str | None = None) -> RunConfig:
    config_path = config_path.resolve()
    data = tomllib.loads(config_path.read_text(encoding="utf-8"))
    data = _apply_config_preset(data)
    data = _apply_mode_overlay(data, mode=mode)

    run = data.get("run", {})
    agent = data.get("agent", {})
    model = data.get("model", {})
    model_map_raw = data.get("model_map", {})
    evaluation = data.get("evaluation", {})

    benchmark = str(_require(run, "benchmark", "run"))
    dataset_path = Path(_require(run, "dataset_path", "run"))
    output_root = Path(run.get("output_root", "runs"))
    split = run.get("split")
    language = run.get("language")
    condition = str(run.get("condition", "baseline")).strip().lower() or "baseline"
    include_repos = _as_str_list(run.get("include_repos", []), "run.include_repos")
    include_instance_ids = _as_str_list(
        run.get("include_instance_ids", []),
        "run.include_instance_ids",
    )
    include_instance_ids.extend(
        _load_instance_ids_file(
            run.get("instance_ids_file"),
            config_dir=config_path.parent,
        )
    )
    include_instance_ids = sorted(set(include_instance_ids))
    max_instances = run.get("max_instances")
    attempts = int(run.get("attempts", DEFAULT_ATTEMPTS))
    max_workers = int(run.get("max_workers", DEFAULT_MAX_WORKERS))
    timeout_seconds = int(run.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS))
    prepare_workspace = bool(run.get("prepare_workspace", DEFAULT_PREPARE_WORKSPACE))
    workspace_isolation_mode_raw = str(run.get("workspace_isolation_mode", "")).strip().lower()
    bitloops_sandbox_mode_raw = str(run.get("bitloops_sandbox_mode", "")).strip().lower()
    repo_url_template = str(
        run.get("repo_url_template", "https://github.com/{repo}.git")
    ).strip()
    git_bin = str(run.get("git_bin", "git")).strip() or "git"
    workspace_root_raw = run.get("workspace_root")
    workspace_root = None
    if isinstance(workspace_root_raw, str) and workspace_root_raw.strip():
        workspace_root = Path(workspace_root_raw.strip())
    workspace_timeout_seconds = int(run.get("workspace_timeout_seconds", DEFAULT_WORKSPACE_TIMEOUT_SECONDS))

    if attempts < 1:
        raise ValueError("run.attempts must be >= 1")
    if max_workers < 1:
        raise ValueError("run.max_workers must be >= 1")
    if timeout_seconds < 1:
        raise ValueError("run.timeout_seconds must be >= 1")
    if workspace_timeout_seconds < 1:
        raise ValueError("run.workspace_timeout_seconds must be >= 1")
    if "{repo}" not in repo_url_template:
        raise ValueError("run.repo_url_template must include '{repo}' placeholder")

    agent_cfg = AgentConfig(
        id=str(_require(agent, "id", "agent")),
        command=list(agent.get("command", [])),
        extra_args=list(agent.get("extra_args", [])),
    )

    model_cfg = ModelConfig(
        provider=str(_require(model, "provider", "model")),
        name=str(_require(model, "name", "model")),
        temperature=float(model.get("temperature", DEFAULT_TEMPERATURE)),
        max_tokens=int(model.get("max_tokens", DEFAULT_MAX_TOKENS)),
        seed=None if model.get("seed") is None else int(model.get("seed")),
    )
    prompt_context_raw = run.get("prompt_context")
    prompt_context = (
        str(prompt_context_raw).strip() if isinstance(prompt_context_raw, str) and prompt_context_raw.strip() else None
    )
    prompt_protocol = str(
        run.get("prompt_protocol", DEFAULT_PROMPT_PROTOCOL)
    ).strip().lower() or DEFAULT_PROMPT_PROTOCOL
    if prompt_protocol != DEFAULT_PROMPT_PROTOCOL:
        raise ValueError("run.prompt_protocol must be 'minimal'")
    bitloops_enabled = _detect_bitloops_enabled(condition=condition, agent=agent_cfg)
    workspace_isolation_mode = _resolve_workspace_isolation_mode(
        requested=workspace_isolation_mode_raw,
        bitloops_enabled=bitloops_enabled,
    )
    bitloops_sandbox_mode = _resolve_bitloops_sandbox_mode(
        requested=bitloops_sandbox_mode_raw,
        bitloops_enabled=bitloops_enabled,
    )

    model_map = _parse_model_map(model_map_raw)
    evaluation_cfg = _parse_evaluation_config(evaluation, split=split, benchmark=benchmark)

    return RunConfig(
        config_mode=_normalize_config_mode(mode),
        benchmark=benchmark,
        dataset_path=dataset_path,
        split=split if split is None else str(split),
        language=language.lower() if isinstance(language, str) else None,
        condition=condition,
        include_repos=include_repos,
        include_instance_ids=include_instance_ids,
        max_instances=None if max_instances is None else int(max_instances),
        attempts=attempts,
        max_workers=max_workers,
        timeout_seconds=timeout_seconds,
        output_root=output_root,
        prepare_workspace=prepare_workspace,
        workspace_isolation_mode=workspace_isolation_mode,
        bitloops_enabled=bitloops_enabled,
        bitloops_sandbox_mode=bitloops_sandbox_mode,
        repo_url_template=repo_url_template,
        git_bin=git_bin,
        workspace_root=workspace_root,
        workspace_timeout_seconds=workspace_timeout_seconds,
        agent=agent_cfg,
        model=model_cfg,
        prompt_context=prompt_context,
        prompt_protocol=prompt_protocol,
        model_map=model_map,
        evaluation=evaluation_cfg,
        source_path=config_path,
    )


def _normalize_config_mode(mode: str | None) -> str | None:
    if mode is None:
        return None
    normalized = str(mode).strip()
    return normalized or None


def _apply_config_preset(data: dict[str, Any]) -> dict[str, Any]:
    raw_preset = data.get("preset")
    if raw_preset is None:
        return data
    if not isinstance(raw_preset, str):
        raise ValueError("preset must be a string")

    preset_name = raw_preset.strip().lower()
    presets = _config_presets()
    preset = presets.get(preset_name)
    if preset is None:
        available = ", ".join(sorted(presets.keys()))
        raise ValueError(
            f"Unknown config preset '{preset_name}'. Available presets: {available}"
        )
    return _merge_config_data(preset, data)


def _config_presets() -> dict[str, dict[str, Any]]:
    common_run_multilingual = {
        "benchmark": "swebench_multilingual",
        "split": "test",
        "language": "rust",
        "condition": "baseline",
        "include_repos": [],
        "include_instance_ids": [],
        "attempts": DEFAULT_ATTEMPTS,
        "max_workers": DEFAULT_MAX_WORKERS,
        "timeout_seconds": DEFAULT_TIMEOUT_SECONDS,
        "output_root": "runs",
        "prepare_workspace": True,
        "repo_url_template": "https://github.com/{repo}.git",
        "git_bin": "git",
        "workspace_timeout_seconds": DEFAULT_WORKSPACE_TIMEOUT_SECONDS,
    }
    common_evaluation_multilingual = {
        "enabled": True,
        "python_bin": "./.venv/bin/python",
        "dataset_name": DEFAULT_SWEBENCH_MULTILINGUAL_DATASET_NAME,
        "split": "test",
        "max_workers": 4,
        "timeout_seconds": 7200,
        "command_template": [],
        "extra_args": [],
    }
    common_run_pro = {
        "benchmark": "swebench_pro",
        "split": "test",
        "language": "javascript",
        "condition": "baseline",
        "include_repos": [],
        "include_instance_ids": [],
        "attempts": DEFAULT_ATTEMPTS,
        "max_workers": DEFAULT_MAX_WORKERS,
        "timeout_seconds": DEFAULT_TIMEOUT_SECONDS,
        "output_root": "runs",
        "prepare_workspace": True,
        "repo_url_template": "https://github.com/{repo}.git",
        "git_bin": "git",
        "workspace_timeout_seconds": DEFAULT_WORKSPACE_TIMEOUT_SECONDS,
    }
    common_evaluation_pro = {
        "enabled": True,
        "python_bin": "./.venv/bin/python",
        "dataset_name": DEFAULT_SWEBENCH_PRO_DATASET_NAME,
        "split": "test",
        "max_workers": 4,
        "timeout_seconds": 7200,
        "command_template": [],
        "extra_args": [],
        "pro_dockerhub_username": "jefzda",
        "pro_use_local_docker": True,
    }
    common_run_contextbench = {
        "benchmark": "contextbench_verified",
        "split": "train",
        "language": None,
        "condition": "baseline",
        "include_repos": [],
        "include_instance_ids": [],
        "attempts": DEFAULT_ATTEMPTS,
        "max_workers": DEFAULT_MAX_WORKERS,
        "timeout_seconds": DEFAULT_TIMEOUT_SECONDS,
        "output_root": "runs",
        "prepare_workspace": True,
        "repo_url_template": "https://github.com/{repo}.git",
        "git_bin": "git",
        "workspace_timeout_seconds": DEFAULT_WORKSPACE_TIMEOUT_SECONDS,
    }
    common_evaluation_contextbench = {
        "enabled": True,
        "python_bin": "./.venv/bin/python",
        "dataset_name": DEFAULT_CONTEXTBENCH_DATASET_NAME,
        "split": "train",
        "max_workers": 1,
        "timeout_seconds": 7200,
        "command_template": [],
        "extra_args": [],
        "contextbench_repo": "third_party/ContextBench",
        "contextbench_cache_dir": "third_party/ContextBench/repos",
    }
    return {
        "codex": {
            "run": {
                **common_run_multilingual,
                "timeout_seconds": 1200,
            },
            "agent": {
                "id": "codex",
                "command": [_default_agent_python_bin(), "-m", "benchkit.swebench.agents.codex.wrapper"],
                "extra_args": [],
            },
            "model": {
                "provider": "openai",
                "temperature": DEFAULT_TEMPERATURE,
                "max_tokens": DEFAULT_MAX_TOKENS,
            },
            "evaluation": common_evaluation_multilingual,
            "modes": {
                "baseline": {
                    "run": {
                        "condition": "baseline",
                        "bitloops_sandbox_mode": "disabled",
                    },
                    "agent": {"extra_args": []},
                },
                "with_bitloops": {
                    "run": {
                        "condition": "with_bitloops",
                    },
                    "agent": {"extra_args": ["--bitloops-init"]},
                },
            },
        },
        "opencode": {
            "run": {
                **common_run_multilingual,
                "timeout_seconds": 900,
                "workspace_timeout_seconds": 1800,
            },
            "agent": {
                "id": "opencode",
                "command": [_default_agent_python_bin(), "-m", "benchkit.swebench.agents.opencode.wrapper"],
                "extra_args": [],
            },
            "model": {
                "provider": "fireworks-ai",
                "temperature": DEFAULT_TEMPERATURE,
                "max_tokens": DEFAULT_MAX_TOKENS,
            },
            "evaluation": {
                **common_evaluation_multilingual,
                "max_workers": 2,
            },
            "modes": {
                "baseline": {
                    "run": {
                        "condition": "baseline",
                        "bitloops_sandbox_mode": "disabled",
                    },
                    "agent": {"extra_args": []},
                },
                "with_bitloops": {
                    "run": {
                        "condition": "with_bitloops",
                        "timeout_seconds": 1500,
                        "bitloops_sandbox_mode": "per_task_daemon",
                    },
                    "agent": {
                        "extra_args": [
                            "--bitloops-init",
                            "--bitloops-embeddings-runtime",
                            "platform",
                            "--bitloops-summary-mode",
                            "off",
                        ],
                    },
                },
            },
        },
        "codex_pro": {
            "run": {
                **common_run_pro,
                "timeout_seconds": 1200,
            },
            "agent": {
                "id": "codex",
                "command": [_default_agent_python_bin(), "-m", "benchkit.swebench.agents.codex.wrapper"],
                "extra_args": [],
            },
            "model": {
                "provider": "openai",
                "temperature": DEFAULT_TEMPERATURE,
                "max_tokens": DEFAULT_MAX_TOKENS,
            },
            "evaluation": common_evaluation_pro,
            "modes": {
                "baseline": {
                    "run": {
                        "condition": "baseline",
                        "bitloops_sandbox_mode": "disabled",
                    },
                    "agent": {"extra_args": []},
                },
                "with_bitloops": {
                    "run": {
                        "condition": "with_bitloops",
                    },
                    "agent": {"extra_args": ["--bitloops-init"]},
                },
            },
        },
        "opencode_pro": {
            "run": {
                **common_run_pro,
                "timeout_seconds": 900,
                "workspace_timeout_seconds": 1800,
            },
            "agent": {
                "id": "opencode",
                "command": [_default_agent_python_bin(), "-m", "benchkit.swebench.agents.opencode.wrapper"],
                "extra_args": [],
            },
            "model": {
                "provider": "fireworks-ai",
                "temperature": DEFAULT_TEMPERATURE,
                "max_tokens": DEFAULT_MAX_TOKENS,
            },
            "evaluation": {
                **common_evaluation_pro,
                "max_workers": 2,
            },
            "modes": {
                "baseline": {
                    "run": {
                        "condition": "baseline",
                        "bitloops_sandbox_mode": "disabled",
                    },
                    "agent": {"extra_args": []},
                },
                "with_bitloops": {
                    "run": {
                        "condition": "with_bitloops",
                        "timeout_seconds": 1500,
                        "bitloops_sandbox_mode": "per_task_daemon",
                    },
                    "agent": {
                        "extra_args": [
                            "--bitloops-init",
                            "--bitloops-embeddings-runtime",
                            "platform",
                            "--bitloops-summary-mode",
                            "off",
                        ],
                    },
                },
            },
        },
        "codex_contextbench": {
            "run": {
                **common_run_contextbench,
                "timeout_seconds": 1200,
            },
            "agent": {
                "id": "codex",
                "command": [_default_agent_python_bin(), "-m", "benchkit.swebench.agents.codex.wrapper"],
                "extra_args": [],
            },
            "model": {
                "provider": "openai",
                "temperature": DEFAULT_TEMPERATURE,
                "max_tokens": DEFAULT_MAX_TOKENS,
            },
            "evaluation": common_evaluation_contextbench,
            "modes": {
                "baseline": {
                    "run": {
                        "condition": "baseline",
                        "bitloops_sandbox_mode": "disabled",
                    },
                    "agent": {"extra_args": []},
                },
                "with_bitloops": {
                    "run": {
                        "condition": "with_bitloops",
                    },
                    "agent": {"extra_args": ["--bitloops-init"]},
                },
            },
        },
        "opencode_contextbench": {
            "run": {
                **common_run_contextbench,
                "timeout_seconds": 900,
                "workspace_timeout_seconds": 1800,
            },
            "agent": {
                "id": "opencode",
                "command": [_default_agent_python_bin(), "-m", "benchkit.swebench.agents.opencode.wrapper"],
                "extra_args": [],
            },
            "model": {
                "provider": "fireworks-ai",
                "temperature": DEFAULT_TEMPERATURE,
                "max_tokens": DEFAULT_MAX_TOKENS,
            },
            "evaluation": {
                **common_evaluation_contextbench,
                "max_workers": 1,
            },
            "modes": {
                "baseline": {
                    "run": {
                        "condition": "baseline",
                        "bitloops_sandbox_mode": "disabled",
                    },
                    "agent": {"extra_args": []},
                },
                "with_bitloops": {
                    "run": {
                        "condition": "with_bitloops",
                        "timeout_seconds": 1500,
                        "bitloops_sandbox_mode": "per_task_daemon",
                    },
                    "agent": {
                        "extra_args": [
                            "--bitloops-init",
                            "--bitloops-embeddings-runtime",
                            "platform",
                            "--bitloops-summary-mode",
                            "off",
                        ],
                    },
                },
            },
        },
    }


def _merge_config_data(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    for key, value in base.items():
        if isinstance(value, dict):
            merged[key] = _merge_config_data(value, {})
        elif isinstance(value, list):
            merged[key] = list(value)
        else:
            merged[key] = value

    for key, value in overlay.items():
        current = merged.get(key)
        if isinstance(current, dict) and isinstance(value, dict):
            merged[key] = _merge_config_data(current, value)
        elif isinstance(value, dict):
            merged[key] = _merge_config_data(value, {})
        elif isinstance(value, list):
            merged[key] = list(value)
        else:
            merged[key] = value
    return merged


def _apply_mode_overlay(
    data: dict[str, Any],
    *,
    mode: str | None,
) -> dict[str, Any]:
    selected_mode = _normalize_config_mode(mode)
    if selected_mode is None:
        return data

    modes = data.get("modes", {})
    if not isinstance(modes, dict):
        raise ValueError("modes must be a table of mode overlays")
    overlay = modes.get(selected_mode)
    if not isinstance(overlay, dict):
        available = ", ".join(sorted(str(key) for key in modes.keys())) or "none"
        raise ValueError(
            f"Unknown config mode '{selected_mode}'. Available modes: {available}"
        )

    merged = {
        key: value
        for key, value in data.items()
        if key != "modes"
    }
    for section_name in ("run", "agent", "model", "evaluation", "model_map"):
        section_overlay = overlay.get(section_name)
        if section_overlay is None:
            continue
        if not isinstance(section_overlay, dict):
            raise ValueError(f"modes.{selected_mode}.{section_name} must be a table")
        base_section = merged.get(section_name, {})
        if not isinstance(base_section, dict):
            raise ValueError(f"{section_name} must be a table")
        merged[section_name] = _merge_config_data(base_section, section_overlay)
    return merged


def _detect_bitloops_enabled(*, condition: str, agent: AgentConfig) -> bool:
    if condition == "with_bitloops":
        return True
    return any(str(arg).strip() == "--bitloops-init" for arg in agent.extra_args)


def _resolve_workspace_isolation_mode(*, requested: str, bitloops_enabled: bool) -> str:
    if requested:
        if requested not in {"shared_repo_commit", "task_scoped", "attempt_scoped"}:
            raise ValueError(
                "run.workspace_isolation_mode must be one of: "
                "'shared_repo_commit', 'task_scoped', 'attempt_scoped'"
            )
        return requested
    if bitloops_enabled:
        return "task_scoped"
    return "shared_repo_commit"


def _resolve_bitloops_sandbox_mode(*, requested: str, bitloops_enabled: bool) -> str:
    if requested:
        if requested not in {"disabled", "shared_daemon", "per_task_daemon"}:
            raise ValueError(
                "run.bitloops_sandbox_mode must be one of: "
                "'disabled', 'shared_daemon', 'per_task_daemon'"
            )
        return requested
    if bitloops_enabled:
        return "per_task_daemon"
    return "disabled"


def _parse_model_map(raw: Any) -> dict[str, dict[str, str]]:
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ValueError("model_map must be a table of tables, e.g. [model_map.claude_code]")

    output: dict[str, dict[str, str]] = {}
    for agent_key, mapping in raw.items():
        if not isinstance(mapping, dict):
            raise ValueError(
                f"model_map.{agent_key} must be a table of string values "
                "(canonical model name -> agent model id)"
            )
        normalized_agent = str(agent_key).strip().lower()
        pairs: dict[str, str] = {}
        for canonical_name, mapped_name in mapping.items():
            if not isinstance(mapped_name, str):
                raise ValueError(
                    f"model_map.{agent_key}.{canonical_name} must be a string model id"
                )
            key = str(canonical_name).strip()
            value = mapped_name.strip()
            if not key or not value:
                continue
            pairs[key] = value
        output[normalized_agent] = pairs
    return output


def _as_str_list(value: Any, name: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError(f"{name} must be a list of strings")
    output: list[str] = []
    for item in value:
        if not isinstance(item, str):
            raise ValueError(f"{name} must contain only strings")
        cleaned = item.strip()
        if cleaned:
            output.append(cleaned)
    return output


def _load_instance_ids_file(value: Any, config_dir: Path) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, str):
        raise ValueError("run.instance_ids_file must be a string path")
    cleaned = value.strip()
    if not cleaned:
        return []
    path = Path(cleaned)
    if not path.is_absolute():
        path = config_dir / path
    path = path.resolve()
    if not path.exists():
        raise FileNotFoundError(f"instance IDs file not found: {path}")

    items: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        value = line.strip()
        if not value or value.startswith("#"):
            continue
        items.append(value)
    return items


def _parse_evaluation_config(
    raw: Any,
    split: Any,
    benchmark: str,
) -> EvaluationConfig:
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ValueError("evaluation must be a TOML table")

    default_dataset_name = None
    if benchmark == "swebench_multilingual":
        default_dataset_name = DEFAULT_SWEBENCH_MULTILINGUAL_DATASET_NAME
    if benchmark == "swebench_pro":
        default_dataset_name = DEFAULT_SWEBENCH_PRO_DATASET_NAME
    if benchmark == "contextbench_verified":
        default_dataset_name = DEFAULT_CONTEXTBENCH_DATASET_NAME

    swebench_repo_raw = raw.get("swebench_repo")
    swebench_repo = None
    if isinstance(swebench_repo_raw, str) and swebench_repo_raw.strip():
        swebench_repo = Path(swebench_repo_raw.strip())
    contextbench_repo_raw = raw.get("contextbench_repo")
    contextbench_repo = None
    if isinstance(contextbench_repo_raw, str) and contextbench_repo_raw.strip():
        contextbench_repo = Path(contextbench_repo_raw.strip())
    contextbench_cache_dir_raw = raw.get("contextbench_cache_dir")
    contextbench_cache_dir = None
    if isinstance(contextbench_cache_dir_raw, str) and contextbench_cache_dir_raw.strip():
        contextbench_cache_dir = Path(contextbench_cache_dir_raw.strip())

    cfg = EvaluationConfig(
        enabled=bool(raw.get("enabled", False)),
        python_bin=str(raw.get("python_bin", "python3")),
        swebench_repo=swebench_repo,
        dataset_name=(
            str(raw.get("dataset_name", "")).strip() or default_dataset_name
        ),
        split=(
            str(raw.get("split", "")).strip()
            or (str(split).strip() if split is not None else None)
        ),
        max_workers=int(raw.get("max_workers", 4)),
        timeout_seconds=int(raw.get("timeout_seconds", 7200)),
        command_template=list(raw.get("command_template", [])),
        result_json_path_template=(
            str(raw.get("result_json_path_template", "")).strip() or None
        ),
        extra_args=list(raw.get("extra_args", [])),
        pro_dockerhub_username=(
            str(raw.get("pro_dockerhub_username", "jefzda")).strip() or "jefzda"
        ),
        pro_scripts_dir=(
            Path(str(raw.get("pro_scripts_dir", "")).strip())
            if str(raw.get("pro_scripts_dir", "")).strip()
            else None
        ),
        pro_use_local_docker=bool(raw.get("pro_use_local_docker", True)),
        pro_block_network=bool(raw.get("pro_block_network", False)),
        pro_docker_platform=(
            str(raw.get("pro_docker_platform", "")).strip() or None
        ),
        contextbench_repo=contextbench_repo,
        contextbench_cache_dir=contextbench_cache_dir,
    )
    if cfg.max_workers < 1:
        raise ValueError("evaluation.max_workers must be >= 1")
    if cfg.timeout_seconds < 1:
        raise ValueError("evaluation.timeout_seconds must be >= 1")
    if cfg.enabled and not cfg.command_template and not cfg.dataset_name:
        raise ValueError(
            "evaluation.dataset_name is required when evaluation.enabled=true "
            "unless evaluation.command_template is provided"
        )
    return cfg
