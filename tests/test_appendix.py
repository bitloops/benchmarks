from __future__ import annotations

from pathlib import Path
import csv
import json
import tempfile
import unittest

from benchkit.common.io import write_json, write_jsonl
from benchkit.swebench.appendix import (
    _extract_bitloops_commands,
    _render_tool_invocation_markdown,
    generate_appendix_files,
)


class AppendixTests(unittest.TestCase):
    def test_extract_bitloops_commands_includes_configure(self) -> None:
        commands = _extract_bitloops_commands(
            {
                "bitloops_status_command": ["bitloops", "status"],
                "bitloops_configure_command": [
                    "bitloops",
                    "configure",
                    "--file",
                    "/tmp/bitloops/config.toml",
                ],
                "bitloops_start_command": ["bitloops", "daemon", "start"],
                "bitloops_init_command": ["bitloops", "init", "--agent", "codex"],
            }
        )

        self.assertEqual(
            commands,
            [
                "bitloops status",
                "bitloops configure --file /tmp/bitloops/config.toml",
                "bitloops daemon start",
                "bitloops init --agent codex",
            ],
        )

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
                    "run_id": "run-1",
                    "benchmark": "swebench_multilingual",
                    "dataset_path": "datasets/sample.jsonl",
                    "split": "dev",
                    "language": "rust",
                    "condition": "baseline",
                    "agent": {"id": "claude_code"},
                    "model": {"resolved_name": "eu.anthropic.claude-opus-4-6-v1"},
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
                    "command": ["claude", "--print", f"Prompt for attempt {index}"],
                    "tool_usage_breakdown": {
                        "web_search_requests": 2,
                        "runTerminalCmdRequests": 1,
                    },
                    "tool_invocation_counts": {
                        "Read": 2,
                        "Bash": 1,
                    },
                    "tool_invocation_sequence": ["Read", "Bash", "Read"],
                    "tool_invocations_raw": [
                        {
                            "call_index": 1,
                            "tool": "Read",
                            "tool_use_id": "toolu_1",
                            "input": {"file_path": "src/main.rs"},
                            "raw_event": {"type": "tool_use", "name": "Read"},
                        },
                        {
                            "call_index": 2,
                            "tool": "Bash",
                            "tool_use_id": "toolu_2",
                            "input": {"command": "pytest -q"},
                            "raw_event": {"type": "tool_use", "name": "Bash"},
                        },
                    ],
                    "tool_invocations_curated": [
                        {
                            "call_index": 1,
                            "tool": "Read",
                            "tool_use_id": "toolu_1",
                            "path": "src/main.rs",
                        },
                        {
                            "call_index": 2,
                            "tool": "Bash",
                            "tool_use_id": "toolu_2",
                            "command": "pytest -q",
                        },
                    ],
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
            self.assertTrue(outputs.prompt_tool_markdown.exists())
            self.assertTrue(outputs.tool_invocation_jsonl.exists())
            self.assertTrue(outputs.tool_invocation_markdown.exists())
            per_task = [
                json.loads(line)
                for line in outputs.per_task_jsonl.read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual(len(per_task), 4)
            self.assertEqual(per_task[0]["status"], "solved")
            self.assertEqual(per_task[0]["estimated_cost"], 0.04)
            self.assertEqual(per_task[0]["cache_creation_input_tokens"], 11)
            self.assertEqual(per_task[0]["cache_read_input_tokens"], 21)
            self.assertEqual(per_task[0]["input_tokens"], 1001)
            self.assertEqual(per_task[0]["output_tokens"], 221)
            self.assertEqual(per_task[0]["total_input_processed_tokens"], 1033)
            self.assertEqual(per_task[0]["total_processed_tokens"], 1254)
            self.assertNotIn("token_input", per_task[0])
            self.assertNotIn("token_output", per_task[0])
            self.assertNotIn("reasoning_output_tokens", per_task[0])
            self.assertNotIn("total_tokens", per_task[0])
            self.assertNotIn("cached_input_tokens", per_task[0])
            self.assertNotIn("cached_output_tokens", per_task[0])
            self.assertNotIn("token_input_uncached", per_task[0])
            self.assertNotIn("token_output_uncached", per_task[0])
            self.assertNotIn("cache_creation_ephemeral_5m_input_tokens", per_task[0])
            self.assertNotIn("cache_creation_ephemeral_1h_input_tokens", per_task[0])
            self.assertEqual(per_task[0]["tool_calls"], 7)
            self.assertEqual(per_task[0]["shell_commands"], 3)
            self.assertEqual(per_task[0]["file_reads"], 8)
            self.assertEqual(per_task[0]["search_actions"], 2)
            self.assertIsNone(per_task[2]["runtime_sec"])
            self.assertIsNone(per_task[2]["estimated_cost"])

            with outputs.per_task_csv.open("r", encoding="utf-8", newline="") as handle:
                per_task_csv_rows = list(csv.DictReader(handle))
            self.assertEqual(len(per_task_csv_rows), 4)
            self.assertNotIn("token_input", per_task_csv_rows[0])
            self.assertNotIn("token_output", per_task_csv_rows[0])
            self.assertNotIn("reasoning_output_tokens", per_task_csv_rows[0])
            self.assertNotIn("total_tokens", per_task_csv_rows[0])

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

            prompt_tool_markdown = outputs.prompt_tool_markdown.read_text(encoding="utf-8")
            self.assertIn("Prompt for attempt 1", prompt_tool_markdown)
            self.assertIn("Read", prompt_tool_markdown)
            self.assertIn("Bash", prompt_tool_markdown)
            self.assertIn("Tool Sequence", prompt_tool_markdown)
            self.assertIn("appendix_tool_invocation_breakdown.md", prompt_tool_markdown)

            tool_invocation_rows = [
                json.loads(line)
                for line in outputs.tool_invocation_jsonl.read_text(encoding="utf-8").splitlines()
            ]
            self.assertGreaterEqual(len(tool_invocation_rows), 2)
            self.assertEqual(tool_invocation_rows[0]["tool"], "Read")
            self.assertIn("curated", tool_invocation_rows[0])
            self.assertIn("raw", tool_invocation_rows[0])

            tool_invocation_markdown = outputs.tool_invocation_markdown.read_text(encoding="utf-8")
            self.assertIn("Tool Invocation Breakdown", tool_invocation_markdown)
            self.assertIn("`path`=src/main.rs", tool_invocation_markdown)
            self.assertIn("`command`=pytest -q", tool_invocation_markdown)

    def test_generate_appendix_files_normalizes_codex_cached_input(self) -> None:
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
                    "model": {"resolved_name": "gpt-5.4"},
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

            outputs = generate_appendix_files(
                run_roots=[run_root],
                output_dir=root / "reports",
            )
            per_task = [
                json.loads(line)
                for line in outputs.per_task_jsonl.read_text(encoding="utf-8").splitlines()
            ]

            self.assertEqual(per_task[0]["input_tokens"], 8568)
            self.assertEqual(per_task[0]["output_tokens"], 27)
            self.assertEqual(per_task[0]["cache_read_input_tokens"], 3456)
            self.assertEqual(per_task[0]["cache_creation_input_tokens"], 0)
            self.assertEqual(per_task[0]["total_input_processed_tokens"], 12024)
            self.assertEqual(per_task[0]["total_processed_tokens"], 12051)
            self.assertNotIn("token_input", per_task[0])
            self.assertNotIn("cached_input_tokens", per_task[0])

    def test_generate_appendix_files_contextbench_results_are_retrieval_first(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            run_root = root / "run-1"
            attempt_dir = run_root / "attempts" / "attempt-01"
            attempt_dir.mkdir(parents=True, exist_ok=True)

            write_json(
                run_root / "run_manifest.json",
                {
                    "run_id": "run-1",
                    "benchmark": "contextbench_verified",
                    "dataset_path": "datasets/contextbench_verified.train.jsonl",
                    "split": "train",
                    "language": "python",
                    "condition": "baseline",
                    "agent": {"id": "codex"},
                    "model": {"resolved_name": "gpt-5.4"},
                },
            )
            write_jsonl(
                run_root / "instances.jsonl",
                [
                    {
                        "instance_id": "owner__repo-1",
                        "repo": "owner/repo",
                        "language": "python",
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
                        "instance_id": "owner__repo-1",
                        "model_name_or_path": "agent:codex",
                        "model_patch": "diff --git a/a.py b/a.py\n",
                    }
                ],
            )
            write_jsonl(
                attempt_dir / "trace.jsonl",
                [
                    {
                        "instance_id": "owner__repo-1",
                        "status": "ok",
                        "metadata": {
                            "elapsed_ms": 1200,
                            "estimated_cost": 0.02,
                            "tool_calls": 5,
                            "file_reads": 4,
                            "search_actions": 2,
                        },
                    }
                ],
            )
            write_jsonl(
                attempt_dir / "evaluation.tasks.jsonl",
                [
                    {
                        "instance_id": "owner__repo-1",
                        "status": "solved",
                        "final_file_coverage": 0.5,
                        "final_file_precision": 0.4,
                        "final_symbol_coverage": 0.6,
                        "final_symbol_precision": 0.7,
                        "final_span_coverage": 0.8,
                        "final_span_precision": 0.9,
                        "final_line_coverage": 0.3,
                        "final_line_precision": 0.2,
                        "traj_auc_file": 0.61,
                        "traj_auc_symbol": 0.62,
                        "traj_auc_span": 0.63,
                        "traj_auc_line": 0.64,
                        "traj_redundancy_file": 0.11,
                        "traj_redundancy_symbol": 0.12,
                        "traj_redundancy_span": 0.13,
                        "traj_redundancy_line": 0.14,
                        "editloc_recall": 0.77,
                        "editloc_precision": 0.88,
                        "raw": {"instance_id": "owner__repo-1"},
                    }
                ],
            )

            outputs = generate_appendix_files(
                run_roots=[run_root],
                output_dir=root / "reports",
            )

            with outputs.results_csv.open("r", encoding="utf-8", newline="") as handle:
                reader = csv.DictReader(handle)
                header = reader.fieldnames or []
                row = next(reader)

            self.assertEqual(
                header[:6],
                [
                    "agent",
                    "condition",
                    "benchmark",
                    "language",
                    "tasks",
                    "final_file_coverage",
                ],
            )
            self.assertEqual(float(row["final_file_coverage"]), 0.5)
            self.assertEqual(float(row["traj_auc_span"]), 0.63)
            self.assertEqual(float(row["editloc_precision"]), 0.88)
            self.assertEqual(float(row["median_runtime_sec"]), 1.2)
            self.assertEqual(float(row["median_tool_calls"]), 5.0)

            results_markdown = outputs.results_markdown.read_text(encoding="utf-8")
            self.assertIn("Final File Cov", results_markdown)
            self.assertIn("Traj AUC Span", results_markdown)
            self.assertIn("EditLoc Precision", results_markdown)

            per_attempt_markdown = outputs.per_attempt_markdown.read_text(encoding="utf-8")
            self.assertIn("Retrieval Metrics (Primary)", per_attempt_markdown)
            self.assertIn("Secondary Diagnostics", per_attempt_markdown)
            self.assertIn("Final Coverage/Precision (span)", per_attempt_markdown)

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

    def test_openai_estimated_cost_fallback_for_supported_model(self) -> None:
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
                    "agent": {"id": "codex"},
                    "model": {"resolved_name": "gpt-5.4"},
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
                        "metadata": {"token_input": 10, "token_output": 3},
                    }
                ],
            )
            write_jsonl(attempt_dir / "evaluation.tasks.jsonl", [])

            outputs = generate_appendix_files(
                run_roots=[run_root],
                output_dir=root / "reports",
            )

            per_task = [
                json.loads(line)
                for line in outputs.per_task_jsonl.read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual(per_task[0]["cache_creation_input_tokens"], 0)
            self.assertEqual(per_task[0]["cache_read_input_tokens"], 0)
            self.assertEqual(per_task[0]["input_tokens"], 10)
            self.assertEqual(per_task[0]["output_tokens"], 3)
            self.assertEqual(per_task[0]["total_input_processed_tokens"], 10)
            self.assertEqual(per_task[0]["total_processed_tokens"], 13)
            self.assertAlmostEqual(per_task[0]["estimated_cost"], 0.00007)
            self.assertNotIn("reasoning_output_tokens", per_task[0])
            self.assertNotIn("total_tokens", per_task[0])
            self.assertNotIn("token_input_uncached", per_task[0])
            self.assertNotIn("token_output_uncached", per_task[0])

    def test_cached_token_columns_and_uncached_derivation(self) -> None:
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
                    "agent": {"id": "codex"},
                    "model": {"resolved_name": "gpt-5.4"},
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
                        "metadata": {
                            "token_input": 10,
                            "token_output": 3,
                            "cached_input_tokens": 4,
                            "cached_output_tokens": 1,
                            "cache_creation_input_tokens": 2,
                            "cache_read_input_tokens": 1,
                            "cache_creation_ephemeral_5m_input_tokens": 1,
                            "cache_creation_ephemeral_1h_input_tokens": 1,
                        },
                    }
                ],
            )
            write_jsonl(attempt_dir / "evaluation.tasks.jsonl", [])

            outputs = generate_appendix_files(
                run_roots=[run_root],
                output_dir=root / "reports",
            )

            per_task = [
                json.loads(line)
                for line in outputs.per_task_jsonl.read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual(per_task[0]["input_tokens"], 6)
            self.assertEqual(per_task[0]["output_tokens"], 3)
            self.assertEqual(per_task[0]["cache_creation_input_tokens"], 2)
            self.assertEqual(per_task[0]["cache_read_input_tokens"], 1)
            self.assertEqual(per_task[0]["total_input_processed_tokens"], 9)
            self.assertEqual(per_task[0]["total_processed_tokens"], 12)
            self.assertAlmostEqual(per_task[0]["estimated_cost"], 0.000061)
            self.assertNotIn("cached_input_tokens", per_task[0])
            self.assertNotIn("cached_output_tokens", per_task[0])
            self.assertNotIn("total_tokens", per_task[0])
            self.assertNotIn("token_input_uncached", per_task[0])
            self.assertNotIn("token_output_uncached", per_task[0])

    def test_estimated_cost_remains_blank_for_unknown_model_without_explicit_cost(self) -> None:
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
                        "metadata": {
                            "token_input": 10,
                            "token_output": 3,
                            "cache_creation_input_tokens": 2,
                            "cache_read_input_tokens": 1,
                        },
                    }
                ],
            )
            write_jsonl(attempt_dir / "evaluation.tasks.jsonl", [])

            outputs = generate_appendix_files(
                run_roots=[run_root],
                output_dir=root / "reports",
            )

            per_task = [
                json.loads(line)
                for line in outputs.per_task_jsonl.read_text(encoding="utf-8").splitlines()
            ]
            self.assertIsNone(per_task[0]["estimated_cost"])

    def test_tool_invocation_markdown_separates_same_task_by_agent_and_model(self) -> None:
        markdown = _render_tool_invocation_markdown(
            [
                {
                    "condition": "baseline",
                    "task_id": "tokio__1",
                    "attempt": 1,
                    "agent": "claude_code",
                    "model_version": "eu.anthropic.claude-opus-4-6-v1",
                    "call_index": 1,
                    "tool": "Read",
                    "curated": {"tool": "Read", "path": "src/a.rs"},
                },
                {
                    "condition": "baseline",
                    "task_id": "tokio__1",
                    "attempt": 1,
                    "agent": "cursor",
                    "model_version": "sonnet-4",
                    "call_index": 1,
                    "tool": "Read",
                    "curated": {"tool": "Read", "path": "src/b.rs"},
                },
            ]
        )

        self.assertEqual(markdown.count("### Task `tokio__1` (attempt 1)"), 2)
        self.assertIn(
            "### Agent `claude_code` | Model `eu.anthropic.claude-opus-4-6-v1`",
            markdown,
        )
        self.assertIn("### Agent `cursor` | Model `sonnet-4`", markdown)
        self.assertIn("`path`=src/a.rs", markdown)
        self.assertIn("`path`=src/b.rs", markdown)


if __name__ == "__main__":
    unittest.main()
