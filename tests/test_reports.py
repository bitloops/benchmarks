from __future__ import annotations

from pathlib import Path
import csv
import json
import tempfile
import unittest
from unittest.mock import patch

from benchkit.common.io import write_json, write_jsonl
from benchkit.swebench.reports import (
    _resolve_agent_cli_version,
    generate_report_files,
    generate_run_summary_files,
)


class ReportsTests(unittest.TestCase):
    def test_generate_run_summary_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            run_root = self._write_run_fixture(root)

            with patch(
                "benchkit.swebench.reports._probe_command_version",
                return_value="opencode 1.4.10",
            ):
                outputs = generate_run_summary_files(
                    run_roots=[run_root],
                    output_dir=root / "reports",
                )

            self.assertTrue(outputs.run_summary_jsonl.exists())
            self.assertTrue(outputs.run_summary_csv.exists())

            rows = [
                json.loads(line)
                for line in outputs.run_summary_jsonl.read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual(len(rows), 1)
            row = rows[0]
            self.assertEqual(row["run_id"], "run-1")
            self.assertEqual(row["engineer"], "Antonis")
            self.assertEqual(row["agent"], "opencode")
            self.assertEqual(row["agent_cli_version"], "opencode 1.4.10")
            self.assertEqual(row["primary_session_id"], "init-session-1")
            self.assertEqual(row["session_id_count"], 3)
            self.assertEqual(
                row["session_ids"],
                ["init-session-1", "init-session-3", "init-session-4"],
            )
            self.assertTrue(row["bitloops_enabled"])
            self.assertTrue(row["bitloops_sync"])
            self.assertTrue(row["bitloops_ingest"])
            self.assertEqual(row["bitloops_embeddings_runtime"], "platform")
            self.assertFalse(row["bitloops_no_embeddings"])
            self.assertEqual(row["bitloops_summary_mode"], "off")
            self.assertEqual(row["bitloops_embedding_mode"], "semantic_aware_once")
            self.assertEqual(row["task_attempt_rows"], 4)
            self.assertEqual(row["unique_tasks"], 1)
            self.assertEqual(row["solved_task_attempts"], 1)
            self.assertEqual(row["unsolved_task_attempts"], 2)
            self.assertEqual(row["invalid_task_attempts"], 1)
            self.assertAlmostEqual(row["attempt_solve_rate"], 0.25)
            self.assertEqual(row["tasks_solved_at_least_once"], 1)
            self.assertAlmostEqual(row["task_solve_rate_at_least_once"], 1.0)
            self.assertEqual(row["input_tokens_total"], 4010)
            self.assertEqual(row["output_tokens_total"], 890)
            self.assertEqual(row["cache_creation_input_tokens_total"], 50)
            self.assertEqual(row["cache_read_input_tokens_total"], 90)
            self.assertEqual(row["total_input_processed_tokens_total"], 4150)
            self.assertEqual(row["total_processed_tokens_total"], 5040)
            self.assertNotIn("derived_total_input_processed_tokens", row)
            self.assertNotIn("derived_total_processed_tokens", row)
            self.assertAlmostEqual(row["runtime_total_sec"], 15.0)
            self.assertAlmostEqual(row["runtime_mean_sec"], 5.0)
            self.assertAlmostEqual(row["runtime_median_sec"], 5.0)
            self.assertEqual(len(row["trace_jsonl_paths"]), 4)
            self.assertEqual(len(row["prediction_jsonl_paths"]), 4)
            self.assertEqual(len(row["evaluation_report_paths"]), 4)

            with outputs.run_summary_csv.open("r", encoding="utf-8", newline="") as handle:
                csv_rows = list(csv.DictReader(handle))
            self.assertEqual(len(csv_rows), 1)
            self.assertEqual(csv_rows[0]["run_id"], "run-1")
            self.assertEqual(csv_rows[0]["engineer"], "Antonis")
            self.assertEqual(csv_rows[0]["agent_cli_version"], "opencode 1.4.10")
            self.assertEqual(
                csv_rows[0]["session_ids"],
                "init-session-1;init-session-3;init-session-4",
            )
            self.assertNotIn("derived_total_input_processed_tokens", csv_rows[0])
            self.assertNotIn("derived_total_processed_tokens", csv_rows[0])

    def test_generate_report_files_keeps_existing_appendix_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            run_root = self._write_run_fixture(root)

            with patch(
                "benchkit.swebench.reports._probe_command_version",
                return_value="opencode 1.4.10",
            ):
                outputs = generate_report_files(
                    run_roots=[run_root],
                    output_dir=root / "reports",
                )

            self.assertTrue(outputs.appendix.per_task_jsonl.exists())
            self.assertTrue(outputs.appendix.results_csv.exists())
            self.assertTrue(outputs.run_summary.run_summary_jsonl.exists())
            self.assertTrue(outputs.run_summary.run_summary_csv.exists())

    def test_generate_run_summary_files_normalizes_codex_cached_input(self) -> None:
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
                    "condition": "baseline",
                    "agent": {"id": "codex"},
                    "model": {
                        "canonical_name": "gpt-5.4",
                        "resolved_name": "gpt-5.4",
                    },
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
                        "model_name_or_path": "agent:codex",
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
                            "token_input": 12024,
                            "token_output": 27,
                            "cached_input_tokens": 3456,
                            "token_input_uncached": 8568,
                            "total_tokens": 12051,
                            "tool_calls": 1,
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

            outputs = generate_run_summary_files(
                run_roots=[run_root],
                output_dir=root / "reports",
            )
            rows = [
                json.loads(line)
                for line in outputs.run_summary_jsonl.read_text(encoding="utf-8").splitlines()
            ]
            row = rows[0]

            self.assertEqual(row["input_tokens_total"], 8568)
            self.assertEqual(row["output_tokens_total"], 27)
            self.assertEqual(row["cache_read_input_tokens_total"], 3456)
            self.assertEqual(row["cache_creation_input_tokens_total"], 0)
            self.assertEqual(row["total_input_processed_tokens_total"], 12024)
            self.assertEqual(row["total_processed_tokens_total"], 12051)

    def test_resolve_agent_cli_version_prefers_metadata_before_probe(self) -> None:
        version = _resolve_agent_cli_version(
            metadata_rows=[
                {
                    "agent_cli_version": "OpenCode 1.4.10",
                    "command": ["opencode", "run", "--format", "json"],
                }
            ]
        )

        self.assertEqual(version, "OpenCode 1.4.10")

    def test_resolve_agent_cli_version_falls_back_to_command_probe(self) -> None:
        with patch(
            "benchkit.swebench.reports._probe_command_version",
            return_value="opencode 1.4.10",
        ) as mock_probe:
            version = _resolve_agent_cli_version(
                metadata_rows=[
                    {
                        "command": ["opencode", "run", "--format", "json"],
                    }
                ]
            )

        self.assertEqual(version, "opencode 1.4.10")
        mock_probe.assert_called_once_with(["opencode", "run", "--format", "json"])

    def _write_run_fixture(self, root: Path) -> Path:
        run_root = root / "run-1"
        attempt_dirs = [
            run_root / "attempts" / "attempt-01",
            run_root / "attempts" / "attempt-02",
            run_root / "attempts" / "attempt-03",
            run_root / "attempts" / "attempt-04",
        ]
        for attempt_dir in attempt_dirs:
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
                "bitloops_enabled": True,
                "bitloops_sandbox_mode": "per_task_daemon",
                "max_workers": 2,
                "attempts": 4,
                "started_at_utc": "2026-04-24T10:00:00+00:00",
                "engineer": "Antonis",
                "agent": {"id": "opencode"},
                "model": {
                    "canonical_name": "qwen3.6-plus-free",
                    "resolved_name": "opencode/qwen3.6-plus-free",
                },
                "evaluation": {"enabled": True},
            },
        )
        write_json(
            run_root / "summary.json",
            {
                "run_id": "run-1",
                "benchmark": "swebench_multilingual",
                "condition": "with_bitloops",
                "bitloops_enabled": True,
                "bitloops_sandbox_mode": "per_task_daemon",
                "workspace_isolation_mode": "task_scoped",
                "dataset_path": "datasets/sample.jsonl",
                "split": "dev",
                "language": "rust",
                "total_instances": 1,
                "attempts": 4,
                "max_workers": 2,
                "total_agent_calls": 4,
                "successful_agent_calls": 3,
                "failed_agent_calls": 1,
                "started_at_utc": "2026-04-24T10:00:00+00:00",
                "finished_at_utc": "2026-04-24T10:30:00+00:00",
                "run_root": str(run_root),
                "model_resolution": {
                    "canonical_name": "qwen3.6-plus-free",
                    "resolved_name": "opencode/qwen3.6-plus-free",
                },
                "evaluation": {
                    "enabled": True,
                    "attempts": [
                        {
                            "attempt": 1,
                            "report_path": str(attempt_dirs[0] / "evaluation.json"),
                        },
                        {
                            "attempt": 2,
                            "report_path": str(attempt_dirs[1] / "evaluation.json"),
                        },
                        {
                            "attempt": 3,
                            "report_path": str(attempt_dirs[2] / "evaluation.json"),
                        },
                        {
                            "attempt": 4,
                            "report_path": str(attempt_dirs[3] / "evaluation.json"),
                        },
                    ],
                },
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
            (attempt_dirs[0], 2000, 0.04, "solved"),
            (attempt_dirs[1], 5000, 0.10, "unsolved"),
            (attempt_dirs[2], None, None, "unsolved"),
            (attempt_dirs[3], 8000, 0.16, "invalid"),
        ]
        for index, (attempt_dir, elapsed_ms, estimated_cost, status) in enumerate(
            attempts,
            start=1,
        ):
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
            metadata = {
                "token_input": str(1000 + index),
                "token_output": 220 + index,
                "reasoning_output_tokens": 30 + index,
                "total_tokens": 1300 + index,
                "cache_creation_input_tokens": 10 + index,
                "cache_read_input_tokens": 20 + index,
                "cache_creation_ephemeral_5m_input_tokens": 7 + index,
                "cache_creation_ephemeral_1h_input_tokens": 3,
                "tool_calls": "7",
                "shell_commands": "3",
                "file_reads": 8,
                "search_actions": "2",
                "command": ["opencode", "run", "--format", "json", f"Prompt for attempt {index}"],
                "bitloops_sync": True,
                "bitloops_ingest": True,
                "bitloops_embeddings_runtime": "platform",
                "bitloops_no_embeddings": False,
                "bitloops_summary_mode": "off",
                "bitloops_embedding_mode": "semantic_aware_once",
                "current_init_session_id": f"init-session-{1 if index < 3 else index}",
                "session": {
                    "initSessionId": f"init-session-{1 if index < 3 else index}",
                },
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

        return run_root


if __name__ == "__main__":
    unittest.main()
