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
                        "model_patch": "diff --git a/a b/a\n",
                    }
                ],
            )
            write_jsonl(
                attempt_dir / "trace.jsonl",
                [
                    {
                        "instance_id": "tokio__1",
                        "status": "ok",
                        "metadata": {
                            "elapsed_ms": 2000,
                            "token_input": "1010",
                            "token_output": 220,
                            "estimated_cost": "0.04",
                            "tool_calls": "7",
                            "shell_commands": "3",
                            "file_reads": 8,
                            "search_actions": "2",
                        },
                    }
                ],
            )
            write_jsonl(
                attempt_dir / "evaluation.tasks.jsonl",
                [
                    {
                        "instance_id": "tokio__1",
                        "status": "solved",
                        "resolved": True,
                        "raw": {"resolved": True},
                    }
                ],
            )

            outputs = generate_appendix_files(
                run_roots=[run_root],
                output_dir=root / "reports",
            )

            self.assertTrue(outputs.per_task_jsonl.exists())
            self.assertTrue(outputs.results_csv.exists())
            per_task = [json.loads(line) for line in outputs.per_task_jsonl.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(len(per_task), 1)
            self.assertEqual(per_task[0]["status"], "solved")
            self.assertEqual(per_task[0]["token_input"], 1010)
            self.assertEqual(per_task[0]["token_output"], 220)
            self.assertEqual(per_task[0]["estimated_cost"], 0.04)
            self.assertEqual(per_task[0]["tool_calls"], 7)
            self.assertEqual(per_task[0]["shell_commands"], 3)
            self.assertEqual(per_task[0]["file_reads"], 8)
            self.assertEqual(per_task[0]["search_actions"], 2)

            with outputs.results_csv.open("r", encoding="utf-8", newline="") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["solved"], "1")
            self.assertNotEqual(rows[0]["median_tool_calls"], "")
            self.assertNotEqual(rows[0]["median_file_reads"], "")
            self.assertNotEqual(rows[0]["median_search_actions"], "")
            self.assertNotEqual(rows[0]["median_cost"], "")
            self.assertEqual(float(rows[0]["median_tool_calls"]), 7.0)
            self.assertEqual(float(rows[0]["median_file_reads"]), 8.0)
            self.assertEqual(float(rows[0]["median_search_actions"]), 2.0)
            self.assertEqual(float(rows[0]["median_cost"]), 0.04)

            markdown = outputs.results_markdown.read_text(encoding="utf-8")
            self.assertIn("7.000", markdown)
            self.assertIn("8.000", markdown)
            self.assertIn("2.000", markdown)
            self.assertIn("0.040", markdown)


if __name__ == "__main__":
    unittest.main()
