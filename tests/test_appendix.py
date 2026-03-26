from __future__ import annotations

from pathlib import Path
import csv
import json
import tempfile
import unittest

from benchkit.common.io import write_json, write_jsonl
from benchkit.swebench.appendix import generate_appendix_files


class AppendixTests(unittest.TestCase):
    def test_generate_appendix_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            run_root = root / "run-1"
            attempt_1 = run_root / "attempts" / "attempt-01"
            attempt_2 = run_root / "attempts" / "attempt-02"
            attempt_3 = run_root / "attempts" / "attempt-03"
            attempt_4 = run_root / "attempts" / "attempt-04"
            for attempt_dir in (attempt_1, attempt_2, attempt_3, attempt_4):
                attempt_dir.mkdir(parents=True, exist_ok=True)

            write_json(
                run_root / "run_manifest.json",
                {
                    "benchmark": "swebench_multilingual",
                    "dataset_path": "datasets/sample.jsonl",
                    "split": "dev",
                    "condition": "baseline",
                    "agent": {"id": "claude_code"},
                    "model": {"resolved_name": "claude-opus-4-6"},
                },
            )
            write_jsonl(
                run_root / "instances.jsonl",
                [
                    {
                        "instance_id": "tokio__1",
                        "repo": "tokio-rs/tokio",
                        "language": "rust",
                        "base_commit": "abc",
                        "problem_statement": "Fix",
                        "metadata": {},
                    }
                ],
            )

            attempts = [
                (attempt_1, 2000, 0.04, "solved"),
                (attempt_2, 5000, 0.10, "unsolved"),
                (attempt_3, None, None, "unsolved"),
                (attempt_4, 8000, 0.16, "invalid"),
            ]
            for index, (attempt_dir, elapsed_ms, estimated_cost, status) in enumerate(attempts, start=1):
                write_jsonl(
                    attempt_dir / "predictions.jsonl",
                    [
                        {
                            "instance_id": "tokio__1",
                            "model_name_or_path": "agent:claude_code",
                            "model_patch": "diff --git a/a b/a\n",
                        }
                    ],
                )
                metadata = {
                    "token_input": str(1000 + index),
                    "token_output": 220 + index,
                    "tool_calls": "7",
                    "shell_commands": "3",
                    "file_reads": 8,
                    "search_actions": "2",
                }
                if elapsed_ms is not None:
                    metadata["elapsed_ms"] = elapsed_ms
                if estimated_cost is not None:
                    metadata["estimated_cost"] = str(estimated_cost)
                write_jsonl(
                    attempt_dir / "trace.jsonl",
                    [
                        {
                            "instance_id": "tokio__1",
                            "status": "ok" if status != "invalid" else "error",
                            "metadata": metadata,
                        }
                    ],
                )
                write_jsonl(
                    attempt_dir / "evaluation.tasks.jsonl",
                    [
                        {
                            "instance_id": "tokio__1",
                            "status": status,
                            "resolved": status == "solved",
                            "raw": {"resolved": status == "solved"},
                        }
                    ],
                )

            outputs = generate_appendix_files(
                run_roots=[run_root],
                output_dir=root / "reports",
            )

            self.assertTrue(outputs.per_task_jsonl.exists())
            self.assertTrue(outputs.results_csv.exists())
            per_task = [
                json.loads(line)
                for line in outputs.per_task_jsonl.read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual(len(per_task), 4)
            self.assertEqual(per_task[0]["status"], "solved")
            self.assertEqual(per_task[0]["token_input"], 1001)
            self.assertEqual(per_task[0]["token_output"], 221)
            self.assertEqual(per_task[0]["estimated_cost"], 0.04)
            self.assertEqual(per_task[0]["tool_calls"], 7)
            self.assertEqual(per_task[0]["shell_commands"], 3)
            self.assertEqual(per_task[0]["file_reads"], 8)
            self.assertEqual(per_task[0]["search_actions"], 2)
            self.assertIsNone(per_task[2]["runtime_sec"])
            self.assertIsNone(per_task[2]["estimated_cost"])

            with outputs.results_csv.open("r", encoding="utf-8", newline="") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(len(rows), 1)
            row = rows[0]
            self.assertEqual(row["solved"], "1")
            self.assertNotEqual(row["median_tool_calls"], "")
            self.assertNotEqual(row["median_file_reads"], "")
            self.assertNotEqual(row["median_search_actions"], "")
            self.assertNotEqual(row["median_cost"], "")
            self.assertEqual(float(row["median_tool_calls"]), 7.0)
            self.assertEqual(float(row["median_file_reads"]), 8.0)
            self.assertEqual(float(row["median_search_actions"]), 2.0)
            self.assertEqual(float(row["median_cost"]), 0.1)
            self.assertEqual(float(row["mean_runtime_sec"]), 5.0)
            self.assertEqual(float(row["variance_runtime_sec"]), 9.0)
            self.assertEqual(float(row["stddev_runtime_sec"]), 3.0)
            self.assertEqual(float(row["mean_cost"]), 0.1)
            self.assertAlmostEqual(float(row["variance_cost"]), 0.0036)
            self.assertAlmostEqual(float(row["stddev_cost"]), 0.06)

            markdown = outputs.results_markdown.read_text(encoding="utf-8")
            self.assertIn("7.000", markdown)
            self.assertIn("8.000", markdown)
            self.assertIn("2.000", markdown)
            self.assertIn("0.100", markdown)
            self.assertIn("9.000", markdown)
            self.assertIn("0.004", markdown)

    def test_single_non_null_value_yields_mean_only(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            run_root = root / "run-1"
            attempt_dir = run_root / "attempts" / "attempt-01"
            attempt_dir.mkdir(parents=True, exist_ok=True)

            write_json(
                run_root / "run_manifest.json",
                {
                    "benchmark": "swebench_multilingual",
                    "dataset_path": "datasets/sample.jsonl",
                    "split": "dev",
                    "condition": "baseline",
                    "agent": {"id": "claude_code"},
                    "model": {"resolved_name": "claude-opus-4-6"},
                },
            )
            write_jsonl(
                run_root / "instances.jsonl",
                [
                    {
                        "instance_id": "tokio__1",
                        "repo": "tokio-rs/tokio",
                        "language": "rust",
                        "base_commit": "abc",
                        "problem_statement": "Fix",
                        "metadata": {},
                    }
                ],
            )
            write_jsonl(
                attempt_dir / "predictions.jsonl",
                [
                    {
                        "instance_id": "tokio__1",
                        "model_name_or_path": "agent:claude_code",
                        "model_patch": "",
                    }
                ],
            )
            write_jsonl(
                attempt_dir / "trace.jsonl",
                [
                    {
                        "instance_id": "tokio__1",
                        "status": "ok",
                        "metadata": {"elapsed_ms": 3000, "estimated_cost": "0.05"},
                    }
                ],
            )
            write_jsonl(attempt_dir / "evaluation.tasks.jsonl", [])

            outputs = generate_appendix_files(
                run_roots=[run_root],
                output_dir=root / "reports",
            )

            with outputs.results_csv.open("r", encoding="utf-8", newline="") as handle:
                row = next(csv.DictReader(handle))
            self.assertEqual(float(row["mean_runtime_sec"]), 3.0)
            self.assertEqual(float(row["mean_cost"]), 0.05)
            self.assertEqual(row["variance_runtime_sec"], "")
            self.assertEqual(row["stddev_runtime_sec"], "")
            self.assertEqual(row["variance_cost"], "")
            self.assertEqual(row["stddev_cost"], "")


if __name__ == "__main__":
    unittest.main()
