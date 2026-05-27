from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock
from unittest.mock import patch

from benchkit.common.config import EvaluationConfig
from benchkit.swebench.evaluation import (
    _build_evaluation_env,
    _build_evaluation_command,
    _resolve_python_bin,
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
                dataset_path=predictions,
                prediction_path=predictions,
                attempt_dir=attempt_dir,
                pro_patch_path=None,
            )
            self.assertIn("--report_dir", command)
            idx = command.index("--report_dir")
            self.assertEqual(command[idx + 1], str(attempt_dir.resolve()))

    def test_contextbench_command_resolves_relative_python_bin(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            contextbench_repo = root / "third_party" / "ContextBench"
            contextbench_repo.mkdir(parents=True, exist_ok=True)
            attempt_dir = root / "attempt-01"
            attempt_dir.mkdir(parents=True, exist_ok=True)
            predictions = attempt_dir / "predictions.jsonl"
            predictions.write_text("{}", encoding="utf-8")
            dataset = root / "dataset.jsonl"
            dataset.write_text("{}", encoding="utf-8")
            pred_converted = attempt_dir / "converted.predictions.jsonl"
            pred_converted.write_text("{}", encoding="utf-8")
            result_out = attempt_dir / "results.jsonl"

            cfg = EvaluationConfig(
                enabled=True,
                python_bin="./.venv/bin/python",
                contextbench_repo=contextbench_repo,
            )
            with patch("benchkit.swebench.evaluation.Path.cwd", return_value=root):
                command = _build_evaluation_command(
                    config=cfg,
                    run_id="run-0",
                    attempt=1,
                    benchmark="contextbench_verified",
                    dataset_path=dataset,
                    prediction_path=predictions,
                    attempt_dir=attempt_dir,
                    contextbench_pred_path=pred_converted,
                    contextbench_results_path=result_out,
                    pro_patch_path=None,
                )
            self.assertEqual(command[0], str((root / ".venv" / "bin" / "python").absolute()))

    def test_resolve_python_bin_preserves_relative_venv_path(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            with patch("benchkit.swebench.evaluation.Path.cwd", return_value=root):
                resolved = _resolve_python_bin("./.venv/bin/python")
            self.assertEqual(resolved, str(root / ".venv" / "bin" / "python"))

    def test_contextbench_preflight_reports_missing_dependencies(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            contextbench_repo = root / "third_party" / "ContextBench"
            contextbench_repo.mkdir(parents=True, exist_ok=True)
            attempt_dir = root / "attempt-01"
            attempt_dir.mkdir(parents=True, exist_ok=True)
            predictions = attempt_dir / "predictions.jsonl"
            predictions.write_text(
                '{"instance_id":"repo__task-1","model_patch":""}\n',
                encoding="utf-8",
            )
            dataset = root / "dataset.jsonl"
            dataset.write_text(
                (
                    '{"instance_id":"repo__task-1","repo":"owner/repo","base_commit":"abc",'
                    '"problem_statement":"x","metadata":{"repo_url":"https://github.com/owner/repo.git"}}\n'
                ),
                encoding="utf-8",
            )
            (attempt_dir / "trace.jsonl").write_text(
                '{"instance_id":"repo__task-1","metadata":{}}\n',
                encoding="utf-8",
            )

            cfg = EvaluationConfig(
                enabled=True,
                contextbench_repo=contextbench_repo,
                python_bin="python3",
            )

            preflight = Mock(return_value=Mock(returncode=1, stdout="tree_sitter,tree_sitter_languages\n", stderr=""))
            with patch("benchkit.swebench.evaluation.subprocess.run", preflight):
                result = evaluate_predictions_with_harness(
                    config=cfg,
                    run_id="run-preflight",
                    attempt=1,
                    benchmark="contextbench_verified",
                    dataset_path=dataset,
                    prediction_path=predictions,
                    attempt_dir=attempt_dir,
                )

            self.assertEqual(result.status, "error")
            self.assertIn("missing dependencies", str(result.error))
            self.assertIn("tree_sitter", str(result.error))

    def test_contextbench_preflight_allows_evaluation_command(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            contextbench_repo = root / "third_party" / "ContextBench"
            contextbench_repo.mkdir(parents=True, exist_ok=True)
            attempt_dir = root / "attempt-01"
            attempt_dir.mkdir(parents=True, exist_ok=True)
            predictions = attempt_dir / "predictions.jsonl"
            predictions.write_text(
                '{"instance_id":"repo__task-1","model_patch":""}\n',
                encoding="utf-8",
            )
            dataset = root / "dataset.jsonl"
            dataset.write_text(
                (
                    '{"instance_id":"repo__task-1","repo":"owner/repo","base_commit":"abc",'
                    '"problem_statement":"x","metadata":{"repo_url":"https://github.com/owner/repo.git"}}\n'
                ),
                encoding="utf-8",
            )
            (attempt_dir / "trace.jsonl").write_text(
                '{"instance_id":"repo__task-1","metadata":{}}\n',
                encoding="utf-8",
            )
            result_jsonl = attempt_dir / "result.jsonl"
            result_jsonl.write_text('{"instance_id":"x"}\n', encoding="utf-8")

            cfg = EvaluationConfig(
                enabled=True,
                contextbench_repo=contextbench_repo,
                python_bin="python3",
                command_template=[
                    "python3",
                    "-c",
                    "import pathlib; pathlib.Path('{contextbench_results_path}').write_text('{{\"instance_id\":\"x\"}}\\n')",
                ],
                timeout_seconds=30,
            )

            calls: list[list[str]] = []

            def _run(*args, **kwargs):  # type: ignore[no-untyped-def]
                cmd = args[0]
                calls.append(cmd)
                if len(calls) == 1:
                    return Mock(returncode=0, stdout="", stderr="")
                return Mock(returncode=0, stdout="", stderr="")

            with patch("benchkit.swebench.evaluation.subprocess.run", side_effect=_run):
                result = evaluate_predictions_with_harness(
                    config=cfg,
                    run_id="run-preflight-ok",
                    attempt=1,
                    benchmark="contextbench_verified",
                    dataset_path=dataset,
                    prediction_path=predictions,
                    attempt_dir=attempt_dir,
                )

            self.assertEqual(result.status, "ok")
            self.assertGreaterEqual(len(calls), 2)
            self.assertIn("tree_sitter_language_pack", calls[0][2])

    def test_contextbench_retries_checkout_failed_with_fresh_cache(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            contextbench_repo = root / "third_party" / "ContextBench"
            contextbench_repo.mkdir(parents=True, exist_ok=True)
            attempt_dir = root / "attempt-01"
            attempt_dir.mkdir(parents=True, exist_ok=True)
            predictions = attempt_dir / "predictions.jsonl"
            predictions.write_text(
                '{"instance_id":"repo__task-1","model_patch":""}\n',
                encoding="utf-8",
            )
            dataset = root / "dataset.jsonl"
            dataset.write_text(
                (
                    '{"instance_id":"repo__task-1","repo":"owner/repo","base_commit":"abc",'
                    '"problem_statement":"x","metadata":{"repo_url":"https://github.com/owner/repo.git"}}\n'
                ),
                encoding="utf-8",
            )
            (attempt_dir / "trace.jsonl").write_text(
                '{"instance_id":"repo__task-1","metadata":{}}\n',
                encoding="utf-8",
            )

            cfg = EvaluationConfig(
                enabled=True,
                contextbench_repo=contextbench_repo,
                python_bin="python3",
                timeout_seconds=30,
            )

            calls: list[list[str]] = []

            def _run(*args, **kwargs):  # type: ignore[no-untyped-def]
                cmd = args[0]
                calls.append(cmd)
                if len(cmd) >= 3 and cmd[1] == "-c":
                    return Mock(returncode=0, stdout="\n", stderr="")
                out_path = Path(cmd[cmd.index("--out") + 1])
                cache_path = Path(cmd[cmd.index("--cache") + 1])
                if cache_path.name == "contextbench_repos_retry":
                    out_path.write_text('{"instance_id":"repo__task-1"}\n', encoding="utf-8")
                else:
                    out_path.write_text(
                        '{"instance_id":"repo__task-1","error":"checkout_failed"}\n',
                        encoding="utf-8",
                    )
                return Mock(returncode=0, stdout="", stderr="")

            with patch("benchkit.swebench.evaluation.subprocess.run", side_effect=_run):
                result = evaluate_predictions_with_harness(
                    config=cfg,
                    run_id="run-checkout-retry",
                    attempt=1,
                    benchmark="contextbench_verified",
                    dataset_path=dataset,
                    prediction_path=predictions,
                    attempt_dir=attempt_dir,
                )

            self.assertEqual(result.status, "ok")
            self.assertEqual(result.task_count, 1)
            self.assertEqual(result.solved_count, 1)
            eval_calls = [
                call
                for call in calls
                if len(call) >= 3 and call[1] == "-m" and call[2] == "contextbench.evaluate"
            ]
            self.assertEqual(len(eval_calls), 2)
            self.assertNotEqual(
                eval_calls[0][eval_calls[0].index("--cache") + 1],
                eval_calls[1][eval_calls[1].index("--cache") + 1],
            )

    def test_build_evaluation_env_adds_swebench_repo_to_pythonpath(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            cwd = Path(temp_dir) / "repo"
            cwd.mkdir(parents=True, exist_ok=True)
            with patch.dict("os.environ", {"PATH": "/usr/bin", "HOME": temp_dir}, clear=True):
                env = _build_evaluation_env(cwd)
            self.assertEqual(env["PYTHONPATH"], str(cwd))

    def test_build_evaluation_env_adds_docker_desktop_helper_dir_when_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            docker_dir = Path(temp_dir) / ".docker"
            docker_dir.mkdir(parents=True, exist_ok=True)
            (docker_dir / "config.json").write_text('{"credsStore":"desktop"}', encoding="utf-8")
            helper_dir = Path(temp_dir) / "docker-bin"
            helper_dir.mkdir(parents=True, exist_ok=True)
            (helper_dir / "docker-credential-desktop").write_text("", encoding="utf-8")

            with patch.dict(
                "os.environ",
                {"PATH": "/usr/bin", "HOME": temp_dir},
                clear=True,
            ), patch(
                "benchkit.swebench.evaluation._docker_desktop_bin_dir",
                return_value=helper_dir,
            ):
                env = _build_evaluation_env(None)

            self.assertTrue(env["PATH"].startswith(f"{helper_dir}:"))

    def test_build_evaluation_env_does_not_touch_path_when_helper_already_present(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            docker_dir = Path(temp_dir) / ".docker"
            docker_dir.mkdir(parents=True, exist_ok=True)
            (docker_dir / "config.json").write_text('{"credsStore":"desktop"}', encoding="utf-8")

            with patch.dict(
                "os.environ",
                {"PATH": "/usr/bin", "HOME": temp_dir},
                clear=True,
            ), patch(
                "shutil.which",
                return_value="/usr/local/bin/docker-credential-desktop",
            ):
                env = _build_evaluation_env(None)

            self.assertEqual(env["PATH"], "/usr/bin")

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
                dataset_path=predictions,
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
                dataset_path=predictions,
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
                dataset_path=predictions,
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
                dataset_path=predictions,
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
                dataset_path=predictions,
                prediction_path=predictions,
                attempt_dir=attempt_dir,
            )
            self.assertEqual(result.status, "ok")
            self.assertEqual(result.task_count, 3)
            self.assertEqual(result.solved_count, 1)
            self.assertEqual(result.unsolved_count, 1)
            self.assertIsNotNone(result.tasks_path)

    def test_normalizes_swebench_pro_eval_results(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            attempt_dir = Path(temp_dir) / "attempt-01"
            attempt_dir.mkdir(parents=True, exist_ok=True)
            predictions = attempt_dir / "predictions.jsonl"
            predictions.write_text(
                '{"instance_id":"instance_a","model_patch":"diff --git a/x b/x\\n"}\n'
                '{"instance_id":"instance_b","model_patch":"diff --git a/y b/y\\n"}\n',
                encoding="utf-8",
            )

            cfg = EvaluationConfig(
                enabled=True,
                command_template=[
                    "python3",
                    "-c",
                    (
                        "import json,sys; "
                        "json.dump({{'instance_a': True, 'instance_b': False}}, open(sys.argv[1], 'w')); "
                        "print('ok')"
                    ),
                    "{attempt_dir}/eval_results.json",
                ],
                timeout_seconds=30,
            )

            result = evaluate_predictions_with_harness(
                config=cfg,
                run_id="run-6",
                attempt=1,
                benchmark="swebench_pro",
                dataset_path=predictions,
                prediction_path=predictions,
                attempt_dir=attempt_dir,
            )
            self.assertEqual(result.status, "ok")
            self.assertEqual(result.task_count, 2)
            self.assertEqual(result.solved_count, 1)
            self.assertEqual(result.unsolved_count, 1)
            self.assertTrue(
                any(attempt_dir.glob("*.swebench_pro.patches.json"))
            )
            self.assertTrue(
                any(attempt_dir.glob("*.swebench_pro.results.json"))
            )


if __name__ == "__main__":
    unittest.main()
