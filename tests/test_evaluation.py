from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from benchkit.common.config import EvaluationConfig
from benchkit.swebench.evaluation import (
    _build_evaluation_command,
    evaluate_predictions_with_harness,
)


class EvaluationTests(unittest.TestCase):
    def test_default_command_includes_report_dir(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            attempt_dir = Path(temp_dir) / "attempt-01"
            attempt_dir.mkdir(parents=True, exist_ok=True)
            predictions = attempt_dir / "predictions.jsonl"
            predictions.write_text("{}", encoding="utf-8")

            cfg = EvaluationConfig(
                enabled=True,
                dataset_name="SWE-bench/SWE-bench_Multilingual",
                split="test",
                max_workers=4,
            )
            command = _build_evaluation_command(
                config=cfg,
                run_id="run-0",
                attempt=1,
                benchmark="swebench_multilingual",
                prediction_path=predictions,
                attempt_dir=attempt_dir,
            )
            self.assertIn("--report_dir", command)
            idx = command.index("--report_dir")
            self.assertEqual(command[idx + 1], str(attempt_dir.resolve()))

    def test_skips_when_disabled(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            attempt_dir = Path(temp_dir) / "attempt-01"
            attempt_dir.mkdir(parents=True, exist_ok=True)
            predictions = attempt_dir / "predictions.jsonl"
            predictions.write_text("{}", encoding="utf-8")

            result = evaluate_predictions_with_harness(
                config=EvaluationConfig(enabled=False),
                run_id="run-1",
                attempt=1,
                benchmark="swebench_multilingual",
                prediction_path=predictions,
                attempt_dir=attempt_dir,
            )
            self.assertEqual(result.status, "skipped")
            self.assertTrue((attempt_dir / "evaluation.json").exists())

    def test_runs_custom_command_template(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            attempt_dir = Path(temp_dir) / "attempt-01"
            attempt_dir.mkdir(parents=True, exist_ok=True)
            predictions = attempt_dir / "predictions.jsonl"
            predictions.write_text("{}", encoding="utf-8")

            cfg = EvaluationConfig(
                enabled=True,
                command_template=[
                    "python3",
                    "-c",
                    "print('eval ok')",
                ],
                timeout_seconds=30,
            )

            result = evaluate_predictions_with_harness(
                config=cfg,
                run_id="run-2",
                attempt=1,
                benchmark="swebench_multilingual",
                prediction_path=predictions,
                attempt_dir=attempt_dir,
            )
            self.assertEqual(result.status, "ok")
            stdout = (attempt_dir / "evaluation.stdout.log").read_text(encoding="utf-8")
            self.assertIn("eval ok", stdout)

    def test_template_substitution(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            attempt_dir = Path(temp_dir) / "attempt-01"
            attempt_dir.mkdir(parents=True, exist_ok=True)
            predictions = attempt_dir / "predictions.jsonl"
            predictions.write_text("{}", encoding="utf-8")

            cfg = EvaluationConfig(
                enabled=True,
                command_template=[
                    "python3",
                    "-c",
                    "import sys; print(sys.argv[1])",
                    "{predictions_path}",
                ],
                timeout_seconds=30,
            )

            result = evaluate_predictions_with_harness(
                config=cfg,
                run_id="run-3",
                attempt=1,
                benchmark="swebench_multilingual",
                prediction_path=predictions,
                attempt_dir=attempt_dir,
            )
            self.assertEqual(result.status, "ok")
            stdout = (attempt_dir / "evaluation.stdout.log").read_text(encoding="utf-8")
            self.assertEqual(stdout.strip(), str(predictions.resolve()))

    def test_parses_task_results_from_configured_result_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            attempt_dir = Path(temp_dir) / "attempt-01"
            attempt_dir.mkdir(parents=True, exist_ok=True)
            predictions = attempt_dir / "predictions.jsonl"
            predictions.write_text("{}", encoding="utf-8")

            cfg = EvaluationConfig(
                enabled=True,
                command_template=[
                    "python3",
                    "-c",
                    (
                        "import json,sys; "
                        "json.dump(dict(results=[dict(instance_id='tokio__1', resolved=True)]),"
                        " open(sys.argv[1],'w')); "
                        "print('ok')"
                    ),
                    "{attempt_dir}/eval_out.json",
                ],
                result_json_path_template="{attempt_dir}/eval_out.json",
                timeout_seconds=30,
            )

            result = evaluate_predictions_with_harness(
                config=cfg,
                run_id="run-4",
                attempt=1,
                benchmark="swebench_multilingual",
                prediction_path=predictions,
                attempt_dir=attempt_dir,
            )
            self.assertEqual(result.status, "ok")
            self.assertEqual(result.task_count, 1)
            self.assertEqual(result.solved_count, 1)
            self.assertIsNotNone(result.tasks_path)

    def test_parses_task_results_from_root_report_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            swebench_repo = Path(temp_dir) / "swebench_repo"
            swebench_repo.mkdir(parents=True, exist_ok=True)
            attempt_dir = swebench_repo / "attempt-01"
            attempt_dir.mkdir(parents=True, exist_ok=True)
            predictions = attempt_dir / "predictions.jsonl"
            predictions.write_text("{}", encoding="utf-8")

            cfg = EvaluationConfig(
                enabled=True,
                swebench_repo=swebench_repo,
                command_template=[
                    "python3",
                    "-c",
                    (
                        "import json,sys; "
                        "json.dump(dict("
                        "submitted_ids=['tokio__1','tokio__2','tokio__3'],"
                        "resolved_ids=['tokio__1'],"
                        "unresolved_ids=[],"
                        "error_ids=['tokio__2']"
                        "),"
                        " open(sys.argv[1],'w')); "
                        "print('ok')"
                    ),
                    "{run_id}-attempt-{attempt:02d}.json",
                ],
                timeout_seconds=30,
            )

            result = evaluate_predictions_with_harness(
                config=cfg,
                run_id="run-5",
                attempt=1,
                benchmark="swebench_multilingual",
                prediction_path=predictions,
                attempt_dir=attempt_dir,
            )
            self.assertEqual(result.status, "ok")
            self.assertEqual(result.task_count, 3)
            self.assertEqual(result.solved_count, 1)
            self.assertEqual(result.unsolved_count, 1)
            self.assertIsNotNone(result.tasks_path)


if __name__ == "__main__":
    unittest.main()
