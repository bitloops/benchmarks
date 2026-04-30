from __future__ import annotations

import contextlib
import importlib.util
import io
import tempfile
from pathlib import Path
import unittest


def _load_script_module():
    module_path = (
        Path(__file__).resolve().parents[1]
        / "scripts"
        / "swebench"
        / "bitloops_init_status.py"
    )
    spec = importlib.util.spec_from_file_location("bitloops_init_status_script", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Failed to load scripts/swebench/bitloops_init_status.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


script = _load_script_module()


class BitloopsInitStatusScriptTests(unittest.TestCase):
    def test_parse_args_reports_shell_continuation_hint_for_whitespace_argument(self) -> None:
        stderr = io.StringIO()

        with contextlib.redirect_stderr(stderr):
            with self.assertRaises(SystemExit) as raised:
                script.parse_args(["--run-id", "20260429_124810_77fdbe", " "])

        self.assertEqual(raised.exception.code, 2)
        self.assertIn("whitespace-only argument", stderr.getvalue())
        self.assertIn("shell continuation", stderr.getvalue())

    def test_find_latest_run_root_picks_latest_timestamped_run(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            runs_root = Path(temp_dir) / "runs"
            older = runs_root / "swebench_multilingual" / "20260426_235959_deadbe"
            newer = runs_root / "swebench_multilingual" / "20260427_103024_a73844"
            older.mkdir(parents=True)
            newer.mkdir(parents=True)
            (older / "run_manifest.json").write_text("{}", encoding="utf-8")
            (newer / "run_manifest.json").write_text("{}", encoding="utf-8")

            latest = script.find_latest_run_root(runs_root)

            self.assertEqual(latest, newer)

    def test_resolve_instance_record_filters_by_repo_and_instance_id(self) -> None:
        entries = [
            {"repo": "astral-sh/ruff", "instance_id": "astral-sh__ruff-1"},
            {"repo": "tokio-rs/axum", "instance_id": "tokio-rs__axum-1119"},
        ]

        record = script.resolve_instance_record(
            entries,
            repo="tokio-rs/axum",
            instance_id="tokio-rs__axum-1119",
        )

        self.assertEqual(record["repo"], "tokio-rs/axum")
        self.assertEqual(record["instance_id"], "tokio-rs__axum-1119")

    def test_resolve_workspace_paths_finds_workspace_and_sandbox(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            run_root = Path(temp_dir) / "20260427_103024_a73844"
            repo_slug = "tokio-rs__axum"
            base_commit = "23808f72a2c00c314cedea40a75b73954402a148"
            instance_id = "tokio-rs__axum-1119"
            workspace_root = (
                run_root
                / "workspaces"
                / "_isolated"
                / "20260427_103024_a73844"
                / repo_slug
                / base_commit
                / instance_id
            )
            sandbox_root = workspace_root.parent / f"{instance_id}__bitloops"
            bitloops_home = sandbox_root / "home"
            bitloops_home.mkdir(parents=True)
            workspace_root.mkdir(parents=True)

            resolved = script.resolve_workspace_paths(
                run_root,
                {
                    "repo": "tokio-rs/axum",
                    "base_commit": base_commit,
                    "instance_id": instance_id,
                },
            )

            self.assertEqual(resolved.workspace_root, workspace_root.resolve())
            self.assertEqual(resolved.sandbox_root, sandbox_root.resolve())
            self.assertEqual(resolved.bitloops_home, bitloops_home.resolve())
            self.assertIsNone(resolved.attempt)

    def test_resolve_workspace_paths_supports_attempt_scoped_layout(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            run_root = Path(temp_dir) / "20260430_144543_c48652"
            repo_slug = "nushell__nushell"
            base_commit = "fb34a4fc6c9ca882cc6dc95d437902a45c402e9a"
            instance_id = "nushell__nushell-13831"
            instance_root = (
                run_root
                / "workspaces"
                / "_isolated"
                / "20260430_144543_c48652"
                / repo_slug
                / base_commit
                / instance_id
            )
            attempt_one = instance_root / "attempt-01"
            attempt_two = instance_root / "attempt-02"
            attempt_one_bitloops = instance_root / "attempt-01__bitloops" / "home"
            attempt_two_bitloops = instance_root / "attempt-02__bitloops" / "home"
            attempt_one.mkdir(parents=True)
            attempt_two.mkdir(parents=True)
            attempt_one_bitloops.mkdir(parents=True)
            attempt_two_bitloops.mkdir(parents=True)

            resolved = script.resolve_workspace_paths(
                run_root,
                {
                    "repo": "nushell/nushell",
                    "base_commit": base_commit,
                    "instance_id": instance_id,
                },
                attempt=2,
            )

            self.assertEqual(resolved.workspace_root, attempt_two.resolve())
            self.assertEqual(
                resolved.sandbox_root,
                (instance_root / "attempt-02__bitloops").resolve(),
            )
            self.assertEqual(resolved.bitloops_home, attempt_two_bitloops.resolve())
            self.assertEqual(resolved.attempt, 2)

    def test_resolve_workspace_paths_requires_attempt_when_parallel_attempts_exist(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            run_root = Path(temp_dir) / "20260430_144543_c48652"
            repo_slug = "nushell__nushell"
            base_commit = "fb34a4fc6c9ca882cc6dc95d437902a45c402e9a"
            instance_id = "nushell__nushell-13831"
            instance_root = (
                run_root
                / "workspaces"
                / "_isolated"
                / "20260430_144543_c48652"
                / repo_slug
                / base_commit
                / instance_id
            )
            (instance_root / "attempt-01").mkdir(parents=True)
            (instance_root / "attempt-02").mkdir(parents=True)

            with self.assertRaisesRegex(ValueError, "--attempt"):
                script.resolve_workspace_paths(
                    run_root,
                    {
                        "repo": "nushell/nushell",
                        "base_commit": base_commit,
                        "instance_id": instance_id,
                    },
                )

    def test_render_snapshot_includes_lane_and_db_sections(self) -> None:
        rendered = script.render_snapshot(
            run_id="20260427_103024_a73844",
            repo="tokio-rs/axum",
            instance_id="tokio-rs__axum-1119",
            workspace_root=Path("/tmp/workspace"),
            attempt=None,
            status_payload={
                "session": {
                    "status": "running",
                    "statusLabel": "Running",
                    "summaryText": "Building your project's Intelligence Layer",
                    "lanes": [
                        {
                            "title": "Sync",
                            "statusLabel": "Completed",
                            "summaryText": "Completed",
                        },
                        {
                            "title": "Code Embeddings",
                            "statusLabel": "Running",
                            "summaryText": "Running | 100 / 200 indexed",
                        },
                    ],
                }
            },
            db_snapshot={
                "repo_id": "repo-123",
                "embedding_counts": {"code": 200, "identity": 200, "summary": 40},
                "summary_mailbox_items": 12,
                "workplane_jobs": [
                    ("semantic_clones.clone_rebuild", "pending", 1),
                    ("semantic_clones.clone_rebuild", "completed", 7),
                ],
            },
            status_error=None,
        )

        self.assertIn("Run ID: 20260427_103024_a73844", rendered)
        self.assertIn("Repo: tokio-rs/axum", rendered)
        self.assertIn("Init Status: Running", rendered)
        self.assertIn("Sync: Completed", rendered)
        self.assertIn("Code Embeddings: Running | 100 / 200 indexed", rendered)
        self.assertIn("Stored embeddings: code=200, identity=200, summary=40", rendered)
        self.assertIn("Summary mailbox items: 12", rendered)


if __name__ == "__main__":
    unittest.main()
