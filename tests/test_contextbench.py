from __future__ import annotations

from pathlib import Path
import json
import tempfile
import unittest

from benchkit.common.config import load_run_config
from benchkit.contextbench.evaluation import (
    build_contextbench_gold_jsonl,
    build_contextbench_prediction_jsonl,
    parse_contextbench_results_jsonl,
)
from benchkit.contextbench.trajectory import build_contextbench_traj_data


class ContextBenchConfigTests(unittest.TestCase):
    def test_load_run_config_applies_codex_contextbench_preset(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / "config.toml"
            config_path.write_text(
                """
preset = "codex_contextbench"

[run]
dataset_path = "datasets/contextbench_verified.train.jsonl"
max_instances = 1

[model]
name = "gpt-5.4"
                """.strip(),
                encoding="utf-8",
            )

            cfg = load_run_config(config_path, mode="baseline")

        self.assertEqual(cfg.benchmark, "contextbench_verified")
        self.assertEqual(cfg.condition, "baseline")
        self.assertEqual(cfg.agent.id, "codex")
        self.assertEqual(cfg.evaluation.contextbench_repo, Path("third_party/ContextBench"))


class ContextBenchTrajectoryTests(unittest.TestCase):
    def test_build_contextbench_traj_data_extracts_read_and_bash_ranges(self) -> None:
        traj_data = build_contextbench_traj_data(
            tool_invocations_curated=[
                {"tool": "Read", "path": "src/app.py", "offset": 9, "limit": 11},
                {"tool": "Bash", "command": "sed -n '20,40p' src/lib.rs"},
            ],
            tool_invocations_raw=[],
        )

        self.assertEqual(
            traj_data["pred_files"],
            ["src/app.py", "src/lib.rs"],
        )
        self.assertEqual(
            traj_data["pred_spans"]["src/app.py"][0],
            {"start": 10, "end": 20},
        )
        self.assertEqual(
            traj_data["pred_spans"]["src/lib.rs"][0],
            {"start": 20, "end": 40},
        )

    def test_build_contextbench_traj_data_falls_back_to_edit_when_no_primary_tools(self) -> None:
        traj_data = build_contextbench_traj_data(
            tool_invocations_curated=[
                {
                    "tool": "Edit",
                    "filePath": "src/transform.py",
                    "raw_event": {
                        "state": {
                            "metadata": {
                                "filediff": {
                                    "patch": "\n".join(
                                        [
                                            "+++ b/src/transform.py",
                                            "@@ -9,1 +10,3 @@",
                                            "+line",
                                        ]
                                    )
                                }
                            }
                        }
                    },
                },
                {
                    "tool": "todowrite",
                    "raw_input_json": "{\"todos\":[{\"content\":\"x\"}]}",
                },
            ],
            tool_invocations_raw=[],
        )

        self.assertEqual(traj_data["pred_files"], ["src/transform.py"])
        self.assertEqual(
            traj_data["pred_spans"]["src/transform.py"][0],
            {"start": 10, "end": 12},
        )

    def test_build_contextbench_traj_data_hydrates_curated_edit_from_raw(self) -> None:
        traj_data = build_contextbench_traj_data(
            tool_invocations_curated=[
                {
                    "tool": "Edit",
                    "tool_use_id": "call_123",
                    "call_index": 1,
                }
            ],
            tool_invocations_raw=[
                {
                    "tool": "Edit",
                    "tool_use_id": "call_123",
                    "call_index": 1,
                    "input": {
                        "filePath": "/tmp/workspace/repo/src/transform.py",
                    },
                    "raw_event": {
                        "state": {
                            "metadata": {
                                "diff": "\n".join(
                                    [
                                        "--- /tmp/workspace/repo/src/transform.py",
                                        "+++ /tmp/workspace/repo/src/transform.py",
                                        "@@ -3,1 +8,2 @@",
                                    ]
                                )
                            }
                        }
                    },
                }
            ],
        )

        self.assertEqual(
            traj_data["pred_files"],
            ["tmp/workspace/repo/src/transform.py"],
        )
        self.assertEqual(
            traj_data["pred_spans"]["tmp/workspace/repo/src/transform.py"][0],
            {"start": 8, "end": 9},
        )

    def test_build_contextbench_traj_data_does_not_force_edit_when_primary_exists(self) -> None:
        traj_data = build_contextbench_traj_data(
            tool_invocations_curated=[
                {"tool": "Read", "path": "src/primary.py", "offset": 0, "limit": 5},
                {
                    "tool": "Edit",
                    "filePath": "src/edited.py",
                    "raw_event": {
                        "state": {
                            "metadata": {
                                "filediff": {
                                    "patch": "\n".join(
                                        [
                                            "+++ b/src/edited.py",
                                            "@@ -1,1 +3,2 @@",
                                            "+line",
                                        ]
                                    )
                                }
                            }
                        }
                    },
                },
            ],
            tool_invocations_raw=[],
        )

        self.assertEqual(traj_data["pred_files"], ["src/primary.py"])
        self.assertIn("src/primary.py", traj_data["pred_spans"])
        self.assertNotIn("src/edited.py", traj_data["pred_spans"])


class ContextBenchEvaluationParseTests(unittest.TestCase):
    def test_build_contextbench_gold_jsonl_parses_gold_context_string(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            dataset_path = root / "dataset.jsonl"
            output_path = root / "gold.normalized.jsonl"
            dataset_path.write_text(
                json.dumps(
                    {
                        "instance_id": "SWE-Bench-Verified__python__maintenance__bugfix__deb49033",
                        "original_inst_id": "astropy__astropy-13398",
                        "repo": "astropy/astropy",
                        "repo_url": "https://github.com/astropy/astropy.git",
                        "base_commit": "abc123",
                        "gold_context": json.dumps(
                            [
                                {
                                    "file": "astropy/coordinates/attributes.py",
                                    "start_line": 10,
                                    "end_line": 20,
                                }
                            ]
                        ),
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            count = build_contextbench_gold_jsonl(
                dataset_path=dataset_path,
                output_path=output_path,
            )

            self.assertEqual(count, 1)
            row = json.loads(output_path.read_text(encoding="utf-8").splitlines()[0])
            self.assertEqual(row["inst_id"], "astropy__astropy-13398")
            self.assertEqual(row["commit"], "abc123")
            self.assertEqual(row["repo_url"], "https://github.com/astropy/astropy.git")
            self.assertEqual(len(row["gold_ctx"]), 1)
            self.assertEqual(
                row["gold_ctx"][0]["file"],
                "astropy/coordinates/attributes.py",
            )

    def test_build_contextbench_prediction_uses_original_inst_id_for_evaluator(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            dataset_path = root / "dataset.jsonl"
            prediction_path = root / "predictions.jsonl"
            trace_path = root / "trace.jsonl"
            output_path = root / "converted.jsonl"
            dataset_path.write_text(
                json.dumps(
                    {
                        "instance_id": "SWE-Bench-Verified__python__maintenance__bugfix__deb49033",
                        "original_inst_id": "astropy__astropy-13398",
                        "repo": "astropy/astropy",
                        "base_commit": "abc",
                        "problem_statement": "x",
                        "gold_context": [],
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            prediction_path.write_text(
                json.dumps(
                    {
                        "instance_id": "SWE-Bench-Verified__python__maintenance__bugfix__deb49033",
                        "model_patch": "",
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            trace_path.write_text(
                json.dumps(
                    {
                        "instance_id": "SWE-Bench-Verified__python__maintenance__bugfix__deb49033",
                        "metadata": {},
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            count = build_contextbench_prediction_jsonl(
                benchmark="contextbench_verified",
                dataset_path=dataset_path,
                prediction_path=prediction_path,
                trace_path=trace_path,
                output_path=output_path,
            )

            self.assertEqual(count, 1)
            row = json.loads(output_path.read_text(encoding="utf-8").splitlines()[0])
            self.assertEqual(row["instance_id"], "astropy__astropy-13398")
            self.assertEqual(
                row["benchkit_instance_id"],
                "SWE-Bench-Verified__python__maintenance__bugfix__deb49033",
            )

    def test_parse_contextbench_results_jsonl_extracts_core_metrics(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            result_path = root / "contextbench.results.jsonl"
            parsed_path = root / "evaluation.parsed.json"
            tasks_path = root / "evaluation.tasks.jsonl"

            row = {
                "instance_id": "astropy__astropy-12907",
                "final": {
                    "file": {"coverage": 0.5, "precision": 0.25},
                    "symbol": {"coverage": 0.2, "precision": 0.1},
                    "span": {"coverage": 0.3, "precision": 0.2},
                    "line": {"coverage": 0.4, "precision": 0.3},
                },
                "trajectory": {
                    "auc_coverage": {"file": 0.7, "symbol": 0.6, "span": 0.5, "line": 0.4},
                    "redundancy": {"file": 1.1, "symbol": 1.2, "span": 1.3, "line": 1.4},
                },
                "editloc": {"recall": 0.8, "precision": 0.9},
            }
            result_path.write_text(json.dumps(row) + "\n", encoding="utf-8")

            parsed = parse_contextbench_results_jsonl(
                result_path=result_path,
                parsed_path=parsed_path,
                tasks_path=tasks_path,
            )

            self.assertEqual(parsed["task_count"], 1)
            tasks = [json.loads(line) for line in tasks_path.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(tasks[0]["status"], "solved")
            self.assertEqual(tasks[0]["final_file_coverage"], 0.5)
            self.assertEqual(tasks[0]["traj_auc_file"], 0.7)
            self.assertEqual(tasks[0]["editloc_precision"], 0.9)

    def test_parse_contextbench_results_maps_back_to_benchkit_instance_id(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            result_path = root / "contextbench.results.jsonl"
            pred_path = root / "contextbench.predictions.jsonl"
            parsed_path = root / "evaluation.parsed.json"
            tasks_path = root / "evaluation.tasks.jsonl"

            result_path.write_text(
                json.dumps(
                    {
                        "instance_id": "astropy__astropy-13398",
                        "final": {"file": {"coverage": 0.5, "precision": 0.5}},
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            pred_path.write_text(
                json.dumps(
                    {
                        "instance_id": "astropy__astropy-13398",
                        "benchkit_instance_id": "SWE-Bench-Verified__python__maintenance__bugfix__deb49033",
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            parse_contextbench_results_jsonl(
                result_path=result_path,
                parsed_path=parsed_path,
                tasks_path=tasks_path,
                prediction_path=pred_path,
            )

            tasks = [json.loads(line) for line in tasks_path.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(
                tasks[0]["instance_id"],
                "SWE-Bench-Verified__python__maintenance__bugfix__deb49033",
            )
