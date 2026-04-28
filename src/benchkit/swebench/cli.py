from __future__ import annotations

import argparse
from pathlib import Path

from benchkit.common.config import load_run_config
from benchkit.swebench.appendix import generate_appendix_files
from benchkit.swebench.db import import_appendix_csv_to_sqlite
from benchkit.swebench.dataset import filter_instances, load_instances
from benchkit.swebench.hf_export import DEFAULT_DATASET, export_hf_swebench_multilingual
from benchkit.swebench.model_mapper import resolve_model_name
from benchkit.swebench.opencode_config_metadata import (
    build_opencode_run_metadata,
    format_opencode_plan_lines,
)
from benchkit.swebench.runner import execute_run


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
        "--appendix-output-dir",
        type=Path,
        default=None,
        help="If set, auto-generate appendix files for this run into the given directory",
    )
    export_parser = subparsers.add_parser(
        "export-hf",
        help="Export SWE-bench Multilingual split from HF to local JSONL",
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
        "--dataset",
        default=DEFAULT_DATASET,
        help=f"HF dataset path (default: {DEFAULT_DATASET})",
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
            args.appendix_output_dir,
        )
        return

    if args.command == "export-hf":
        run_export_hf(
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

    raise RuntimeError(f"Unsupported command: {args.command}")


def run_plan(config_path: Path, show: int, mode: str | None = None) -> None:
    try:
        config = load_run_config(config_path, mode=mode)
    except ValueError as exc:
        raise SystemExit(f"Config error: {exc}") from exc
    all_instances = load_instances(config.dataset_path)
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
    print("Benchmark TOML model manifest (run.json payload; not OpenCode sampling):")
    print(f"  Temperature: {config.model.temperature}")
    print(f"  Max tokens: {config.model.max_tokens}")
    print(f"  Seed: {config.model.seed if config.model.seed is not None else 'none'}")
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


def run_execute(
    config_path: Path,
    mode: str | None,
    dry_run: bool,
    attempts: int | None,
    max_workers: int | None,
    appendix_output_dir: Path | None = None,
) -> None:
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
    if appendix_output_dir is not None:
        print()
        print("Generating appendix files...")
        run_appendix(run_roots=[result.run_root], output_dir=appendix_output_dir)


def run_export_hf(
    output: Path,
    split: str,
    dataset: str,
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
    if instance_ids_file:
        include_instance_ids = include_instance_ids + _load_line_items(instance_ids_file)
    try:
        stats = export_hf_swebench_multilingual(
            output_path=output,
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
