from __future__ import annotations

from pathlib import Path
from contextlib import redirect_stderr, redirect_stdout
from types import SimpleNamespace
import io
import json
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from benchkit.swebench.cli import (
    _appendix_timestamp_segment,
    _filesystem_slug,
    default_appendix_output_dir,
    run_execute,
    run_plan,
)
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
                    mode="with_bitloops",
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
                    mode=None,
                    dry_run=False,
                    attempts=None,
                    max_workers=None,
                    appendix_output_dir=None,
                )

            run_appendix_mock.assert_not_called()

    def test_run_execute_appendix_auto_dir_bitloops(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / "config.toml"
            run_root = root / "runs" / "swebench_multilingual" / "20260101" / "20260101_120000_abc123"
            result = RunResult(
                run_id="20260101_120000_abc123",
                run_root=run_root,
                total_instances=1,
                attempts=1,
                prediction_files=[],
                trace_files=[],
                evaluation_reports=[],
            )
            config = SimpleNamespace(
                agent=SimpleNamespace(id="opencode"),
                bitloops_enabled=True,
            )

            with (
                patch("benchkit.swebench.cli.load_run_config", return_value=config),
                patch("benchkit.swebench.cli.execute_run", return_value=result),
                patch("benchkit.swebench.cli.run_appendix") as run_appendix_mock,
            ):
                run_execute(
                    config_path=config_path,
                    mode="with_bitloops",
                    dry_run=False,
                    attempts=None,
                    max_workers=None,
                    appendix=True,
                )

            run_appendix_mock.assert_called_once_with(
                run_roots=[run_root],
                output_dir=Path("reports/appendix/opencode_bitloops_20260101_120000"),
            )

    def test_run_execute_appendix_auto_dir_baseline(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / "config.toml"
            run_root = root / "runs" / "run-1"
            result = RunResult(
                run_id="20260101_120000_def456",
                run_root=run_root,
                total_instances=1,
                attempts=1,
                prediction_files=[],
                trace_files=[],
                evaluation_reports=[],
            )
            config = SimpleNamespace(
                agent=SimpleNamespace(id="opencode"),
                bitloops_enabled=False,
            )

            with (
                patch("benchkit.swebench.cli.load_run_config", return_value=config),
                patch("benchkit.swebench.cli.execute_run", return_value=result),
                patch("benchkit.swebench.cli.run_appendix") as run_appendix_mock,
            ):
                run_execute(
                    config_path=config_path,
                    mode="baseline",
                    dry_run=False,
                    attempts=None,
                    max_workers=None,
                    appendix=True,
                )

            run_appendix_mock.assert_called_once_with(
                run_roots=[run_root],
                output_dir=Path("reports/appendix/opencode_baseline_20260101_120000"),
            )

    def test_run_execute_appendix_explicit_dir_overrides_appendix_flag(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / "config.toml"
            custom = root / "my_appendix"
            run_root = root / "runs" / "20260101_120000_zzz"
            result = RunResult(
                run_id="20260101_120000_zzz",
                run_root=run_root,
                total_instances=1,
                attempts=1,
                prediction_files=[],
                trace_files=[],
                evaluation_reports=[],
            )
            config = SimpleNamespace(
                agent=SimpleNamespace(id="opencode"),
                bitloops_enabled=True,
            )
            stderr = io.StringIO()
            with (
                patch("benchkit.swebench.cli.load_run_config", return_value=config),
                patch("benchkit.swebench.cli.execute_run", return_value=result),
                patch("benchkit.swebench.cli.run_appendix") as run_appendix_mock,
                redirect_stderr(stderr),
            ):
                run_execute(
                    config_path=config_path,
                    mode="with_bitloops",
                    dry_run=False,
                    attempts=None,
                    max_workers=None,
                    appendix_output_dir=custom,
                    appendix=True,
                )

            run_appendix_mock.assert_called_once_with(run_roots=[run_root], output_dir=custom)
            self.assertIn("precedence", stderr.getvalue())

    def test_run_execute_passes_mode_to_config_loader(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / "config.toml"
            result = RunResult(
                run_id="run-1",
                run_root=root / "runs" / "run-1",
                total_instances=0,
                attempts=1,
                prediction_files=[],
                trace_files=[],
                evaluation_reports=[],
            )

            with (
                patch("benchkit.swebench.cli.load_run_config", return_value=object()) as load_config_mock,
                patch("benchkit.swebench.cli.execute_run", return_value=result),
            ):
                run_execute(
                    config_path=config_path,
                    mode="baseline",
                    dry_run=True,
                    attempts=None,
                    max_workers=None,
                    appendix_output_dir=None,
                )

            load_config_mock.assert_called_once_with(config_path, mode="baseline")

    def test_run_plan_prints_selected_mode(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            dataset_path = root / "dataset.jsonl"
            dataset_path.write_text(
                json.dumps(
                    {
                        "instance_id": "tokio__1",
                        "repo": "tokio-rs/tokio",
                        "base_commit": "0" * 40,
                        "problem_statement": "Fix it",
                        "language": "rust",
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            config_path = root / "config.toml"
            config_path.write_text(
                f"""
[run]
benchmark = "swebench_multilingual"
dataset_path = "{dataset_path}"
language = "rust"
include_repos = []
include_instance_ids = []

[agent]
id = "noop"

[model]
provider = "test"
name = "test-model"

[modes.with_bitloops.run]
condition = "with_bitloops"
timeout_seconds = 1500

[modes.with_bitloops.agent]
extra_args = ["--bitloops-init"]
                """.strip(),
                encoding="utf-8",
            )

            output = io.StringIO()
            with redirect_stdout(output):
                run_plan(config_path, show=1, mode="with_bitloops")

            rendered = output.getvalue()
            self.assertIn("Config mode: with_bitloops", rendered)
            self.assertIn("Condition: with_bitloops", rendered)
            self.assertIn("Selected instances: 1", rendered)

    def test_run_execute_dry_run_writes_mode_manifest_and_summary(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = _write_mode_cli_fixture(root)

            for mode, expected_condition, expected_bitloops in (
                ("baseline", "baseline", False),
                ("with_bitloops", "with_bitloops", True),
            ):
                output = io.StringIO()
                with redirect_stdout(output):
                    run_execute(
                        config_path=config_path,
                        mode=mode,
                        dry_run=True,
                        attempts=None,
                        max_workers=None,
                        appendix_output_dir=None,
                    )

                run_root_line = next(
                    line for line in output.getvalue().splitlines() if line.startswith("Run root: ")
                )
                run_root = Path(run_root_line.removeprefix("Run root: "))
                manifest = json.loads((run_root / "run_manifest.json").read_text(encoding="utf-8"))
                summary = json.loads((run_root / "summary.json").read_text(encoding="utf-8"))

                self.assertEqual(manifest["config_mode"], mode)
                self.assertEqual(summary["config_mode"], mode)
                self.assertEqual(summary["condition"], expected_condition)
                self.assertEqual(summary["bitloops_enabled"], expected_bitloops)

def _write_mode_cli_fixture(root: Path) -> Path:
    dataset_path = root / "dataset.jsonl"
    dataset_path.write_text(
        json.dumps(
            {
                "instance_id": "tokio__1",
                "repo": "tokio-rs/tokio",
                "base_commit": "0" * 40,
                "problem_statement": "Fix it",
                "language": "rust",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    config_path = root / "config.toml"
    config_path.write_text(
        f"""
[run]
benchmark = "swebench_multilingual"
dataset_path = "{dataset_path}"
language = "rust"
include_repos = []
include_instance_ids = []
attempts = 1
max_workers = 1
output_root = "{root / "runs"}"
prepare_workspace = false

[agent]
id = "opencode"
command = ["python3", "scripts/agents/opencode_wrapper.py"]

[model]
provider = "test"
name = "test-model"

[evaluation]
enabled = false

[modes.baseline.run]
condition = "baseline"
timeout_seconds = 900

[modes.baseline.agent]
extra_args = []

[modes.with_bitloops.run]
condition = "with_bitloops"
timeout_seconds = 1500
bitloops_sandbox_mode = "per_task_daemon"

[modes.with_bitloops.agent]
extra_args = ["--bitloops-init"]
        """.strip(),
        encoding="utf-8",
    )
    return config_path


class AppendixDirNamingTests(unittest.TestCase):
    def test_filesystem_slug_normalizes(self) -> None:
        self.assertEqual(_filesystem_slug("OpenCode-Agent"), "opencode_agent")

    def test_appendix_timestamp_from_run_id(self) -> None:
        self.assertEqual(_appendix_timestamp_segment("20260101_120000_abc123"), "20260101_120000")

    def test_appendix_timestamp_fallback_for_nonstandard_run_id(self) -> None:
        self.assertEqual(_appendix_timestamp_segment("run-1"), "run_1")

    def test_default_appendix_output_dir(self) -> None:
        cfg = SimpleNamespace(agent=SimpleNamespace(id="opencode"), bitloops_enabled=True)
        self.assertEqual(
            default_appendix_output_dir(cfg, "20260429_153823_ab12cd"),
            Path("reports/appendix/opencode_bitloops_20260429_153823"),
        )


if __name__ == "__main__":
    unittest.main()
