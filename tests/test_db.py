from __future__ import annotations

from pathlib import Path
import csv
import sqlite3
import tempfile
import unittest

from benchkit.common.io import write_json
from benchkit.swebench.db import import_appendix_csv_to_sqlite


class SqliteImportTests(unittest.TestCase):
    def test_import_appendix_csv_to_sqlite(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            run_root = root / "run-1"
            run_root.mkdir(parents=True, exist_ok=True)
            appendix_csv = root / "appendix_minimal_per_task_log.csv"
            db_path = root / "benchmarks.sqlite"

            write_json(
                run_root / "run_manifest.json",
                {
                    "run_id": "run-123",
                    "benchmark": "swebench_multilingual",
                    "condition": "baseline",
                    "dataset_path": "datasets/sample.jsonl",
                    "split": "test",
                    "attempts": 2,
                    "max_workers": 3,
                    "started_at_utc": "2026-03-26T10:00:00+00:00",
                    "agent": {"id": "claude_code"},
                    "model": {
                        "canonical_name": "opus-4-6",
                        "resolved_name": "eu.anthropic.claude-opus-4-6-v1",
                    },
                    "evaluation": {"enabled": True},
                },
            )

            with appendix_csv.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(
                    handle,
                    fieldnames=[
                        "task_id              ",
                        " attempt",
                        " benchmark            ",
                        " benchmark_version                                        ",
                        " repo          ",
                        " repo_label",
                        " language ",
                        " agent      ",
                        " model_version                  ",
                        " condition",
                        " status",
                        " runtime_sec",
                        " input_tokens",
                        " output_tokens",
                        " cache_read_input_tokens",
                        " cache_creation_input_tokens",
                        " total_input_processed_tokens",
                        " total_processed_tokens",
                        " estimated_cost     ",
                        " tool_calls",
                        " shell_commands",
                        " file_reads",
                        " search_actions",
                        " files_edited",
                        " patch_size",
                        " first_file_opened",
                        " first_file_edited",
                        " first_test_command",
                        " bitloops_context_tokens",
                        " evaluator_result",
                    ],
                )
                writer.writeheader()
                writer.writerow(
                    {
                        "task_id              ": "astral-sh__ruff-15309",
                        " attempt": "       1",
                        " benchmark            ": "swebench_multilingual",
                        " benchmark_version                                        ": "datasets/sample.jsonl|split=test",
                        " repo          ": "astral-sh/ruff",
                        " repo_label": "astral-sh ",
                        " language ": "astral-sh",
                        " agent      ": "claude_code",
                        " model_version                  ": "eu.anthropic.claude-opus-4-6-v1",
                        " condition": "baseline ",
                        " status": "solved",
                        " runtime_sec": "     208.408",
                        " input_tokens": "         625",
                        " output_tokens": "        6701",
                        " cache_read_input_tokens": "         55",
                        " cache_creation_input_tokens": "         22",
                        " total_input_processed_tokens": "         702",
                        " total_processed_tokens": "        7403",
                        " estimated_cost     ": "0.8117682          ",
                        " tool_calls": "         29",
                        " shell_commands": "               ",
                        " file_reads": "           ",
                        " search_actions": "              0",
                        " files_edited": "            3",
                        " patch_size": "       4078",
                        " first_file_opened": "                  ",
                        " first_file_edited": "                  ",
                        " first_test_command": "                   ",
                        " bitloops_context_tokens": "                        ",
                        " evaluator_result": '{"source": "summary_ids", "submitted": true, "resolved": true, "unresolved": false, "error": false}',
                    }
                )
                writer.writerow(
                    {
                        "task_id              ": "astral-sh__ruff-15330",
                        " attempt": "       2",
                        " benchmark            ": "swebench_multilingual",
                        " benchmark_version                                        ": "datasets/sample.jsonl|split=test",
                        " repo          ": "astral-sh/ruff",
                        " repo_label": "astral-sh ",
                        " language ": "astral-sh",
                        " agent      ": "claude_code",
                        " model_version                  ": "eu.anthropic.claude-opus-4-6-v1",
                        " condition": "baseline ",
                        " status": "unsolved",
                        " runtime_sec": "     355.139",
                        " input_tokens": "          15",
                        " output_tokens": "        15911",
                        " cache_read_input_tokens": "          0",
                        " cache_creation_input_tokens": "          0",
                        " total_input_processed_tokens": "          15",
                        " total_processed_tokens": "        15926",
                        " estimated_cost     ": "0.92352475         ",
                        " tool_calls": "         14",
                        " shell_commands": "               ",
                        " file_reads": "           ",
                        " search_actions": "              0",
                        " files_edited": "            3",
                        " patch_size": "       4722",
                        " first_file_opened": "                  ",
                        " first_file_edited": "                  ",
                        " first_test_command": "                   ",
                        " bitloops_context_tokens": "                        ",
                        " evaluator_result": '{"source": "summary_ids", "submitted": true, "resolved": false, "unresolved": true, "error": false}',
                    }
                )

            result = import_appendix_csv_to_sqlite(
                db_path=db_path,
                appendix_csv=appendix_csv,
                run_root=run_root,
            )
            self.assertEqual(result.run_id, "run-123")
            self.assertEqual(result.inserted_runs, 1)
            self.assertEqual(result.inserted_task_attempts, 2)

            duplicate = import_appendix_csv_to_sqlite(
                db_path=db_path,
                appendix_csv=appendix_csv,
                run_root=run_root,
            )
            self.assertEqual(duplicate.inserted_runs, 0)
            self.assertEqual(duplicate.inserted_task_attempts, 0)

            connection = sqlite3.connect(db_path)
            try:
                run_row = connection.execute(
                    "SELECT run_id, benchmark, model_canonical, model_resolved, max_workers, attempts, evaluation_enabled FROM runs"
                ).fetchone()
                self.assertEqual(
                    run_row,
                    (
                        "run-123",
                        "swebench_multilingual",
                        "opus-4-6",
                        "eu.anthropic.claude-opus-4-6-v1",
                        3,
                        2,
                        1,
                    ),
                )

                task_count = connection.execute(
                    "SELECT COUNT(*) FROM task_attempts WHERE run_id = ?",
                    ("run-123",),
                ).fetchone()[0]
                self.assertEqual(task_count, 2)

                typed_row = connection.execute(
                    """
                    SELECT attempt, runtime_sec, typeof(runtime_sec), estimated_cost, typeof(estimated_cost),
                           input_tokens, typeof(input_tokens), cache_read_input_tokens,
                           cache_creation_input_tokens, total_input_processed_tokens,
                           total_processed_tokens, shell_commands, file_reads
                    FROM task_attempts
                    WHERE run_id = ? AND task_id = ?
                    """,
                    ("run-123", "astral-sh__ruff-15309"),
                ).fetchone()
                self.assertEqual(typed_row[0], 1)
                self.assertAlmostEqual(typed_row[1], 208.408)
                self.assertEqual(typed_row[2], "real")
                self.assertAlmostEqual(typed_row[3], 0.8117682)
                self.assertEqual(typed_row[4], "real")
                self.assertEqual(typed_row[5], 625)
                self.assertEqual(typed_row[6], "integer")
                self.assertEqual(typed_row[7], 55)
                self.assertEqual(typed_row[8], 22)
                self.assertEqual(typed_row[9], 702)
                self.assertEqual(typed_row[10], 7403)
                self.assertIsNone(typed_row[11])
                self.assertIsNone(typed_row[12])

                task_attempt_columns = {
                    row[1]
                    for row in connection.execute("PRAGMA table_info(task_attempts)").fetchall()
                }
                self.assertNotIn("token_input", task_attempt_columns)
                self.assertNotIn("token_output", task_attempt_columns)

                joined = connection.execute(
                    """
                    SELECT r.run_id, t.task_id
                    FROM runs AS r
                    JOIN task_attempts AS t ON t.run_id = r.run_id
                    ORDER BY t.task_id
                    """
                ).fetchall()
                self.assertEqual(
                    joined,
                    [
                        ("run-123", "astral-sh__ruff-15309"),
                        ("run-123", "astral-sh__ruff-15330"),
                    ],
                )
            finally:
                connection.close()


if __name__ == "__main__":
    unittest.main()
