from __future__ import annotations

import json
from pathlib import Path
from subprocess import CompletedProcess
import tempfile
import unittest
from unittest.mock import patch

from benchkit.swebench.types import BenchmarkInstance
from benchkit.swebench import workspace as workspace_module
from benchkit.swebench.workspace import prepare_instance_workspace


class WorkspaceTests(unittest.TestCase):
    def _instance(self) -> BenchmarkInstance:
        return BenchmarkInstance(
            instance_id="tokio__1",
            repo="tokio-rs/tokio",
            base_commit="abc123",
            problem_statement="Fix issue",
            language="rust",
        )

    def _strict_clone_run(self, commands: list[list[str]] | None = None):
        def fake_run(command: list[str], _timeout_seconds: int, check: bool = True) -> CompletedProcess[str]:
            del check
            if commands is not None:
                commands.append(command)
            if command[:2] == ["git", "clone"]:
                Path(command[-1]).mkdir(parents=True, exist_ok=True)
                (Path(command[-1]) / ".git").mkdir(parents=True, exist_ok=True)
            if command[-2:] == ["remove", "origin"] or command[-2:] == ["remove", "backup"]:
                return CompletedProcess(args=command, returncode=0, stdout="", stderr="")
            if command[-1] == "remote":
                return CompletedProcess(args=command, returncode=0, stdout="origin\nbackup\n", stderr="")
            return CompletedProcess(args=command, returncode=0, stdout="", stderr="")

        return fake_run

    def test_prepare_workspace_reuses_existing_checkout(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            run_root = Path(temp_dir)
            with patch(
                "benchkit.swebench.workspace._is_checkout_ready",
                return_value=True,
            ):
                result = prepare_instance_workspace(
                    instance=self._instance(),
                    run_root=run_root,
                    repo_url_template="https://github.com/{repo}.git",
                    git_bin="git",
                    timeout_seconds=60,
                )
            self.assertEqual(result.status, "reused")
            self.assertIsNotNone(result.workspace_path)
            self.assertEqual(result.isolation_mode, "shared_repo_commit")

    def test_prepare_workspace_clones_and_checks_out(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            run_root = Path(temp_dir)
            commands: list[list[str]] = []

            with patch(
                "benchkit.swebench.workspace._is_checkout_ready",
                return_value=False,
            ), patch(
                "benchkit.swebench.workspace._run",
                side_effect=self._strict_clone_run(commands),
            ):
                result = prepare_instance_workspace(
                    instance=self._instance(),
                    run_root=run_root,
                    repo_url_template="https://github.com/{repo}.git",
                    git_bin="git",
                    timeout_seconds=60,
                )
            self.assertEqual(result.status, "prepared")
            self.assertTrue(str(result.repo_url).endswith("tokio-rs/tokio.git"))
            self.assertEqual(result.isolation_mode, "shared_repo_commit")
            self.assertIn(
                [
                    "git",
                    "clone",
                    "--revision=abc123",
                    "https://github.com/tokio-rs/tokio.git",
                    str(result.workspace_path),
                ],
                commands,
            )
            self.assertIn(
                ["git", "-C", str(result.workspace_path), "remote"],
                commands,
            )
            self.assertIn(
                ["git", "-C", str(result.workspace_path), "remote", "remove", "origin"],
                commands,
            )
            self.assertIn(
                ["git", "-C", str(result.workspace_path), "remote", "remove", "backup"],
                commands,
            )
            marker_path = result.workspace_path / workspace_module.WORKSPACE_PREP_MARKER
            marker = json.loads(marker_path.read_text(encoding="utf-8"))
            self.assertEqual(marker["prep_mode"], "strict_revision_clone")
            self.assertEqual(marker["repo"], "tokio-rs/tokio")
            self.assertEqual(marker["base_commit"], "abc123")
            self.assertTrue(marker["remotes_removed"])

    def test_prepare_workspace_uses_task_scoped_path_for_bitloops_runs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            run_root = Path(temp_dir)
            with patch(
                "benchkit.swebench.workspace._is_checkout_ready",
                return_value=False,
            ), patch(
                "benchkit.swebench.workspace._run",
                side_effect=self._strict_clone_run(),
            ):
                result = prepare_instance_workspace(
                    instance=self._instance(),
                    run_root=run_root,
                    repo_url_template="https://github.com/{repo}.git",
                    git_bin="git",
                    timeout_seconds=60,
                    isolation_mode="task_scoped",
                    run_id="run-123",
                )
            self.assertIsNotNone(result.workspace_path)
            self.assertEqual(result.isolation_mode, "task_scoped")
            self.assertIn("_isolated", str(result.workspace_path))
            self.assertTrue(str(result.workspace_path).endswith("tokio__1"))

    def test_prepare_workspace_uses_attempt_scoped_path_for_parallel_attempt_runs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            run_root = Path(temp_dir)
            with patch(
                "benchkit.swebench.workspace._is_checkout_ready",
                return_value=False,
            ), patch(
                "benchkit.swebench.workspace._run",
                side_effect=self._strict_clone_run(),
            ):
                result = prepare_instance_workspace(
                    instance=self._instance(),
                    run_root=run_root,
                    repo_url_template="https://github.com/{repo}.git",
                    git_bin="git",
                    timeout_seconds=60,
                    isolation_mode="attempt_scoped",
                    run_id="run-123",
                    attempt=2,
                )
            self.assertIsNotNone(result.workspace_path)
            self.assertEqual(result.isolation_mode, "attempt_scoped")
            self.assertIn("_isolated", str(result.workspace_path))
            self.assertTrue(str(result.workspace_path).endswith("attempt-02"))

    def test_prepare_workspace_reports_error(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            run_root = Path(temp_dir)
            with patch(
                "benchkit.swebench.workspace._is_checkout_ready",
                return_value=False,
            ), patch(
                "benchkit.swebench.workspace._run",
                side_effect=RuntimeError("clone failed"),
            ):
                result = prepare_instance_workspace(
                    instance=self._instance(),
                    run_root=run_root,
                    repo_url_template="https://github.com/{repo}.git",
                    git_bin="git",
                    timeout_seconds=60,
                )
            self.assertEqual(result.status, "error")
            self.assertIn("clone failed", str(result.error))

    def test_is_checkout_ready_rejects_workspace_without_marker(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            (workspace / ".git").mkdir()

            def fake_run(command: list[str], _timeout_seconds: int, check: bool = True) -> CompletedProcess[str]:
                del check
                if command[-2:] == ["rev-parse", "HEAD"]:
                    return CompletedProcess(args=command, returncode=0, stdout="abc123\n", stderr="")
                if command[-1] == "remote":
                    return CompletedProcess(args=command, returncode=0, stdout="", stderr="")
                raise AssertionError(f"unexpected command: {command}")

            with patch("benchkit.swebench.workspace._run", side_effect=fake_run):
                ready = workspace_module._is_checkout_ready(
                    workspace,
                    "tokio-rs/tokio",
                    "abc123",
                    "git",
                    60,
                )

            self.assertFalse(ready)

    def test_is_checkout_ready_rejects_workspace_with_remotes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            (workspace / ".git").mkdir()
            marker_path = workspace / workspace_module.WORKSPACE_PREP_MARKER
            marker_path.write_text(
                json.dumps(
                    {
                        "prep_mode": "strict_revision_clone",
                        "repo": "tokio-rs/tokio",
                        "base_commit": "abc123",
                        "remotes_removed": True,
                    }
                ),
                encoding="utf-8",
            )

            def fake_run(command: list[str], _timeout_seconds: int, check: bool = True) -> CompletedProcess[str]:
                del check
                if command[-2:] == ["rev-parse", "HEAD"]:
                    return CompletedProcess(args=command, returncode=0, stdout="abc123\n", stderr="")
                if command[-1] == "remote":
                    return CompletedProcess(args=command, returncode=0, stdout="origin\n", stderr="")
                raise AssertionError(f"unexpected command: {command}")

            with patch("benchkit.swebench.workspace._run", side_effect=fake_run):
                ready = workspace_module._is_checkout_ready(
                    workspace,
                    "tokio-rs/tokio",
                    "abc123",
                    "git",
                    60,
                )

            self.assertFalse(ready)

    def test_is_checkout_ready_accepts_strict_workspace_without_remotes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            (workspace / ".git").mkdir()
            marker_path = workspace / workspace_module.WORKSPACE_PREP_MARKER
            marker_path.write_text(
                json.dumps(
                    {
                        "prep_mode": "strict_revision_clone",
                        "repo": "tokio-rs/tokio",
                        "base_commit": "abc123",
                        "remotes_removed": True,
                    }
                ),
                encoding="utf-8",
            )

            def fake_run(command: list[str], _timeout_seconds: int, check: bool = True) -> CompletedProcess[str]:
                del check
                if command[-2:] == ["rev-parse", "HEAD"]:
                    return CompletedProcess(args=command, returncode=0, stdout="abc123\n", stderr="")
                if command[-1] == "remote":
                    return CompletedProcess(args=command, returncode=0, stdout="", stderr="")
                raise AssertionError(f"unexpected command: {command}")

            with patch("benchkit.swebench.workspace._run", side_effect=fake_run):
                ready = workspace_module._is_checkout_ready(
                    workspace,
                    "tokio-rs/tokio",
                    "abc123",
                    "git",
                    60,
                )

            self.assertTrue(ready)


if __name__ == "__main__":
    unittest.main()
