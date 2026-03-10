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
                        "metadata": {"elapsed_ms": 2000, "tool_calls": 7},
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

            with outputs.results_csv.open("r", encoding="utf-8", newline="") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["solved"], "1")


if __name__ == "__main__":
    unittest.main()
