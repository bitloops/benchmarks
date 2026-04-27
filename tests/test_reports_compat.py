from __future__ import annotations

from pathlib import Path
import json
import tempfile
import unittest
from unittest.mock import patch

from benchkit.common.io import write_json, write_jsonl
from benchkit.swebench.reports import generate_run_summary_files


class ReportCompatibilityTests(unittest.TestCase):
    def test_generate_run_summary_includes_sample_compatibility_fields(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            run_root = root / "run-1"
            attempt_dir = run_root / "attempts" / "attempt-01"
            attempt_dir.mkdir(parents=True, exist_ok=True)

            write_json(
                run_root / "run_manifest.json",
                {
                    "run_id": "run-1",
                    "benchmark": "swebench_multilingual",
                    "dataset_path": "datasets/sample.jsonl",
                    "split": "dev",
                    "language": "rust",
                    "condition": "with_bitloops",
                    "agent": {"id": "opencode"},
                    "model": {
                        "canonical_name": "qwen3.6-plus-free",
                        "resolved_name": "opencode/qwen3.6-plus-free",
                    },
                },
            )
            write_json(
                run_root / "summary.json",
                {
                    "run_id": "run-1",
                    "language": "rust",
                    "condition": "with_bitloops",
                    "total_instances": 1,
                    "bitloops_cli_commit_sha": "379427b9ca3ab379d05ccd6f90640a7bcd2dff04",
                    "ai_agent_and_model_used_for_analysis": "Codex (GPT-5.5 Thinking)",
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
                        "model_name_or_path": "agent:opencode",
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
                            "tool_calls": "9",
                            "command": ["opencode", "run", "--format", "json"],
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

            with patch(
                "benchkit.swebench.reports._probe_command_version",
                return_value="opencode 1.4.10",
            ):
                outputs = generate_run_summary_files(
                    run_roots=[run_root],
                    output_dir=root / "reports",
                )

            rows = [
                json.loads(line)
                for line in outputs.run_summary_jsonl.read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual(len(rows), 1)
            row = rows[0]
            self.assertEqual(row["result"], "solved")
            self.assertEqual(
                row["bitloops_cli_commit_sha"],
                "379427b9ca3ab379d05ccd6f90640a7bcd2dff04",
            )
            self.assertEqual(row["internal_tool_calls"], 9)
            self.assertEqual(
                row["ai_agent_and_model_used_for_analysis"],
                "Codex (GPT-5.5 Thinking)",
            )
            self.assertTrue(str(row["log_jsonl_link"]).endswith("attempt-01/trace.jsonl"))


if __name__ == "__main__":
    unittest.main()
