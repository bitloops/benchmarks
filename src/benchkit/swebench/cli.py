from __future__ import annotations

import argparse
import ast
import contextlib
import os
import shutil
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from benchkit.common.config import RunConfig, load_run_config
from benchkit.swebench.appendix import generate_appendix_files
from benchkit.swebench.agents.codex.config import (
    build_codex_run_metadata,
    format_codex_plan_lines,
)
from benchkit.swebench.db import import_appendix_csv_to_sqlite
from benchkit.swebench.dataset import (
    BENCHMARK_MULTILINGUAL,
    BENCHMARK_PRO,
    filter_instances,
    load_instances,
)
from benchkit.swebench.hf_export import default_dataset_for_benchmark, export_hf_dataset
from benchkit.swebench.model_mapper import resolve_model_name
from benchkit.swebench.agents.opencode.config import (
    build_ollama_provider_run_metadata,
    build_opencode_run_metadata,
    format_ollama_provider_plan_lines,
    format_opencode_plan_lines,
)
from benchkit.swebench.runner import execute_run

ARTIFACT_RETENTION_POLICIES = (
    "appendix_summary",
    "appendix_transcripts",
    "appendix_only",
    "keep_all",
)
DEFAULT_ARTIFACT_RETENTION_POLICY = "appendix_transcripts"


def _parse_dotenv_assignment(line: str) -> tuple[str, str] | None:
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        return None
    if stripped.startswith("export "):
        stripped = stripped[len("export ") :].lstrip()

    key, separator, value = stripped.partition("=")
    if not separator:
        return None

    key = key.strip()
    if not key or not key.replace("_", "a").isalnum() or key[0].isdigit():
        return None

    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        try:
            parsed = ast.literal_eval(value)
        except (SyntaxError, ValueError):
            value = value[1:-1]
        else:
            value = parsed if isinstance(parsed, str) else str(parsed)
    return key, value


def _candidate_dotenv_paths(
    *,
    config_path: Path | None = None,
    cwd: Path | None = None,
) -> list[Path]:
    candidates: list[Path] = []
    start_dir = (cwd or Path.cwd()).resolve()
    candidates.append(start_dir / ".env")

    if config_path is not None:
        config_parent = config_path.expanduser().resolve().parent
        for parent in (config_parent, *config_parent.parents):
            candidates.append(parent / ".env")

    seen: set[str] = set()
    ordered: list[Path] = []
    for candidate in candidates:
        normalized = str(candidate.resolve())
        if normalized in seen:
            continue
        seen.add(normalized)
        ordered.append(Path(normalized))
    return ordered


def _load_dotenv_file(env_path: Path) -> bool:
    if not env_path.is_file():
        return False

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        parsed = _parse_dotenv_assignment(raw_line)
        if parsed is None:
            continue
        key, value = parsed
        os.environ.setdefault(key, value)
    return True


def _load_cli_dotenv(
    *,
    config_path: Path | None = None,
    cwd: Path | None = None,
) -> list[Path]:
    for candidate in _candidate_dotenv_paths(config_path=config_path, cwd=cwd):
        if _load_dotenv_file(candidate):
            return [candidate]
    return []


def main() -> None:
    parser = argparse.ArgumentParser(description="SWE-bench agent benchmarking CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    plan_parser = subparsers.add_parser("plan", help="Inspect selected instances before running")
    plan_parser.add_argument("--config", required=True, type=Path, help="Path to TOML config")
    plan_parser.add_argument("--mode", default=None, help="Optional config mode overlay")
    plan_parser.add_argument("--show", type=int, default=5, help="How many instance IDs to show")

    run_parser = subparsers.add_parser("run", help="Run agent-based benchmark generation")
    run_parser.add_argument("--config", required=True, type=Path, help="Path to TOML config")
    run_parser.add_argument("--mode", default=None, help="Optional config mode overlay")
    run_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Use noop adapter and produce empty patches",
    )
    run_parser.add_argument(
        "--attempts",
        type=int,
        default=None,
        help="Override attempts from config",
    )
    run_parser.add_argument(
        "--max-workers",
        type=int,
        default=None,
        help="Override run.max_workers from config",
    )
    run_parser.add_argument(
        "--appendix",
        action="store_true",
        help=(
            "Auto-generate appendix under reports/appendix/<agent>_bitloops|baseline_<run_timestamp>/ "
            "(run_id date/time from the harness run)"
        ),
    )
    run_parser.add_argument(
        "--appendix-output-dir",
        type=Path,
        default=None,
        help="If set, write appendix to this path (--appendix-output-dir overrides --appendix)",
    )
    run_parser.add_argument(
        "--artifact-retention-policy",
        choices=ARTIFACT_RETENTION_POLICIES,
        default=None,
        help=(
            "Post-run artifact retention policy override. "
            "Default comes from run.artifact_retention_policy (or appendix_transcripts)."
        ),
    )
    export_parser = subparsers.add_parser(
        "export-hf",
        help="Export SWE-bench split from HF to local JSONL",
    )
    export_parser.add_argument(
        "--output",
        required=True,
        type=Path,
        help="Output JSONL file path",
    )
    export_parser.add_argument(
        "--split",
        default="dev",
        help="HF dataset split to export (e.g. dev, test)",
    )
    export_parser.add_argument(
        "--benchmark",
        default=BENCHMARK_MULTILINGUAL,
        choices=(BENCHMARK_MULTILINGUAL, BENCHMARK_PRO),
        help="Benchmark profile for row normalization/default dataset",
    )
    export_parser.add_argument(
        "--dataset",
        default=None,
        help="HF dataset path (default depends on --benchmark)",
    )
    export_parser.add_argument(
        "--dataset-config",
        default=None,
        help="Optional HF dataset config name",
    )
    export_parser.add_argument(
        "--revision",
        default=None,
        help="Optional HF dataset revision (branch/tag/commit)",
    )
    export_parser.add_argument(
        "--cache-dir",
        type=Path,
        default=None,
        help="Optional HF datasets cache directory",
    )
    export_parser.add_argument(
        "--streaming",
        action="store_true",
        help="Use streaming mode while reading HF dataset",
    )
    export_parser.add_argument(
        "--language",
        default=None,
        help="Optional language filter (e.g. rust)",
    )
    export_parser.add_argument(
        "--repo",
        action="append",
        default=[],
        help="Repeatable repo filter (e.g. tokio-rs/tokio)",
    )
    export_parser.add_argument(
        "--instance-id",
        action="append",
        default=[],
        help="Repeatable instance_id allowlist",
    )
    export_parser.add_argument(
        "--instance-ids-file",
        type=Path,
        default=None,
        help="Optional file containing one instance_id per line",
    )
    export_parser.add_argument(
        "--max-instances",
        type=int,
        default=None,
        help="Optional max number of rows to write after filtering",
    )
    export_parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Overwrite output file if it already exists",
    )
    export_parser.add_argument(
        "--token-env",
        default="HF_TOKEN",
        help="Env var name for HF token (default: HF_TOKEN)",
    )
    appendix_parser = subparsers.add_parser(
        "appendix",
        help="Generate appendix files from completed run artifact folders",
    )
    appendix_parser.add_argument(
        "--run-root",
        action="append",
        required=True,
        type=Path,
        help="Repeatable run root path (contains run_manifest.json)",
    )
    appendix_parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("reports/appendix"),
        help="Directory for generated appendix files",
    )
    db_parser = subparsers.add_parser(
        "db-import",
        help="Import appendix CSV results into a local SQLite database",
    )
    db_parser.add_argument(
        "--appendix-csv",
        action="append",
        required=True,
        type=Path,
        help="Repeatable appendix per-task CSV path",
    )
    db_parser.add_argument(
        "--run-root",
        action="append",
        required=True,
        type=Path,
        help="Repeatable run root path matching each appendix CSV",
    )
    db_parser.add_argument(
        "--db-path",
        type=Path,
        default=Path("reports/benchmarks.sqlite"),
        help="SQLite database file path",
    )
    prune_parser = subparsers.add_parser(
        "prune-artifacts",
        help="Prune heavy benchmark artifacts from existing run roots (dry-run by default)",
    )
    prune_parser.add_argument(
        "--run-root",
        action="append",
        default=[],
        type=Path,
        help="Optional repeatable run root path(s). If omitted, auto-discovers under --runs-root.",
    )
    prune_parser.add_argument(
        "--runs-root",
        type=Path,
        default=Path("runs"),
        help="Base directory used for run auto-discovery when --run-root is omitted",
    )
    prune_parser.add_argument(
        "--benchmark",
        default=None,
        help="Optional benchmark filter for auto-discovery (e.g. swebench_multilingual)",
    )
    prune_parser.add_argument(
        "--older-than-days",
        type=int,
        default=None,
        help="Only prune run roots last modified before N days ago",
    )
    prune_parser.add_argument(
        "--artifact-retention-policy",
        choices=ARTIFACT_RETENTION_POLICIES,
        default=DEFAULT_ARTIFACT_RETENTION_POLICY,
        help=(
            "Retention policy to enforce while pruning historical artifacts "
            "(default: appendix_transcripts)."
        ),
    )
    prune_parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply deletions. Without this flag, prune-artifacts is preview-only.",
    )

    args = parser.parse_args()

    if args.command == "plan":
        run_plan(args.config, args.show, args.mode)
        return

    if args.command == "run":
        run_execute(
            args.config,
            args.mode,
            args.dry_run,
            args.attempts,
            args.max_workers,
            appendix_output_dir=args.appendix_output_dir,
            appendix=args.appendix,
            artifact_retention_policy_override=args.artifact_retention_policy,
        )
        return

    if args.command == "export-hf":
        run_export_hf(
            benchmark=args.benchmark,
            output=args.output,
            split=args.split,
            dataset=args.dataset,
            dataset_config=args.dataset_config,
            revision=args.revision,
            cache_dir=args.cache_dir,
            streaming=args.streaming,
            language=args.language,
            include_repos=args.repo,
            include_instance_ids=args.instance_id,
            instance_ids_file=args.instance_ids_file,
            max_instances=args.max_instances,
            overwrite=args.overwrite,
            token_env=args.token_env,
        )
        return

    if args.command == "appendix":
        run_appendix(args.run_root, args.output_dir)
        return

    if args.command == "db-import":
        run_db_import(args.appendix_csv, args.run_root, args.db_path)
        return

    if args.command == "prune-artifacts":
        run_prune_artifacts(
            run_roots=args.run_root,
            runs_root=args.runs_root,
            benchmark=args.benchmark,
            older_than_days=args.older_than_days,
            artifact_retention_policy=args.artifact_retention_policy,
            apply=args.apply,
        )
        return

    raise RuntimeError(f"Unsupported command: {args.command}")


def run_plan(config_path: Path, show: int, mode: str | None = None) -> None:
    _load_cli_dotenv(config_path=config_path)
    try:
        config = load_run_config(config_path, mode=mode)
    except ValueError as exc:
        raise SystemExit(f"Config error: {exc}") from exc
    all_instances = load_instances(
        config.dataset_path,
        benchmark=config.benchmark,
    )
    selected = filter_instances(
        all_instances,
        language=config.language,
        include_repos=config.include_repos,
        include_instance_ids=config.include_instance_ids,
        max_instances=config.max_instances,
    )
    try:
        model_resolution = resolve_model_name(
            canonical_name=config.model.name,
            agent_id=config.agent.id,
            model_map=config.model_map,
        )
    except ValueError as exc:
        raise SystemExit(f"Model resolution error: {exc}") from exc

    print(f"Benchmark: {config.benchmark}")
    print(f"Config mode: {config.config_mode or 'none'}")
    print(f"Dataset: {config.dataset_path}")
    print(f"Condition: {config.condition}")
    print(f"Agent: {config.agent.id}")
    print(f"Canonical model: {model_resolution.canonical_name}")
    print(f"Resolved model: {model_resolution.resolved_name}")
    print(f"Model resolution source: {model_resolution.source}")
    print(f"Model map key: {model_resolution.map_key}")
    print("Benchmark TOML model manifest (run.json payload; runtime JSON may override):")
    print(f"  Temperature: {config.model.temperature}")
    print(f"  Max tokens: {config.model.max_tokens}")
    print(f"  Seed: {config.model.seed if config.model.seed is not None else 'none'}")
    if str(config.model.provider).strip().lower() == "ollama":
        for line in format_ollama_provider_plan_lines(
            build_ollama_provider_run_metadata(
                config=config,
                resolved_model_name=model_resolution.resolved_name,
            )
        ):
            print(line)
    if config.agent.id == "codex":
        for line in format_codex_plan_lines(build_codex_run_metadata()):
            print(line)
    if config.agent.id == "opencode":
        for line in format_opencode_plan_lines(build_opencode_run_metadata()):
            print(line)
    print(f"Total instances in file: {len(all_instances)}")
    print(f"Selected instances: {len(selected)}")
    print(f"Language filter: {config.language or 'none'}")
    print(f"Repo allowlist: {config.include_repos or 'none'}")
    print(
        "Instance allowlist: "
        f"{len(config.include_instance_ids)} selected"
        if config.include_instance_ids
        else "Instance allowlist: none"
    )
    print(f"Max instances: {config.max_instances}")
    print(f"Attempts: {config.attempts}")
    print(f"Run max workers: {config.max_workers}")
    print()
    print("Sample instance IDs:")
    for item in selected[: max(0, show)]:
        print(f"- {item.instance_id}")


def default_appendix_output_dir(config: RunConfig, run_id: str) -> Path:
    """reports/appendix/<agent>_bitloops|baseline_<YYYYMMDD_HHMMSS>/ from config + run_id."""
    agent = _filesystem_slug(config.agent.id)
    variant = "bitloops" if config.bitloops_enabled else "baseline"
    ts = _appendix_timestamp_segment(run_id)
    return Path("reports/appendix") / f"{agent}_{variant}_{ts}"


def default_transcripts_output_dir(config: RunConfig, run_id: str) -> Path:
    agent_id = "agent"
    variant = "baseline"
    try:
        agent_obj = getattr(config, "agent", None)
        if agent_obj is not None:
            agent_id = str(getattr(agent_obj, "id", "")).strip() or "agent"
        variant = "bitloops" if bool(getattr(config, "bitloops_enabled", False)) else "baseline"
    except Exception:
        pass
    agent = _filesystem_slug(agent_id)
    ts = _appendix_timestamp_segment(run_id)
    return Path("reports/transcripts") / f"{agent}_{variant}_{ts}"


def copy_run_transcripts_to_reports(run_root: Path, transcripts_root: Path) -> Path:
    run_root = run_root.resolve()
    destination = transcripts_root.resolve() / run_root.name
    destination.mkdir(parents=True, exist_ok=True)
    attempts_root = run_root / "attempts"
    if not attempts_root.exists():
        return destination
    for attempt_dir in sorted(attempts_root.glob("attempt-*")):
        if not attempt_dir.is_dir():
            continue
        target_attempt = destination / "attempts" / attempt_dir.name
        target_attempt.mkdir(parents=True, exist_ok=True)
        for filename in (
            "trace.jsonl",
            "predictions.jsonl",
            "evaluation.json",
            "evaluation.tasks.jsonl",
            "evaluation.parsed.json",
            "evaluation.stdout.log",
            "evaluation.stderr.log",
        ):
            source = attempt_dir / filename
            if source.exists():
                shutil.copy2(source, target_attempt / filename)
    return destination


def _filesystem_slug(value: str) -> str:
    lowered = value.strip().lower()
    out: list[str] = []
    for ch in lowered:
        if ch.isalnum():
            out.append(ch)
        elif ch in "._":
            out.append(ch)
        else:
            out.append("_")
    collapsed = "".join(out)
    while "__" in collapsed:
        collapsed = collapsed.replace("__", "_")
    collapsed = collapsed.strip("_")
    return collapsed or "agent"


def _appendix_timestamp_segment(run_id: str) -> str:
    parts = run_id.split("_")
    if (
        len(parts) >= 3
        and len(parts[0]) == 8
        and parts[0].isdigit()
        and len(parts[1]) == 6
        and parts[1].isdigit()
    ):
        return f"{parts[0]}_{parts[1]}"
    return _filesystem_slug(run_id)


def _resolve_artifact_retention_policy(
    *,
    config: object,
    override: str | None,
) -> str:
    if isinstance(override, str) and override.strip():
        policy = override.strip().lower()
    else:
        policy = str(
            getattr(config, "artifact_retention_policy", DEFAULT_ARTIFACT_RETENTION_POLICY)
            or DEFAULT_ARTIFACT_RETENTION_POLICY
        ).strip().lower()
    if policy not in ARTIFACT_RETENTION_POLICIES:
        raise SystemExit(
            "run error: artifact retention policy must be one of "
            f"{', '.join(ARTIFACT_RETENTION_POLICIES)}"
        )
    return policy


def _transcript_run_dirs(run_root: Path, *, transcripts_root_hint: Path | None = None) -> list[Path]:
    run_root = run_root.resolve()
    run_id = run_root.name
    candidates: list[Path] = []

    if transcripts_root_hint is not None:
        hinted = transcripts_root_hint.resolve() / run_id
        if hinted.exists():
            candidates.append(hinted)

    default_base = Path("reports/transcripts").resolve()
    if default_base.exists():
        for group_dir in sorted(default_base.glob("*"), key=str):
            if not group_dir.is_dir():
                continue
            candidate = group_dir / run_id
            if candidate.exists():
                candidates.append(candidate)

    deduped: list[Path] = []
    seen: set[str] = set()
    for path in candidates:
        key = str(path.resolve())
        if key in seen:
            continue
        seen.add(key)
        deduped.append(path.resolve())
    return deduped


def _path_size_bytes(path: Path) -> int:
    if not path.exists():
        return 0
    if path.is_file():
        with contextlib.suppress(OSError):
            return int(path.stat().st_size)
        return 0
    total = 0
    for root, _dirs, files in os.walk(path):
        root_path = Path(root)
        for name in files:
            file_path = root_path / name
            with contextlib.suppress(OSError):
                total += int(file_path.stat().st_size)
    return total


def _delete_path(path: Path, *, apply: bool) -> tuple[bool, int]:
    path = path.resolve()
    if not path.exists():
        return False, 0
    bytes_estimate = _path_size_bytes(path)
    if not apply:
        return True, bytes_estimate
    if path.is_dir():
        shutil.rmtree(path)
    else:
        path.unlink()
    return True, bytes_estimate


def _format_bytes(value: int) -> str:
    size = float(max(0, value))
    units = ("B", "KB", "MB", "GB", "TB")
    unit = units[0]
    for unit in units:
        if size < 1024 or unit == units[-1]:
            break
        size /= 1024
    if unit == "B":
        return f"{int(size)} {unit}"
    return f"{size:.2f} {unit}"


def _enforce_retention_policy_for_run(
    *,
    run_root: Path,
    policy: str,
    apply: bool,
    transcripts_root_hint: Path | None = None,
) -> dict[str, int]:
    run_root = run_root.resolve()
    removed_paths = 0
    reclaimed_bytes = 0

    targets: list[Path] = []
    if policy == "keep_all":
        return {"removed_paths": 0, "reclaimed_bytes": 0}
    if policy == "appendix_only":
        targets.append(run_root)
        targets.extend(_transcript_run_dirs(run_root, transcripts_root_hint=transcripts_root_hint))
    else:
        for child in ("attempts", "workspaces", "bitloops_sandboxes"):
            targets.append(run_root / child)
        if policy == "appendix_summary":
            targets.extend(_transcript_run_dirs(run_root, transcripts_root_hint=transcripts_root_hint))

    deduped: list[Path] = []
    seen: set[str] = set()
    for target in targets:
        key = str(target.resolve())
        if key in seen:
            continue
        seen.add(key)
        deduped.append(target)

    for target in deduped:
        deleted, bytes_estimate = _delete_path(target, apply=apply)
        if deleted:
            removed_paths += 1
            reclaimed_bytes += bytes_estimate

    return {"removed_paths": removed_paths, "reclaimed_bytes": reclaimed_bytes}


def _discover_run_roots_for_prune(
    *,
    run_roots: list[Path],
    runs_root: Path,
    benchmark: str | None,
    older_than_days: int | None,
) -> list[Path]:
    candidates: list[Path] = []
    if run_roots:
        for run_root in run_roots:
            path = run_root.resolve()
            if (path / "run_manifest.json").exists():
                candidates.append(path)
        deduped = sorted({path.resolve() for path in candidates}, key=str)
        return deduped

    base = runs_root.resolve()
    if benchmark:
        search_roots = [base / benchmark]
    else:
        search_roots = [path for path in sorted(base.glob("*"), key=str) if path.is_dir()]

    for search_root in search_roots:
        if not search_root.exists():
            continue
        for manifest in search_root.rglob("run_manifest.json"):
            candidates.append(manifest.parent.resolve())

    if older_than_days is not None:
        if older_than_days < 0:
            raise SystemExit("prune-artifacts error: --older-than-days must be >= 0")
        cutoff = datetime.now(timezone.utc) - timedelta(days=older_than_days)
        cutoff_ts = cutoff.timestamp()
        filtered: list[Path] = []
        for candidate in candidates:
            with contextlib.suppress(OSError):
                if candidate.stat().st_mtime < cutoff_ts:
                    filtered.append(candidate)
        candidates = filtered

    deduped: list[Path] = []
    seen: set[str] = set()
    for candidate in sorted(candidates, key=str):
        key = str(candidate.resolve())
        if key in seen:
            continue
        seen.add(key)
        deduped.append(candidate.resolve())
    return deduped


def run_execute(
    config_path: Path,
    mode: str | None,
    dry_run: bool,
    attempts: int | None,
    max_workers: int | None,
    appendix_output_dir: Path | None = None,
    appendix: bool = False,
    artifact_retention_policy_override: str | None = None,
) -> None:
    _load_cli_dotenv(config_path=config_path)
    try:
        config = load_run_config(config_path, mode=mode)
    except ValueError as exc:
        raise SystemExit(f"Config error: {exc}") from exc
    try:
        result = execute_run(
            config,
            dry_run=dry_run,
            attempts=attempts,
            max_workers=max_workers,
        )
    except ValueError as exc:
        raise SystemExit(f"Run error: {exc}") from exc
    print(f"Run ID: {result.run_id}")
    print(f"Run root: {result.run_root}")
    print(f"Instances: {result.total_instances}")
    print(f"Attempts: {result.attempts}")
    print("Prediction files:")
    for path in result.prediction_files:
        print(f"- {path}")
    print("Trace files:")
    for path in result.trace_files:
        print(f"- {path}")
    print("Evaluation reports:")
    for path in result.evaluation_reports:
        print(f"- {path}")
    if appendix and appendix_output_dir is not None:
        print(
            "Note: --appendix-output-dir takes precedence over --appendix; "
            "using the explicit output directory.",
            file=sys.stderr,
        )
    effective_appendix_dir: Path | None = None
    if appendix_output_dir is not None:
        effective_appendix_dir = appendix_output_dir
    elif appendix:
        effective_appendix_dir = default_appendix_output_dir(config, result.run_id)
    if effective_appendix_dir is not None:
        print()
        print("Generating appendix files...")
        print(f"Appendix output: {effective_appendix_dir}")
        run_appendix(run_roots=[result.run_root], output_dir=effective_appendix_dir)

    retention_policy = _resolve_artifact_retention_policy(
        config=config,
        override=artifact_retention_policy_override,
    )
    print(f"Artifact retention policy: {retention_policy}")

    transcripts_root = default_transcripts_output_dir(config, result.run_id)
    transcripts_destination: Path | None = None
    if retention_policy in {"appendix_transcripts", "keep_all"}:
        transcripts_destination = copy_run_transcripts_to_reports(result.run_root, transcripts_root)
        print(f"Transcripts copied to: {transcripts_destination}")
    else:
        print("Transcripts copied to: skipped (policy does not retain transcripts)")

    cleanup = _enforce_retention_policy_for_run(
        run_root=result.run_root,
        policy=retention_policy,
        apply=True,
        transcripts_root_hint=transcripts_root,
    )
    print(
        "Cleanup summary: "
        f"{cleanup['removed_paths']} path(s), { _format_bytes(cleanup['reclaimed_bytes']) } reclaimed"
    )


def run_prune_artifacts(
    *,
    run_roots: list[Path],
    runs_root: Path,
    benchmark: str | None,
    older_than_days: int | None,
    artifact_retention_policy: str,
    apply: bool,
) -> None:
    _load_cli_dotenv()
    policy = artifact_retention_policy.strip().lower()
    if policy not in ARTIFACT_RETENTION_POLICIES:
        raise SystemExit(
            "prune-artifacts error: --artifact-retention-policy must be one of "
            f"{', '.join(ARTIFACT_RETENTION_POLICIES)}"
        )

    resolved_run_roots = _discover_run_roots_for_prune(
        run_roots=run_roots,
        runs_root=runs_root,
        benchmark=benchmark,
        older_than_days=older_than_days,
    )
    if not resolved_run_roots:
        print("No run roots matched the prune-artifacts filters.")
        return

    print(f"Prune policy: {policy}")
    print(f"Mode: {'apply' if apply else 'preview'}")
    print(f"Run roots: {len(resolved_run_roots)}")

    total_paths = 0
    total_bytes = 0
    start = time.time()
    for run_root in resolved_run_roots:
        result = _enforce_retention_policy_for_run(
            run_root=run_root,
            policy=policy,
            apply=apply,
            transcripts_root_hint=None,
        )
        total_paths += int(result["removed_paths"])
        total_bytes += int(result["reclaimed_bytes"])
        print(
            f"- {run_root}: {result['removed_paths']} path(s), "
            f"{_format_bytes(result['reclaimed_bytes'])}"
        )

    elapsed_ms = int((time.time() - start) * 1000)
    print(
        f"Prune summary: {total_paths} path(s), { _format_bytes(total_bytes) }, "
        f"elapsed_ms={elapsed_ms}"
    )


def run_export_hf(
    benchmark: str,
    output: Path,
    split: str,
    dataset: str | None,
    dataset_config: str | None,
    revision: str | None,
    cache_dir: Path | None,
    streaming: bool,
    language: str | None,
    include_repos: list[str],
    include_instance_ids: list[str],
    instance_ids_file: Path | None,
    max_instances: int | None,
    overwrite: bool,
    token_env: str,
) -> None:
    _load_cli_dotenv()
    resolved_dataset = (
        dataset.strip()
        if isinstance(dataset, str) and dataset.strip()
        else default_dataset_for_benchmark(benchmark)
    )
    if instance_ids_file:
        include_instance_ids = include_instance_ids + _load_line_items(instance_ids_file)
    try:
        stats = export_hf_dataset(
            output_path=output,
            split=split,
            dataset=resolved_dataset,
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
            benchmark=benchmark,
        )
    except RuntimeError as exc:
        raise SystemExit(f"export-hf error: {exc}") from exc

    print(f"Dataset: {stats.dataset}")
    print(f"Dataset config: {stats.dataset_config}")
    print(f"Split: {stats.split}")
    print(f"Revision: {stats.revision}")
    print(f"Language filter: {stats.language_filter}")
    print(f"Rows seen: {stats.total_rows_seen}")
    print(f"Rows written: {stats.rows_written}")
    print(f"Output: {stats.output_path}")


def _load_line_items(path: Path) -> list[str]:
    path = path.resolve()
    if not path.exists():
        raise SystemExit(f"instance IDs file not found: {path}")
    values: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        item = line.strip()
        if not item or item.startswith("#"):
            continue
        values.append(item)
    return values


def run_appendix(run_roots: list[Path], output_dir: Path) -> None:
    _load_cli_dotenv()
    outputs = generate_appendix_files(run_roots=run_roots, output_dir=output_dir)
    print(f"Per-task JSONL: {outputs.per_task_jsonl}")
    print(f"Per-task CSV: {outputs.per_task_csv}")
    print(f"Results CSV: {outputs.results_csv}")
    print(f"Results Markdown: {outputs.results_markdown}")
    print(f"Per-Attempt Breakdown: {outputs.per_attempt_markdown}")
    print(f"Prompt/Tool Breakdown: {outputs.prompt_tool_markdown}")
    print(f"Tool Invocation Log: {outputs.tool_invocation_jsonl}")
    print(f"Tool Invocation Breakdown: {outputs.tool_invocation_markdown}")


def run_db_import(appendix_csvs: list[Path], run_roots: list[Path], db_path: Path) -> None:
    _load_cli_dotenv()
    if len(appendix_csvs) != len(run_roots):
        raise SystemExit(
            "db-import error: --appendix-csv and --run-root must be provided the same number of times"
        )

    total_runs = 0
    total_task_attempts = 0
    for appendix_csv, run_root in zip(appendix_csvs, run_roots):
        result = import_appendix_csv_to_sqlite(
            db_path=db_path,
            appendix_csv=appendix_csv,
            run_root=run_root,
        )
        total_runs += result.inserted_runs
        total_task_attempts += result.inserted_task_attempts
        print(
            f"Imported run {result.run_id}: +{result.inserted_runs} runs, "
            f"+{result.inserted_task_attempts} task_attempts"
        )
    print(f"SQLite DB: {db_path.resolve()}")
    print(f"New runs inserted: {total_runs}")
    print(f"New task_attempts inserted: {total_task_attempts}")


if __name__ == "__main__":
    main()
