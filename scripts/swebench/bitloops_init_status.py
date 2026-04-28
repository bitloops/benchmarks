#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import time
from typing import Any, NamedTuple


class WorkspacePaths(NamedTuple):
    workspace_root: Path
    sandbox_root: Path
    bitloops_home: Path


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Inspect Bitloops init status for a benchmark task sandbox."
    )
    parser.add_argument("--run-id", help="Benchmark run id, for example 20260427_103024_a73844.")
    parser.add_argument(
        "--run-root",
        help="Absolute or relative path to a run root. Overrides --run-id lookup.",
    )
    parser.add_argument(
        "--runs-root",
        default="runs",
        help="Root directory that contains benchmark run folders. Defaults to ./runs.",
    )
    parser.add_argument("--repo", help="Repository slug, for example tokio-rs/axum.")
    parser.add_argument(
        "--instance-id",
        help="Benchmark instance id, for example tokio-rs__axum-1119.",
    )
    parser.add_argument(
        "--watch",
        action="store_true",
        help="Refresh the snapshot until interrupted.",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=3.0,
        help="Polling interval in seconds for --watch. Defaults to 3.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit the aggregated script snapshot as JSON.",
    )
    return parser.parse_args(argv)


def find_run_roots(runs_root: Path) -> list[Path]:
    manifests = sorted(runs_root.rglob("run_manifest.json"))
    return [manifest.parent for manifest in manifests]


def find_latest_run_root(runs_root: Path) -> Path:
    run_roots = find_run_roots(runs_root)
    if not run_roots:
        raise FileNotFoundError(f"No benchmark runs found under {runs_root}")
    return sorted(run_roots, key=lambda path: path.name)[-1]


def resolve_run_root(
    *,
    runs_root: Path,
    run_root: str | None,
    run_id: str | None,
) -> Path:
    if run_root:
        return Path(run_root).expanduser().resolve()
    if run_id:
        matches = [path for path in find_run_roots(runs_root) if path.name == run_id]
        if not matches:
            raise FileNotFoundError(f"Could not find run id {run_id} under {runs_root}")
        return matches[0]
    return find_latest_run_root(runs_root)


def load_instances(run_root: Path) -> list[dict[str, Any]]:
    path = run_root / "instances.jsonl"
    entries: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        text = line.strip()
        if not text:
            continue
        payload = json.loads(text)
        if isinstance(payload, dict):
            entries.append(payload)
    return entries


def _prompt_choice(label: str, options: list[str]) -> str:
    print(f"Select {label}:")
    for index, option in enumerate(options, start=1):
        print(f"  {index}. {option}")
    while True:
        raw = input(f"Enter {label} number [1-{len(options)}]: ").strip()
        if not raw:
            continue
        try:
            selected_index = int(raw)
        except ValueError:
            print("Please enter a number.")
            continue
        if 1 <= selected_index <= len(options):
            return options[selected_index - 1]
        print("Selection out of range.")


def resolve_instance_record(
    entries: list[dict[str, Any]],
    *,
    repo: str | None,
    instance_id: str | None,
    interactive: bool = False,
) -> dict[str, Any]:
    if not entries:
        raise ValueError("Run has no instances.")

    matching = entries
    if repo:
        matching = [entry for entry in matching if str(entry.get("repo", "")).strip() == repo]
    if instance_id:
        matching = [
            entry
            for entry in matching
            if str(entry.get("instance_id", "")).strip() == instance_id
        ]

    if len(matching) == 1:
        return matching[0]
    if matching and repo and instance_id:
        raise ValueError(f"No exact instance matched repo={repo!r} instance_id={instance_id!r}")

    if not interactive:
        if not matching:
            raise ValueError("No instances matched the supplied filters.")
        preview = ", ".join(
            f"{entry.get('repo')}:{entry.get('instance_id')}" for entry in matching[:5]
        )
        raise ValueError(
            "Multiple instances matched. Re-run with --repo and --instance-id. "
            f"Matches: {preview}"
        )

    repo_options = sorted({str(entry.get("repo", "")).strip() for entry in matching})
    selected_repo = repo or (repo_options[0] if len(repo_options) == 1 else _prompt_choice("repo", repo_options))
    repo_matching = [
        entry for entry in matching if str(entry.get("repo", "")).strip() == selected_repo
    ]

    if len(repo_matching) == 1:
        return repo_matching[0]

    instance_options = [
        str(entry.get("instance_id", "")).strip()
        for entry in sorted(repo_matching, key=lambda item: str(item.get("instance_id", "")))
    ]
    selected_instance_id = instance_id or _prompt_choice("instance", instance_options)
    final_matching = [
        entry
        for entry in repo_matching
        if str(entry.get("instance_id", "")).strip() == selected_instance_id
    ]
    if len(final_matching) != 1:
        raise ValueError(
            f"Could not resolve a single instance for repo={selected_repo!r} "
            f"instance_id={selected_instance_id!r}"
        )
    return final_matching[0]


def _repo_slug(repo: str) -> str:
    return repo.replace("/", "__")


def resolve_workspace_paths(run_root: Path, record: dict[str, Any]) -> WorkspacePaths:
    repo = str(record.get("repo", "")).strip()
    base_commit = str(record.get("base_commit", "")).strip()
    instance_id = str(record.get("instance_id", "")).strip()
    if not repo or not base_commit or not instance_id:
        raise ValueError("Instance record is missing repo, base_commit, or instance_id.")

    pattern = (
        "workspaces/_isolated/*/"
        f"{_repo_slug(repo)}/{base_commit}/{instance_id}"
    )
    matches = sorted(run_root.glob(pattern))
    if not matches:
        raise FileNotFoundError(
            f"Could not find workspace for repo={repo} instance_id={instance_id} under {run_root}"
        )
    workspace_root = matches[0].resolve()
    sandbox_root = (workspace_root.parent / f"{instance_id}__bitloops").resolve()
    bitloops_home = (sandbox_root / "home").resolve()
    return WorkspacePaths(
        workspace_root=workspace_root,
        sandbox_root=sandbox_root,
        bitloops_home=bitloops_home,
    )


def build_bitloops_env(bitloops_home: Path) -> dict[str, str]:
    env = dict(os.environ)
    home_text = str(bitloops_home)
    env["HOME"] = home_text
    env["USERPROFILE"] = home_text
    env["XDG_CONFIG_HOME"] = str(bitloops_home / "xdg")
    env["XDG_STATE_HOME"] = str(bitloops_home / "xdg-state")
    env["XDG_CACHE_HOME"] = str(bitloops_home / "xdg-cache")
    env["XDG_DATA_HOME"] = str(bitloops_home / "xdg-data")
    env["BITLOOPS_DAEMON_CONFIG_PATH_OVERRIDE"] = str(
        bitloops_home / "Library" / "Application Support" / "bitloops" / "config.toml"
    )
    return env


def call_command(
    command: list[str],
    *,
    env: dict[str, str],
    cwd: Path,
    timeout_seconds: int = 15,
) -> tuple[str, str, int]:
    completed = subprocess.run(
        command,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        env=env,
        cwd=str(cwd),
        timeout=timeout_seconds,
        check=False,
    )
    return completed.stdout, completed.stderr, completed.returncode


def load_bitloops_init_status(
    *,
    workspace_root: Path,
    bitloops_home: Path,
) -> tuple[dict[str, Any] | None, str | None]:
    env = build_bitloops_env(bitloops_home)
    stdout, stderr, return_code = call_command(
        ["bitloops", "init", "status", "--json"],
        env=env,
        cwd=workspace_root,
    )
    if return_code != 0:
        detail = stderr.strip() or stdout.strip() or f"bitloops init status exit={return_code}"
        return None, detail
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError as exc:
        return None, f"invalid JSON from bitloops init status: {exc}"
    if not isinstance(payload, dict):
        return None, "bitloops init status returned a non-object payload"
    return payload, None


def _safe_query_all(db_path: Path, sql: str, params: tuple[Any, ...] = ()) -> list[tuple[Any, ...]]:
    if not db_path.exists():
        return []
    connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=1.0)
    try:
        return list(connection.execute(sql, params).fetchall())
    except sqlite3.Error:
        return []
    finally:
        connection.close()


def load_db_snapshot(
    *,
    bitloops_home: Path,
    repo: str,
) -> dict[str, Any]:
    runtime_db = (
        bitloops_home
        / "Library"
        / "Application Support"
        / "bitloops"
        / "stores"
        / "runtime"
        / "runtime.sqlite"
    )
    relational_db = (
        bitloops_home
        / "Library"
        / "Application Support"
        / "bitloops"
        / "stores"
        / "relational"
        / "relational.db"
    )

    provider = "github"
    organization, _, name = repo.partition("/")
    repo_rows = _safe_query_all(
        relational_db,
        """
        SELECT repo_id, provider, organization, name
        FROM repositories
        WHERE provider = ? AND organization = ? AND name = ?
        """,
        (provider, organization, name),
    )
    repo_id = repo_rows[0][0] if repo_rows else None

    embedding_counts: dict[str, int] = {}
    if repo_id is not None:
        for representation_kind, count in _safe_query_all(
            relational_db,
            """
            SELECT representation_kind, count(*)
            FROM symbol_embeddings_current
            WHERE repo_id = ?
            GROUP BY representation_kind
            ORDER BY representation_kind
            """,
            (repo_id,),
        ):
            embedding_counts[str(representation_kind)] = int(count)

    embedding_mailbox_rows = _safe_query_all(
        runtime_db,
        """
        SELECT representation_kind, item_kind, status, count(*)
        FROM semantic_embedding_mailbox_items
        GROUP BY representation_kind, item_kind, status
        ORDER BY representation_kind, item_kind, status
        """,
    )
    summary_mailbox_rows = _safe_query_all(
        runtime_db,
        "SELECT count(*) FROM semantic_summary_mailbox_items",
    )
    workplane_rows = _safe_query_all(
        runtime_db,
        """
        SELECT mailbox_name, status, count(*)
        FROM capability_workplane_jobs
        GROUP BY mailbox_name, status
        ORDER BY mailbox_name, status
        """,
    )
    return {
        "repo_id": repo_id,
        "embedding_counts": embedding_counts,
        "embedding_mailbox_items": [
            (str(kind), str(item_kind), str(status), int(count))
            for kind, item_kind, status, count in embedding_mailbox_rows
        ],
        "summary_mailbox_items": int(summary_mailbox_rows[0][0]) if summary_mailbox_rows else 0,
        "workplane_jobs": [
            (str(mailbox_name), str(status), int(count))
            for mailbox_name, status, count in workplane_rows
        ],
    }


def render_snapshot(
    *,
    run_id: str,
    repo: str,
    instance_id: str,
    workspace_root: Path,
    status_payload: dict[str, Any] | None,
    db_snapshot: dict[str, Any],
    status_error: str | None,
) -> str:
    lines = [
        f"Run ID: {run_id}",
        f"Repo: {repo}",
        f"Instance: {instance_id}",
        f"Workspace: {workspace_root}",
    ]

    session = status_payload.get("session") if isinstance(status_payload, dict) else None
    if isinstance(session, dict):
        lines.append(
            f"Init Status: {session.get('statusLabel') or session.get('status') or 'Unknown'}"
        )
        summary_text = str(session.get("summaryText") or "").strip()
        if summary_text:
            lines.append(f"Summary: {summary_text}")
        waiting_label = str(session.get("waitingLabel") or "").strip()
        if waiting_label:
            lines.append(f"Waiting: {waiting_label}")
        for lane in session.get("lanes", []):
            if not isinstance(lane, dict):
                continue
            title = str(lane.get("title") or lane.get("label") or "Lane").strip()
            lane_summary = str(
                lane.get("summaryText") or lane.get("statusLabel") or lane.get("status") or ""
            ).strip()
            if lane_summary:
                lines.append(f"{title}: {lane_summary}")
    elif status_error:
        lines.append(f"Init Status: unavailable ({status_error})")
    else:
        lines.append("Init Status: unavailable")

    repo_id = db_snapshot.get("repo_id")
    if repo_id:
        lines.append(f"Repo ID: {repo_id}")
    embedding_counts = db_snapshot.get("embedding_counts", {})
    if isinstance(embedding_counts, dict) and embedding_counts:
        embedding_parts = [
            f"{kind}={embedding_counts[kind]}"
            for kind in sorted(embedding_counts)
        ]
        lines.append("Stored embeddings: " + ", ".join(embedding_parts))
    else:
        lines.append("Stored embeddings: none yet")

    embedding_mailbox_items = db_snapshot.get("embedding_mailbox_items", [])
    if embedding_mailbox_items:
        mailbox_parts = [
            f"{kind}/{item_kind}/{status}={count}"
            for kind, item_kind, status, count in embedding_mailbox_items
        ]
        lines.append("Embedding mailbox: " + ", ".join(mailbox_parts))
    else:
        lines.append("Embedding mailbox: empty")

    lines.append(f"Summary mailbox items: {db_snapshot.get('summary_mailbox_items', 0)}")

    workplane_jobs = db_snapshot.get("workplane_jobs", [])
    if workplane_jobs:
        job_parts = [
            f"{mailbox_name}/{status}={count}"
            for mailbox_name, status, count in workplane_jobs
        ]
        lines.append("Workplane jobs: " + ", ".join(job_parts))
    else:
        lines.append("Workplane jobs: none")

    return "\n".join(lines)


def build_snapshot_payload(
    *,
    run_root: Path,
    record: dict[str, Any],
    paths: WorkspacePaths,
) -> dict[str, Any]:
    status_payload, status_error = load_bitloops_init_status(
        workspace_root=paths.workspace_root,
        bitloops_home=paths.bitloops_home,
    )
    db_snapshot = load_db_snapshot(
        bitloops_home=paths.bitloops_home,
        repo=str(record.get("repo", "")),
    )
    return {
        "run_id": run_root.name,
        "repo": str(record.get("repo", "")),
        "instance_id": str(record.get("instance_id", "")),
        "workspace_root": str(paths.workspace_root),
        "bitloops_home": str(paths.bitloops_home),
        "status_payload": status_payload,
        "status_error": status_error,
        "db_snapshot": db_snapshot,
    }


def _clear_screen() -> None:
    sys.stdout.write("\033[2J\033[H")
    sys.stdout.flush()


def _print_snapshot(snapshot: dict[str, Any], *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(snapshot, indent=2, sort_keys=True))
        return
    print(
        render_snapshot(
            run_id=str(snapshot["run_id"]),
            repo=str(snapshot["repo"]),
            instance_id=str(snapshot["instance_id"]),
            workspace_root=Path(str(snapshot["workspace_root"])),
            status_payload=snapshot.get("status_payload"),
            db_snapshot=dict(snapshot.get("db_snapshot", {})),
            status_error=snapshot.get("status_error"),
        )
    )


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    runs_root = Path(args.runs_root).expanduser().resolve()
    run_root = resolve_run_root(runs_root=runs_root, run_root=args.run_root, run_id=args.run_id)
    entries = load_instances(run_root)
    record = resolve_instance_record(
        entries,
        repo=args.repo,
        instance_id=args.instance_id,
        interactive=sys.stdin.isatty(),
    )
    paths = resolve_workspace_paths(run_root, record)

    try:
        while True:
            snapshot = build_snapshot_payload(run_root=run_root, record=record, paths=paths)
            if args.watch:
                _clear_screen()
                print(time.strftime("%Y-%m-%d %H:%M:%S"))
            _print_snapshot(snapshot, as_json=args.json)
            if not args.watch:
                return 0
            time.sleep(max(args.interval, 0.5))
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
