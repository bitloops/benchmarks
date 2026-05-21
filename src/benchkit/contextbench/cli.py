from __future__ import annotations

import argparse
from pathlib import Path

from benchkit.swebench.cli import (
    run_appendix,
    run_db_import,
    run_execute,
    run_export_hf,
    run_plan,
    run_prune_artifacts,
)
from benchkit.swebench.dataset import BENCHMARK_CONTEXTBENCH_VERIFIED


def main() -> None:
    parser = argparse.ArgumentParser(description="ContextBench agent benchmarking CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    plan_parser = subparsers.add_parser("plan", help="Inspect selected instances before running")
    plan_parser.add_argument("--config", required=True, type=Path, help="Path to TOML config")
    plan_parser.add_argument("--mode", default=None, help="Optional config mode overlay")
    plan_parser.add_argument("--show", type=int, default=5, help="How many instance IDs to show")

    run_parser = subparsers.add_parser("run", help="Run agent-based benchmark generation")
    run_parser.add_argument("--config", required=True, type=Path, help="Path to TOML config")
    run_parser.add_argument("--mode", default=None, help="Optional config mode overlay")
    run_parser.add_argument("--dry-run", action="store_true", help="Use noop adapter")
    run_parser.add_argument("--attempts", type=int, default=None, help="Override attempts")
    run_parser.add_argument("--max-workers", type=int, default=None, help="Override max workers")
    run_parser.add_argument(
        "--appendix",
        action="store_true",
        help="Auto-generate appendix files after run",
    )
    run_parser.add_argument(
        "--appendix-output-dir",
        type=Path,
        default=None,
        help="Custom appendix output dir",
    )
    run_parser.add_argument(
        "--artifact-retention-policy",
        choices=("appendix_summary", "appendix_transcripts", "appendix_only", "keep_all"),
        default=None,
        help="Post-run artifact retention policy override",
    )

    export_parser = subparsers.add_parser(
        "export-hf",
        help="Export ContextBench split from HF to local JSONL",
    )
    export_parser.add_argument("--output", required=True, type=Path, help="Output JSONL path")
    export_parser.add_argument("--split", default="train", help="HF split")
    export_parser.add_argument(
        "--benchmark",
        default=BENCHMARK_CONTEXTBENCH_VERIFIED,
        choices=(BENCHMARK_CONTEXTBENCH_VERIFIED,),
        help="Benchmark profile",
    )
    export_parser.add_argument("--dataset", default=None, help="HF dataset path")
    export_parser.add_argument("--dataset-config", default="contextbench_verified", help="HF config")
    export_parser.add_argument("--revision", default=None, help="HF revision")
    export_parser.add_argument("--cache-dir", type=Path, default=None, help="HF cache dir")
    export_parser.add_argument("--streaming", action="store_true", help="Stream HF rows")
    export_parser.add_argument("--language", default=None, help="Language filter")
    export_parser.add_argument("--repo", action="append", default=[], help="Repo filter")
    export_parser.add_argument("--instance-id", action="append", default=[], help="Instance filter")
    export_parser.add_argument("--instance-ids-file", type=Path, default=None, help="Instance id file")
    export_parser.add_argument("--max-instances", type=int, default=None, help="Max rows")
    export_parser.add_argument("--overwrite", action="store_true", help="Overwrite output")
    export_parser.add_argument("--token-env", default="HF_TOKEN", help="HF token env var")

    appendix_parser = subparsers.add_parser(
        "appendix",
        help="Generate appendix files from completed run roots",
    )
    appendix_parser.add_argument(
        "--run-root",
        action="append",
        required=True,
        type=Path,
        help="Repeatable run root path",
    )
    appendix_parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("reports/appendix"),
        help="Appendix output directory",
    )

    db_parser = subparsers.add_parser("db-import", help="Import appendix CSV into SQLite")
    db_parser.add_argument(
        "--appendix-csv",
        action="append",
        required=True,
        type=Path,
        help="Repeatable appendix CSV path",
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
        help="SQLite DB file path",
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
        default=BENCHMARK_CONTEXTBENCH_VERIFIED,
        help="Optional benchmark filter for auto-discovery",
    )
    prune_parser.add_argument(
        "--older-than-days",
        type=int,
        default=None,
        help="Only prune run roots last modified before N days ago",
    )
    prune_parser.add_argument(
        "--artifact-retention-policy",
        choices=("appendix_summary", "appendix_transcripts", "appendix_only", "keep_all"),
        default="appendix_summary",
        help="Retention policy to enforce while pruning historical artifacts",
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


if __name__ == "__main__":
    main()
