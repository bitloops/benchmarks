from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from benchkit.swebench.cli import run_execute
from benchkit.swebench.runner import RunResult


class CliRunTests(unittest.TestCase):
    def test_run_execute_generates_appendix_when_output_dir_is_set(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / "config.toml"
            run_root = root / "runs" / "swebench_multilingual" / "20260101" / "run-1"
            result = RunResult(
                run_id="run-1",
                run_root=run_root,
                total_instances=1,
                attempts=1,
                prediction_files=[],
                trace_files=[],
                evaluation_reports=[],
            )

            with (
                patch("benchkit.swebench.cli.load_run_config", return_value=object()),
                patch("benchkit.swebench.cli.execute_run", return_value=result),
                patch("benchkit.swebench.cli.run_appendix") as run_appendix_mock,
            ):
                run_execute(
                    config_path=config_path,
                    dry_run=False,
                    attempts=None,
                    max_workers=None,
                    appendix_output_dir=root / "appendix",
                )

            run_appendix_mock.assert_called_once_with(
                run_roots=[run_root],
                output_dir=root / "appendix",
            )

    def test_run_execute_skips_appendix_when_output_dir_not_set(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / "config.toml"
            run_root = root / "runs" / "swebench_multilingual" / "20260101" / "run-1"
            result = RunResult(
                run_id="run-1",
                run_root=run_root,
                total_instances=1,
                attempts=1,
                prediction_files=[],
                trace_files=[],
                evaluation_reports=[],
            )

            with (
                patch("benchkit.swebench.cli.load_run_config", return_value=object()),
                patch("benchkit.swebench.cli.execute_run", return_value=result),
                patch("benchkit.swebench.cli.run_appendix") as run_appendix_mock,
            ):
                run_execute(
                    config_path=config_path,
                    dry_run=False,
                    attempts=None,
                    max_workers=None,
                    appendix_output_dir=None,
                )

            run_appendix_mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()
