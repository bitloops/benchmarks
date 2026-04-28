from __future__ import annotations

from pathlib import Path
from subprocess import CompletedProcess
import tempfile
import unittest
from unittest.mock import patch

from benchkit.swebench.types import BenchmarkInstance
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
            with patch(
                "benchkit.swebench.workspace._is_checkout_ready",
                return_value=False,
            ), patch(
                "benchkit.swebench.workspace._run",
                return_value=CompletedProcess(args=["git"], returncode=0, stdout="", stderr=""),
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

    def test_prepare_workspace_uses_task_scoped_path_for_bitloops_runs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            run_root = Path(temp_dir)
            with patch(
                "benchkit.swebench.workspace._is_checkout_ready",
                return_value=False,
            ), patch(
                "benchkit.swebench.workspace._run",
                return_value=CompletedProcess(args=["git"], returncode=0, stdout="", stderr=""),
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
                return_value=CompletedProcess(args=["git"], returncode=0, stdout="", stderr=""),
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


if __name__ == "__main__":
    unittest.main()
