from __future__ import annotations

from pathlib import Path
import contextlib
import importlib
import json
import os
import re
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch


def _load_common_module():
    return importlib.import_module("benchkit.swebench.agents.common")


common = _load_common_module()
common_impl = importlib.import_module("benchkit.swebench.agents.common")


class AgentWrapperCommonTests(unittest.TestCase):
    def test_render_task_prompt_instructs_devql_for_bitloops_condition(self) -> None:
        prompt = common.render_task_prompt(
            {
                "instance_id": "ruff__1",
                "repo": "astral-sh/ruff",
                "base_commit": "abc123",
                "language": "rust",
                "problem_statement": "Fix the failing lint behavior.",
                "metadata": {},
                "run": {"condition": "with_bitloops"},
            },
            wrapper_name="claude_code",
        )

        self.assertTrue(prompt.startswith("Investigate and fix the following issue by editing files directly in the workspace."))
        self.assertIn("Do not commit your changes; just leave the edited files in place.", prompt)
        self.assertIn("bitloops devql query", prompt)
        self.assertIn("Use the returned paths and symbols", prompt)
        self.assertTrue(prompt.endswith("Fix the failing lint behavior."))
        self.assertNotIn("Issue:\n", prompt)

    def test_render_task_prompt_is_minimal_for_baseline_condition(self) -> None:
        prompt = common.render_task_prompt(
            {
                "instance_id": "ruff__1",
                "repo": "astral-sh/ruff",
                "base_commit": "abc123",
                "language": "rust",
                "problem_statement": "Fix the failing lint behavior.",
                "metadata": {},
                "run": {"condition": "baseline"},
            },
            wrapper_name="claude_code",
        )

        self.assertTrue(prompt.startswith("Investigate and fix the following issue by editing files directly in the workspace."))
        self.assertIn("Do not commit your changes; just leave the edited files in place.", prompt)
        self.assertTrue(prompt.endswith("Fix the failing lint behavior."))
        self.assertNotIn("bitloops devql", prompt)
        self.assertNotIn("Issue:\n", prompt)

    def test_parse_agent_output_prefers_json_result(self) -> None:
        raw = '{"type":"result","result":"diff --git a/x b/x\\n--- a/x\\n+++ b/x\\n@@ -1 +1 @@\\n-a\\n+b\\n"}'
        text = common.parse_agent_output(raw)
        self.assertIn("diff --git", text)

    def test_parse_agent_output_prefers_terminal_codex_agent_message(self) -> None:
        payload = [
            {
                "type": "item.completed",
                "item": {"type": "agent_message", "text": "Initial status"},
            },
            {
                "type": "item.completed",
                "item": {"type": "agent_message", "text": "Final answer with patch summary"},
            },
        ]
        text = common.parse_agent_output("", parsed_payload=payload)
        self.assertEqual(text, "")

        raw = "\n".join(json.dumps(item) for item in payload)
        parsed_text = common.parse_agent_output(raw)
        self.assertEqual(parsed_text, "Final answer with patch summary")

    def test_parse_agent_output_prefers_final_opencode_assistant_message(self) -> None:
        payload = [
            {
                "type": "message.updated",
                "properties": {
                    "info": {
                        "info": {"role": "assistant"},
                        "parts": [
                            {"id": "part_1", "type": "text", "text": "Initial status"},
                        ],
                    }
                },
            },
            {
                "type": "message.updated",
                "properties": {
                    "info": {
                        "info": {"role": "assistant"},
                        "parts": [
                            {
                                "id": "part_2",
                                "type": "text",
                                "text": "Final answer with patch summary",
                            },
                        ],
                    }
                },
            },
        ]

        raw = "\n".join(json.dumps(item) for item in payload)
        parsed_text = common.parse_agent_output(raw)
        self.assertEqual(parsed_text, "Final answer with patch summary")

    def test_parse_agent_output_prefers_final_opencode_text_event(self) -> None:
        payload = [
            {
                "type": "step_start",
                "part": {"type": "step-start"},
            },
            {
                "type": "text",
                "part": {
                    "type": "text",
                    "text": "<patch>\n--- a/x\n+++ b/x\n@@ -1 +1 @@\n-old\n+new\n</patch>",
                },
            },
            {
                "type": "step_finish",
                "part": {"type": "step-finish"},
            },
        ]

        raw = "\n".join(json.dumps(item) for item in payload)
        parsed_text = common.parse_agent_output(raw)
        self.assertIn("--- a/x", parsed_text)
        self.assertNotEqual(parsed_text, "step_start")

    def test_extract_git_patch_from_markdown_fence(self) -> None:
        raw = """Here is the fix:\n```diff\ndiff --git a/a b/a\n--- a/a\n+++ b/a\n@@ -1 +1 @@\n-old\n+new\n```\n"""
        patch, source = common.extract_git_patch(raw)
        self.assertEqual(source, "diff_header")
        self.assertTrue(patch.startswith("diff --git"))

    def test_extract_git_patch_returns_empty_when_missing(self) -> None:
        patch, source = common.extract_git_patch("No code changes required.")
        self.assertEqual(patch, "")
        self.assertEqual(source, "no_patch_found")

    def test_extract_git_patch_strips_patch_tags_and_repairs_hunk_context(self) -> None:
        raw = (
            "<patch>\n"
            "--- a/x\n"
            "+++ b/x\n"
            "@@ -1,3 +1,3 @@\n"
            " fn demo() {\n"
            "-    old();\n"
            "+    new();\n"
            " }\n"
            "</patch>\n"
        )
        patch, source = common.extract_git_patch(raw)
        self.assertEqual(source, "unified_header")
        self.assertNotIn("<patch>", patch)
        self.assertNotIn("</patch>", patch)
        self.assertIn("\n fn demo() {\n", patch)
        self.assertIn("\n }\n", patch)

    def test_extract_git_patch_repairs_hunk_header_counts(self) -> None:
        raw = (
            "<patch>\n"
            "--- a/axum/src/routing/mod.rs\n"
            "+++ b/axum/src/routing/mod.rs\n"
            "@@ -123,7 +122,6 @@ impl<B> Service<Request<B>> for Router<B>\n"
            "                     }\n"
            "                 },\n"
            "             } else {\n"
            "-            fallback.call(req)\n"
            "+                fallback.call(req)\n"
            "             }\n"
            "         }\n"
            "     }\n"
            "</patch>\n"
        )
        patch, source = common.extract_git_patch(raw)
        self.assertEqual(source, "unified_header")
        self.assertIn("@@ -123,7 +122,7 @@", patch)

    def test_call_command_tolerates_invalid_utf8_output(self) -> None:
        stdout, stderr, return_code, elapsed_ms = common.call_command(
            [
                common.sys.executable,
                "-c",
                (
                    "import sys; "
                    "sys.stdout.buffer.write(b'\\xe2'); "
                    "sys.stdout.flush(); "
                    "sys.stderr.buffer.write(b'\\xff'); "
                    "sys.stderr.flush()"
                ),
            ],
            5,
        )

        self.assertEqual(return_code, 0)
        self.assertEqual(stdout, "\ufffd")
        self.assertEqual(stderr, "\ufffd")
        self.assertGreaterEqual(elapsed_ms, 0)

    def test_call_command_rewrites_pwd_when_cwd_is_set(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            resolved_workspace = str(Path(workspace).resolve())
            stdout, stderr, return_code, _ = common.call_command(
                [
                    common.sys.executable,
                    "-c",
                    (
                        "import os; "
                        "print(os.getcwd()); "
                        "print(os.environ.get('PWD', ''))"
                    ),
                ],
                5,
                env={**os.environ, "PWD": "/stale/pwd"},
                cwd=workspace,
            )

        self.assertEqual(return_code, 0)
        self.assertEqual(stderr, "")
        self.assertEqual(stdout.splitlines(), [resolved_workspace, resolved_workspace])

    def test_call_command_starts_new_session_and_kills_process_group_on_timeout(self) -> None:
        class FakeProcess:
            def __init__(self) -> None:
                self.pid = 4242
                self.returncode = None
                self.communicate_calls = 0
                self.wait_calls: list[float | None] = []

            def communicate(self, timeout=None):  # type: ignore[no-untyped-def]
                self.communicate_calls += 1
                if self.communicate_calls == 1:
                    raise common_impl.subprocess.TimeoutExpired(
                        ["cmd"],
                        timeout,
                        output="partial stdout",
                        stderr="partial stderr",
                    )
                return ("partial stdout", "partial stderr")

            def wait(self, timeout=None):  # type: ignore[no-untyped-def]
                self.wait_calls.append(timeout)
                return 0

        process = FakeProcess()
        with patch.object(common_impl.subprocess, "Popen", return_value=process) as mock_popen:
            with patch.object(common_impl.os, "killpg") as mock_killpg:
                with self.assertRaises(common_impl.subprocess.TimeoutExpired) as raised:
                    common_impl.call_command(["cmd"], 7)

        self.assertEqual(raised.exception.output, "partial stdout")
        self.assertEqual(raised.exception.stderr, "partial stderr")
        self.assertEqual(mock_popen.call_args.kwargs.get("start_new_session"), True)
        mock_killpg.assert_called_once_with(process.pid, common_impl.signal.SIGTERM)
        self.assertEqual(process.wait_calls, [2])

    def test_extract_usage_metrics_from_claude_style_json(self) -> None:
        payload = {
            "type": "result",
            "usage": {
                "input_tokens": 1234,
                "output_tokens": 321,
                "cache_creation_input_tokens": 777,
                "cache_read_input_tokens": 55,
                "cache_creation": {
                    "ephemeral_5m_input_tokens": 444,
                    "ephemeral_1h_input_tokens": 333,
                },
                "server_tool_use": {
                    "web_search_requests": 2,
                    "web_fetch_requests": 3,
                },
            },
            "total_cost_usd": 0.42,
        }

        metrics = common.extract_usage_metrics(payload)

        self.assertEqual(metrics.get("token_input"), 1234)
        self.assertEqual(metrics.get("token_output"), 321)
        self.assertEqual(metrics.get("estimated_cost"), 0.42)
        self.assertEqual(metrics.get("cache_creation_input_tokens"), 777)
        self.assertEqual(metrics.get("cache_read_input_tokens"), 55)
        self.assertEqual(metrics.get("cache_creation_ephemeral_5m_input_tokens"), 444)
        self.assertEqual(metrics.get("cache_creation_ephemeral_1h_input_tokens"), 333)
        self.assertEqual(metrics.get("search_actions"), 2)
        self.assertEqual(metrics.get("tool_calls"), 5)
        self.assertEqual(metrics.get("total_tokens"), 1555)
        self.assertEqual(metrics.get("input_tokens"), 1234)
        self.assertEqual(metrics.get("output_tokens"), 321)
        self.assertEqual(metrics.get("total_input_processed_tokens"), 2066)
        self.assertEqual(metrics.get("total_processed_tokens"), 2387)

    def test_extract_usage_metrics_from_cursor_style_json(self) -> None:
        payload = [
            {"type": "assistant", "message": "working"},
            {
                "type": "result",
                "usage": {
                    "inputTokens": 2222,
                    "outputTokens": 444,
                    "toolCalls": 7,
                    "shellCommands": 5,
                    "fileReads": 3,
                },
                "totalCostUsd": "0.12",
            },
        ]

        metrics = common.extract_usage_metrics(payload)

        self.assertEqual(metrics.get("token_input"), 2222)
        self.assertEqual(metrics.get("token_output"), 444)
        self.assertEqual(metrics.get("estimated_cost"), 0.12)
        self.assertEqual(metrics.get("tool_calls"), 7)
        self.assertEqual(metrics.get("shell_commands"), 5)
        self.assertEqual(metrics.get("file_reads"), 3)
        self.assertEqual(metrics.get("input_tokens"), 2222)
        self.assertEqual(metrics.get("output_tokens"), 444)
        self.assertEqual(metrics.get("cache_creation_input_tokens"), 0)
        self.assertEqual(metrics.get("cache_read_input_tokens"), 0)
        self.assertEqual(metrics.get("total_input_processed_tokens"), 2222)
        self.assertEqual(metrics.get("total_processed_tokens"), 2666)

    def test_extract_usage_metrics_prefers_terminal_result_usage(self) -> None:
        payload = [
            {
                "type": "assistant",
                "usage": {
                    "input_tokens": 3,
                    "output_tokens": 11,
                    "cache_creation_input_tokens": 7711,
                    "cache_read_input_tokens": 8413,
                    "cache_creation": {
                        "ephemeral_5m_input_tokens": 7711,
                        "ephemeral_1h_input_tokens": 0,
                    },
                },
            },
            {
                "type": "result",
                "subtype": "error",
                "is_error": True,
                "usage": {
                    "input_tokens": 99,
                    "output_tokens": 99,
                },
            },
            {
                "type": "result",
                "subtype": "success",
                "usage": {
                    "input_tokens": 5397,
                    "output_tokens": 9016,
                    "cache_creation_input_tokens": 41001,
                    "cache_read_input_tokens": 1027283,
                    "cache_creation": {
                        "ephemeral_5m_input_tokens": 41001,
                        "ephemeral_1h_input_tokens": 0,
                    },
                },
                "total_cost_usd": 1.02228275,
            },
        ]

        metrics = common.extract_usage_metrics(payload)

        self.assertEqual(metrics.get("token_input"), 5397)
        self.assertEqual(metrics.get("token_output"), 9016)
        self.assertEqual(metrics.get("cache_creation_input_tokens"), 41001)
        self.assertEqual(metrics.get("cache_read_input_tokens"), 1027283)
        self.assertEqual(metrics.get("cache_creation_ephemeral_5m_input_tokens"), 41001)
        self.assertEqual(metrics.get("cache_creation_ephemeral_1h_input_tokens"), 0)
        self.assertEqual(metrics.get("estimated_cost"), 1.02228275)
        self.assertEqual(metrics.get("token_metrics_source"), "result_usage")

    def test_extract_usage_metrics_uses_result_model_usage_fallback(self) -> None:
        payload = [
            {"type": "assistant", "usage": {"input_tokens": 3, "output_tokens": 11}},
            {
                "type": "result",
                "usage": {},
                "modelUsage": {
                    "main": {"inputTokens": 5000, "outputTokens": 8000, "costUSD": 0.9},
                    "mini": {"inputTokens": 397, "outputTokens": 1016, "costUSD": 0.12228275},
                },
            },
        ]

        metrics = common.extract_usage_metrics(payload)

        self.assertEqual(metrics.get("token_input"), 5397)
        self.assertEqual(metrics.get("token_output"), 9016)
        self.assertAlmostEqual(float(metrics.get("estimated_cost", 0)), 1.02228275, places=9)
        self.assertEqual(metrics.get("token_metrics_source"), "result_model_usage")

    def test_extract_usage_metrics_falls_back_to_scan_when_result_usage_missing(self) -> None:
        payload = [
            {"type": "assistant", "usage": {"input_tokens": 123, "output_tokens": 45}},
            {"type": "assistant", "usage": {"input_tokens": 9999, "output_tokens": 8888}},
        ]

        metrics = common.extract_usage_metrics(payload)

        self.assertEqual(metrics.get("token_input"), 123)
        self.assertEqual(metrics.get("token_output"), 45)
        self.assertEqual(metrics.get("token_metrics_source"), "fallback_scan")

    def test_extract_usage_metrics_cross_format_parity_for_terminal_usage(self) -> None:
        single_payload = {
            "type": "result",
            "usage": {
                "input_tokens": 5397,
                "output_tokens": 9016,
                "cache_creation_input_tokens": 41001,
                "cache_read_input_tokens": 1027283,
                "cache_creation": {
                    "ephemeral_5m_input_tokens": 41001,
                    "ephemeral_1h_input_tokens": 0,
                },
            },
            "total_cost_usd": 1.02228275,
        }
        stream_payload = [
            {"type": "assistant", "usage": {"input_tokens": 3, "output_tokens": 11}},
            single_payload,
        ]

        single_metrics = common.extract_usage_metrics(single_payload)
        stream_metrics = common.extract_usage_metrics(stream_payload)

        for key in (
            "token_input",
            "token_output",
            "estimated_cost",
            "cache_creation_input_tokens",
            "cache_read_input_tokens",
            "cache_creation_ephemeral_5m_input_tokens",
            "cache_creation_ephemeral_1h_input_tokens",
        ):
            self.assertEqual(
                single_metrics.get(key),
                stream_metrics.get(key),
                msg=f"mismatch for {key}",
            )
        self.assertEqual(single_metrics.get("token_metrics_source"), "result_usage")
        self.assertEqual(stream_metrics.get("token_metrics_source"), "result_usage")

    def test_extract_usage_metrics_from_cursor_server_tool_use_block(self) -> None:
        payload = {
            "type": "result",
            "usage": {
                "inputTokens": 100,
                "outputTokens": 10,
                "serverToolUse": {
                    "runTerminalCmdRequests": 4,
                    "readFileRequests": 7,
                    "webSearchRequests": 3,
                    "webFetchRequests": 2,
                },
            },
        }

        metrics = common.extract_usage_metrics(payload)

        self.assertEqual(metrics.get("token_input"), 100)
        self.assertEqual(metrics.get("token_output"), 10)
        self.assertEqual(metrics.get("tool_calls"), 16)
        self.assertEqual(metrics.get("shell_commands"), 4)
        self.assertEqual(metrics.get("file_reads"), 7)
        self.assertEqual(metrics.get("search_actions"), 3)

    def test_extract_usage_metrics_from_codex_turn_completed_event(self) -> None:
        payload = [
            {"type": "thread.started", "thread_id": "abc"},
            {"type": "turn.started"},
            {
                "type": "turn.completed",
                "usage": {
                    "input_tokens": 12024,
                    "cached_input_tokens": 3456,
                    "output_tokens": 27,
                    "reasoning_output_tokens": 11,
                    "total_tokens": 12051,
                },
            },
        ]

        metrics = common.extract_usage_metrics(payload)

        self.assertEqual(metrics.get("token_input"), 12024)
        self.assertEqual(metrics.get("token_output"), 27)
        self.assertEqual(metrics.get("cached_input_tokens"), 3456)
        self.assertEqual(metrics.get("token_input_uncached"), 8568)
        self.assertEqual(metrics.get("reasoning_output_tokens"), 11)
        self.assertEqual(metrics.get("total_tokens"), 12051)
        self.assertEqual(metrics.get("token_metrics_source"), "result_usage")
        self.assertEqual(metrics.get("input_tokens"), 8568)
        self.assertEqual(metrics.get("output_tokens"), 27)
        self.assertEqual(metrics.get("cache_read_input_tokens"), 3456)
        self.assertEqual(metrics.get("cache_creation_input_tokens"), 0)
        self.assertEqual(metrics.get("total_input_processed_tokens"), 12024)
        self.assertEqual(metrics.get("total_processed_tokens"), 12051)

    def test_extract_usage_metrics_from_opencode_assistant_message(self) -> None:
        payload = [
            {
                "type": "message.updated",
                "properties": {
                    "info": {
                        "info": {
                            "role": "assistant",
                            "cost": 0.12,
                            "tokens": {
                                "input": 2222,
                                "output": 444,
                                "reasoning": 51,
                                "cache": {"read": 111, "write": 22},
                            },
                        },
                        "parts": [],
                    }
                },
            },
        ]

        metrics = common.extract_usage_metrics(payload)

        self.assertEqual(metrics.get("token_input"), 2222)
        self.assertEqual(metrics.get("token_output"), 444)
        self.assertEqual(metrics.get("estimated_cost"), 0.12)

    def test_extract_usage_metrics_aggregates_opencode_step_finish_tokens_before_fallback_scan(
        self,
    ) -> None:
        payload = [
            {
                "type": "step_finish",
                "part": {
                    "cost": 0.006207,
                    "tokens": {
                        "total": 10889,
                        "input": 10584,
                        "output": 305,
                        "reasoning": 0,
                        "cache": {"write": 0, "read": 0},
                    },
                },
            },
            {
                "type": "message.updated",
                "part": {
                    "cost": 999,
                    "tokens": {
                        "input": 999999,
                        "output": 999999,
                        "cache": {"write": 999999, "read": 999999},
                    },
                },
            },
            {
                "type": "step_finish",
                "part": {
                    "cost": 0.5,
                    "tokens": {
                        "input": 100,
                        "output": 7,
                        "reasoning": 3,
                        "cache": {"write": 4, "read": 200},
                    },
                },
            },
        ]

        metrics = common.extract_usage_metrics(payload)

        self.assertEqual(metrics.get("token_input"), 10684)
        self.assertEqual(metrics.get("token_output"), 312)
        self.assertEqual(metrics.get("reasoning_output_tokens"), 3)
        self.assertEqual(metrics.get("cache_creation_input_tokens"), 4)
        self.assertEqual(metrics.get("cache_read_input_tokens"), 200)
        self.assertEqual(metrics.get("total_tokens"), 11203)
        self.assertAlmostEqual(float(metrics.get("estimated_cost", 0)), 0.506207)
        self.assertEqual(metrics.get("token_metrics_source"), "opencode_step_finish_sum")
        self.assertEqual(metrics.get("input_tokens"), 10684)
        self.assertEqual(metrics.get("output_tokens"), 312)
        self.assertEqual(metrics.get("total_input_processed_tokens"), 10888)
        self.assertEqual(metrics.get("total_processed_tokens"), 11200)

    def test_extract_usage_metrics_emits_zero_cache_counts_for_opencode_step_finish(
        self,
    ) -> None:
        payload = [
            {
                "type": "step_finish",
                "part": {
                    "cost": 0.1,
                    "tokens": {
                        "total": 12,
                        "input": 10,
                        "output": 2,
                        "reasoning": 0,
                        "cache": {},
                    },
                },
            },
            {
                "type": "step_finish",
                "part": {
                    "cost": 0.2,
                    "tokens": {
                        "total": 13,
                        "input": 11,
                        "output": 2,
                    },
                },
            },
            {
                "type": "step.finish",
                "part": {
                    "cost": 999,
                    "tokens": {
                        "total": 999,
                        "input": 999,
                        "output": 999,
                        "cache": {"write": 999, "read": 999},
                    },
                },
            },
            {
                "type": "step_finish",
                "part": {
                    "cost": 999,
                    "tokens": "not-a-dict",
                },
            },
        ]

        metrics = common.extract_usage_metrics(payload)

        self.assertEqual(metrics.get("token_input"), 21)
        self.assertEqual(metrics.get("token_output"), 4)
        self.assertEqual(metrics.get("reasoning_output_tokens"), 0)
        self.assertEqual(metrics.get("cache_creation_input_tokens"), 0)
        self.assertEqual(metrics.get("cache_read_input_tokens"), 0)
        self.assertEqual(metrics.get("total_tokens"), 25)
        self.assertAlmostEqual(float(metrics.get("estimated_cost", 0)), 0.3)
        self.assertEqual(metrics.get("token_metrics_source"), "opencode_step_finish_sum")

    def test_extract_codex_command_execution_as_tool_invocation(self) -> None:
        payload = [
            {
                "type": "item.started",
                "item": {
                    "id": "item_1",
                    "type": "command_execution",
                    "command": "/bin/zsh -lc 'ls -1'",
                    "status": "in_progress",
                    "exit_code": None,
                },
            },
            {
                "type": "item.completed",
                "item": {
                    "id": "item_1",
                    "type": "command_execution",
                    "command": "/bin/zsh -lc 'ls -1'",
                    "status": "completed",
                    "exit_code": 0,
                },
            },
        ]

        raw = common.extract_tool_invocations_raw(payload)
        curated = common.extract_tool_invocations_curated(raw)
        metrics = common.extract_usage_metrics(payload)

        self.assertEqual(len(raw), 1)
        self.assertEqual(raw[0]["tool"], "Bash")
        self.assertEqual(raw[0]["tool_use_id"], "item_1")
        self.assertEqual(curated[0]["tool"], "Bash")
        self.assertEqual(curated[0]["command"], "/bin/zsh -lc 'ls -1'")
        self.assertEqual(metrics.get("tool_calls"), 1)
        self.assertEqual(metrics.get("shell_commands"), 1)

    def test_extract_opencode_tool_parts_as_tool_invocations(self) -> None:
        payload = [
            {
                "type": "message.updated",
                "properties": {
                    "info": {
                        "info": {"role": "assistant"},
                        "parts": [
                            {
                                "id": "part_bash",
                                "type": "tool",
                                "tool": "bash",
                                "callID": "call_1",
                                "state": {
                                    "status": "completed",
                                    "input": {"command": "ls -1"},
                                },
                            },
                            {
                                "id": "part_read",
                                "type": "tool",
                                "tool": "read",
                                "callID": "call_2",
                                "state": {
                                    "status": "completed",
                                    "input": {
                                        "file": "src/lib.rs",
                                        "offset": 10,
                                        "limit": 20,
                                    },
                                },
                            },
                            {
                                "id": "part_edit",
                                "type": "tool",
                                "tool": "edit",
                                "callID": "call_3",
                                "state": {
                                    "status": "completed",
                                    "input": {
                                        "file": "src/lib.rs",
                                        "oldString": "a",
                                        "newString": "abc",
                                    },
                                },
                            },
                        ],
                    }
                },
            }
        ]

        raw = common.extract_tool_invocations_raw(payload)
        curated = common.extract_tool_invocations_curated(raw)
        metrics = common.extract_usage_metrics(payload)

        self.assertEqual([row["tool"] for row in raw], ["Bash", "Read", "Edit"])
        self.assertEqual([row["tool_use_id"] for row in raw], ["call_1", "call_2", "call_3"])
        self.assertEqual(curated[0]["command"], "ls -1")
        self.assertEqual(curated[1]["path"], "src/lib.rs")
        self.assertEqual(curated[2]["old_chars"], 1)
        self.assertEqual(curated[2]["new_chars"], 3)
        self.assertEqual(metrics.get("tool_calls"), 3)
        self.assertEqual(metrics.get("shell_commands"), 1)
        self.assertEqual(metrics.get("file_reads"), 1)

    def test_extract_tool_usage_breakdown_from_usage_blocks(self) -> None:
        payload = {
            "usage": {
                "serverToolUse": {
                    "runTerminalCmdRequests": 4,
                    "readFileRequests": 7,
                },
                "tool_usage": {
                    "web_search_requests": 3,
                },
            }
        }

        breakdown = common.extract_tool_usage_breakdown(payload)

        self.assertEqual(
            breakdown,
            {
                "readFileRequests": 7,
                "runTerminalCmdRequests": 4,
                "web_search_requests": 3,
            },
        )

    def test_extract_tool_invocation_sequence_from_stream_events(self) -> None:
        payload = [
            {"type": "assistant", "message": {"content": [{"type": "text", "text": "x"}]}},
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {"type": "tool_use", "id": "toolu_1", "name": "Read", "input": {"file": "a.py"}},
                        {"type": "tool_use", "id": "toolu_2", "name": "Bash", "input": {"cmd": "pytest"}},
                    ]
                },
            },
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {"type": "tool_use", "id": "toolu_3", "name": "Read", "input": {"file": "b.py"}},
                    ]
                },
            },
        ]

        sequence = common.extract_tool_invocation_sequence(payload)
        counts = common.summarize_tool_invocation_counts(sequence)

        self.assertEqual(sequence, ["Read", "Bash", "Read"])
        self.assertEqual(counts, {"Bash": 1, "Read": 2})

    def test_extract_tool_invocations_raw_and_curated(self) -> None:
        payload = [
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "toolu_grep",
                            "name": "Grep",
                            "input": {"pattern": "foo", "path": "src", "line_numbers": True},
                        },
                        {
                            "type": "tool_use",
                            "id": "toolu_read",
                            "name": "Read",
                            "input": {"file_path": "src/lib.rs", "offset": 10, "limit": 30},
                        },
                        {
                            "type": "tool_use",
                            "id": "toolu_bash",
                            "name": "Bash",
                            "input": {"command": "pytest -q", "cwd": "/repo"},
                        },
                        {
                            "type": "tool_use",
                            "id": "toolu_edit",
                            "name": "Edit",
                            "input": {
                                "file_path": "src/lib.rs",
                                "old_string": "a",
                                "new_string": "abc",
                                "replace_all": False,
                            },
                        },
                        {
                            "type": "tool_use",
                            "id": "toolu_unknown",
                            "name": "CustomTool",
                            "input": {"alpha": 1},
                        },
                    ]
                },
            }
        ]

        raw = common.extract_tool_invocations_raw(payload)
        curated = common.extract_tool_invocations_curated(raw)

        self.assertEqual([row["tool"] for row in raw], ["Grep", "Read", "Bash", "Edit", "CustomTool"])
        self.assertEqual([row["call_index"] for row in raw], [1, 2, 3, 4, 5])
        self.assertEqual(curated[0]["query"], "foo")
        self.assertEqual(curated[0]["path"], "src")
        self.assertEqual(curated[1]["path"], "src/lib.rs")
        self.assertEqual(curated[2]["command"], "pytest -q")
        self.assertEqual(curated[3]["old_chars"], 1)
        self.assertEqual(curated[3]["new_chars"], 3)
        self.assertIn("raw_input_json", curated[4])

    def test_extract_tool_invocations_ignores_tool_results_and_empty_deltas(self) -> None:
        payload = [
            {"type": "tool_result", "name": "Read", "content": "ignored"},
            {"type": "tool_use_delta", "name": "Read"},
            {"type": "tool_use_delta", "name": "Read", "input": {"file_path": "a.py"}},
            {"type": "tool_use", "name": "Grep", "input": {"pattern": "x"}},
        ]

        raw = common.extract_tool_invocations_raw(payload)

        self.assertEqual(len(raw), 2)
        self.assertEqual(raw[0]["tool"], "Read")
        self.assertEqual(raw[1]["tool"], "Grep")
        self.assertEqual(raw[0]["call_index"], 1)
        self.assertEqual(raw[1]["call_index"], 2)

    def test_extract_tool_invocations_includes_failed_command_execution(self) -> None:
        payload = [
            {"type": "command_execution", "id": "cmd_start", "command": "bitloops devql query '{}'", "status": "running"},
            {
                "type": "command_execution",
                "id": "cmd_done",
                "command": "bitloops devql query '{}'",
                "status": "failed",
                "exit_code": 1,
            },
        ]

        raw = common.extract_tool_invocations_raw(payload)
        curated = common.extract_tool_invocations_curated(raw)

        self.assertEqual(len(raw), 1)
        self.assertEqual(raw[0]["tool"], "Bash")
        self.assertEqual(raw[0]["input"]["status"], "failed")
        self.assertEqual(curated[0]["command"], "bitloops devql query '{}'")
        self.assertEqual(curated[0]["status"], "failed")
        self.assertEqual(curated[0]["exit_code"], 1)

    def test_validate_exact_tool_capture(self) -> None:
        error = common.validate_exact_tool_capture(
            require_exact_tools=True,
            output_format="stream-json",
            parsed_payload=[{"type": "result"}],
            reported_tool_total=2,
            invocations_raw=[],
            invocations_curated=[],
            tool_usage_breakdown={},
        )
        self.assertIn("no per-tool events", str(error))

        no_error = common.validate_exact_tool_capture(
            require_exact_tools=True,
            output_format="stream-json",
            parsed_payload=[{"type": "result"}],
            reported_tool_total=2,
            invocations_raw=[{"call_index": 1, "tool": "Read"}],
            invocations_curated=[{"call_index": 1, "tool": "Read", "path": "a.py"}],
            tool_usage_breakdown={},
        )
        self.assertIsNone(no_error)

    def test_load_hook_metrics_from_jsonl(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            hook_path = Path(temp_dir) / "hook.jsonl"
            hook_path.write_text(
                "\n".join(
                    [
                        '{"type":"shell_command","command":"pytest -q"}',
                        '{"type":"file_read","path":"src/main.rs"}',
                        '{"type":"search","query":"tokio::"}',
                        '{"token_input":"11","token_output":4,"estimated_cost":"0.03"}',
                    ]
                ),
                encoding="utf-8",
            )
            with patch.dict("os.environ", {"TEST_HOOK_PATH": str(hook_path)}):
                metrics = common.load_hook_metrics(("TEST_HOOK_PATH",))

        self.assertEqual(metrics.get("tool_calls"), 3)
        self.assertEqual(metrics.get("shell_commands"), 1)
        self.assertEqual(metrics.get("file_reads"), 1)
        self.assertEqual(metrics.get("search_actions"), 1)
        self.assertEqual(metrics.get("token_input"), 11)
        self.assertEqual(metrics.get("token_output"), 4)
        self.assertEqual(metrics.get("estimated_cost"), 0.03)
        self.assertEqual(metrics.get("hook_metrics_path"), str(hook_path))

    def test_merge_metric_metadata_prefers_primary_then_fills_missing(self) -> None:
        merged = common.merge_metric_metadata(
            {
                "token_input": 100,
                "tool_calls": 2,
                "cache_creation_input_tokens": 9,
                "token_metrics_source": "result_usage",
            },
            {
                "token_input": 200,
                "shell_commands": 5,
                "cache_read_input_tokens": 7,
                "cache_creation_ephemeral_5m_input_tokens": 4,
                "cache_creation_ephemeral_1h_input_tokens": 3,
                "hook_metrics_path": "/tmp/hook.jsonl",
                "token_metrics_source": "fallback_scan",
            },
        )
        self.assertEqual(merged["token_input"], 100)
        self.assertEqual(merged["tool_calls"], 2)
        self.assertEqual(merged["shell_commands"], 5)
        self.assertEqual(merged["cache_creation_input_tokens"], 9)
        self.assertEqual(merged["cache_read_input_tokens"], 7)
        self.assertEqual(merged["cache_creation_ephemeral_5m_input_tokens"], 4)
        self.assertEqual(merged["cache_creation_ephemeral_1h_input_tokens"], 3)
        self.assertEqual(merged["hook_metrics_path"], "/tmp/hook.jsonl")
        self.assertEqual(merged["token_metrics_source"], "result_usage")
        self.assertEqual(merged["input_tokens"], 100)
        self.assertIsNone(merged.get("output_tokens"))

    def test_merge_metric_metadata_derives_codex_canonical_token_fields(self) -> None:
        merged = common.merge_metric_metadata(
            {
                "token_input": 12024,
                "token_output": 27,
                "cached_input_tokens": 3456,
                "token_input_uncached": 8568,
                "token_usage_semantics": "codex_turn_completed",
            }
        )

        self.assertEqual(merged["input_tokens"], 8568)
        self.assertEqual(merged["output_tokens"], 27)
        self.assertEqual(merged["cache_read_input_tokens"], 3456)
        self.assertEqual(merged["cache_creation_input_tokens"], 0)
        self.assertEqual(merged["total_input_processed_tokens"], 12024)
        self.assertEqual(merged["total_processed_tokens"], 12051)

    def test_setup_bitloops_starts_daemon_when_stopped(self) -> None:
        responses = [
            ("Bitloops daemon: stopped\n", "", 0, 8),
            ("Bitloops daemon started in detached mode", "", 0, 14),
            ("Bitloops init completed", "", 0, 21),
        ]
        with patch.object(
            common,
            "_ensure_git_branch_for_bitloops_sync",
            return_value=(False, False, None, None, 0),
        ), patch.object(common, "call_command", side_effect=responses) as mock_call:
            metadata = common.setup_bitloops_for_workspace(
                agent_name="claude-code",
                bitloops_bin="bitloops",
                timeout_seconds=30,
            )

        self.assertFalse(metadata["bitloops_daemon_was_running"])
        self.assertTrue(metadata["bitloops_daemon_start_attempted"])
        self.assertFalse(metadata["bitloops_daemon_bootstrap_attempted"])
        self.assertEqual(metadata["bitloops_daemon_start_mode"], "start_detached")
        self.assertTrue(metadata["bitloops_global_lock_enabled"])
        self.assertTrue(metadata["bitloops_global_lock_acquired"])
        self.assertEqual(
            metadata["bitloops_start_command"],
            ["bitloops", "start", "--telemetry=false", "--detached"],
        )
        self.assertIsNone(metadata["bitloops_bootstrap_command"])
        first_command = mock_call.call_args_list[0].args[0]
        second_command = mock_call.call_args_list[1].args[0]
        self.assertEqual(first_command, ["bitloops", "status"])
        self.assertEqual(
            second_command,
            ["bitloops", "start", "--telemetry=false", "--detached"],
        )
        self.assertEqual(
            mock_call.call_args_list[1].kwargs["env"]["BITLOOPS_TELEMETRY_OPTOUT"],
            "1",
        )

    def test_setup_bitloops_bootstraps_daemon_when_needed(self) -> None:
        responses = [
            ("Bitloops daemon: stopped\n", "", 0, 6),
            ("", "Bitloops daemon has not been bootstrapped yet.", 1, 9),
            ("Bitloops daemon started in detached mode", "", 0, 17),
            ("Bitloops init completed", "", 0, 18),
        ]
        with patch.object(
            common,
            "_ensure_git_branch_for_bitloops_sync",
            return_value=(False, False, None, None, 0),
        ), patch.object(common, "call_command", side_effect=responses) as mock_call:
            metadata = common.setup_bitloops_for_workspace(
                agent_name="cursor",
                bitloops_bin="bitloops",
                timeout_seconds=30,
            )

        self.assertTrue(metadata["bitloops_daemon_start_attempted"])
        self.assertTrue(metadata["bitloops_daemon_bootstrap_attempted"])
        self.assertEqual(
            metadata["bitloops_daemon_start_mode"],
            "start_create_default_config",
        )
        self.assertEqual(
            metadata["bitloops_start_command"],
            ["bitloops", "start", "--telemetry=false", "--detached"],
        )
        self.assertEqual(
            metadata["bitloops_bootstrap_command"],
            [
                "bitloops",
                "start",
                "--create-default-config",
                "--telemetry=false",
                "--detached",
            ],
        )
        bootstrap_command = mock_call.call_args_list[2].args[0]
        self.assertEqual(
            bootstrap_command,
            [
                "bitloops",
                "start",
                "--create-default-config",
                "--telemetry=false",
                "--detached",
            ],
        )
        self.assertEqual(
            mock_call.call_args_list[2].kwargs["env"]["BITLOOPS_TELEMETRY_OPTOUT"],
            "1",
        )

    def test_setup_bitloops_uses_non_interactive_init_flags(self) -> None:
        responses = [
            ("Bitloops daemon: running\n", "", 0, 5),
            ("Bitloops init completed", "", 0, 16),
        ]
        with patch.object(
            common,
            "_ensure_git_branch_for_bitloops_sync",
            return_value=(False, False, None, None, 0),
        ), patch.object(common, "call_command", side_effect=responses) as mock_call:
            metadata = common.setup_bitloops_for_workspace(
                agent_name="claude-code",
                bitloops_bin="bitloops",
                timeout_seconds=30,
            )

        init_command = mock_call.call_args_list[1].args[0]
        init_env = mock_call.call_args_list[1].kwargs["env"]
        self.assertEqual(
            init_command,
            [
                "bitloops",
                "init",
                "--agent",
                "claude-code",
                "--telemetry=false",
                "--sync=true",
                "--ingest=true",
                "--no-embeddings",
                "--no-summaries",
            ],
        )
        self.assertEqual(init_env["BITLOOPS_TELEMETRY_OPTOUT"], "1")
        self.assertFalse(metadata["bitloops_install_default_daemon"])
        self.assertTrue(metadata["bitloops_install_default_daemon_requested"])

    def test_setup_bitloops_emits_timing_metadata(self) -> None:
        responses = [
            ("Bitloops daemon: running\n", "", 0, 4),
            ("Bitloops init completed", "", 0, 19),
        ]
        with patch.object(
            common,
            "_ensure_git_branch_for_bitloops_sync",
            return_value=(False, False, None, None, 0),
        ), patch.object(common, "call_command", side_effect=responses):
            metadata = common.setup_bitloops_for_workspace(
                agent_name="cursor",
                bitloops_bin="bitloops",
                timeout_seconds=30,
            )

        self.assertIn("bitloops_status_elapsed_ms", metadata)
        self.assertIn("bitloops_daemon_start_elapsed_ms", metadata)
        self.assertIn("bitloops_init_elapsed_ms", metadata)
        self.assertIn("bitloops_setup_elapsed_ms", metadata)
        self.assertEqual(metadata["bitloops_status_elapsed_ms"], 4)
        self.assertEqual(metadata["bitloops_daemon_start_elapsed_ms"], 0)
        self.assertEqual(metadata["bitloops_init_elapsed_ms"], 19)
        self.assertGreaterEqual(metadata["bitloops_setup_elapsed_ms"], 0)

    def test_setup_bitloops_retries_without_ingest_when_flag_is_unsupported(self) -> None:
        responses = [
            ("Bitloops daemon: running\n", "", 0, 4),
            (
                "",
                "error: unexpected argument '--ingest' found\nUsage: bitloops init ...",
                2,
                9,
            ),
            ("Bitloops init completed", "", 0, 13),
        ]
        with patch.object(
            common,
            "_ensure_git_branch_for_bitloops_sync",
            return_value=(False, False, None, None, 0),
        ), patch.object(common, "call_command", side_effect=responses) as mock_call:
            metadata = common.setup_bitloops_for_workspace(
                agent_name="claude-code",
                bitloops_bin="bitloops",
                timeout_seconds=30,
            )

        self.assertTrue(metadata["bitloops_init_fallback_used"])
        self.assertEqual(
            metadata["bitloops_init_command"],
            [
                "bitloops",
                "init",
                "--agent",
                "claude-code",
                "--telemetry=false",
                "--sync=true",
                "--no-embeddings",
                "--no-summaries",
            ],
        )
        self.assertEqual(metadata["bitloops_init_elapsed_ms"], 22)
        self.assertEqual(
            mock_call.call_args_list[1].args[0],
            [
                "bitloops",
                "init",
                "--agent",
                "claude-code",
                "--telemetry=false",
                "--sync=true",
                "--ingest=true",
                "--no-embeddings",
                "--no-summaries",
            ],
        )
        self.assertEqual(
            mock_call.call_args_list[2].args[0],
            [
                "bitloops",
                "init",
                "--agent",
                "claude-code",
                "--telemetry=false",
                "--sync=true",
                "--no-embeddings",
                "--no-summaries",
            ],
        )

    def test_setup_bitloops_retries_on_database_locked(self) -> None:
        responses = [
            ("Bitloops daemon: running\n", "", 0, 4),
            ("", "database is locked", 1, 9),
            ("", "Error code 5: database is locked", 1, 10),
            ("Bitloops init completed", "", 0, 13),
        ]
        with patch.dict(
            common.os.environ,
            {
                "BITLOOPS_INIT_DB_LOCK_RETRIES": "3",
                "BITLOOPS_INIT_DB_LOCK_RETRY_DELAY_MS": "1",
            },
            clear=False,
        ), patch.object(
            common,
            "_ensure_git_branch_for_bitloops_sync",
            return_value=(False, False, None, None, 0),
        ), patch.object(
            common.time,
            "sleep",
        ) as mock_sleep, patch.object(
            common,
            "call_command",
            side_effect=responses,
        ) as mock_call:
            metadata = common.setup_bitloops_for_workspace(
                agent_name="codex",
                bitloops_bin="bitloops",
                timeout_seconds=30,
            )

        self.assertEqual(metadata["bitloops_init_db_lock_retry_count"], 2)
        self.assertTrue(metadata["bitloops_init_db_lock_retry_used"])
        self.assertEqual(metadata["bitloops_init_elapsed_ms"], 32)
        self.assertEqual(mock_call.call_count, 4)
        self.assertEqual(mock_sleep.call_count, 2)
        self.assertAlmostEqual(float(mock_sleep.call_args_list[0].args[0]), 0.001)
        self.assertAlmostEqual(float(mock_sleep.call_args_list[1].args[0]), 0.002)

    def test_setup_bitloops_supports_no_summaries_alias_and_embedding_mode(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            config_path = workspace / "config.toml"
            config_path.write_text("[semantic_clones]\nsummary_mode = \"auto\"\n", encoding="utf-8")
            call_log: list[list[str]] = []

            def fake_call_command(command, timeout_seconds, *, env=None, cwd=None):
                del timeout_seconds, env
                call_log.append(list(command))
                if command[:2] == ["bitloops", "status"]:
                    return "Bitloops daemon: running\n", "", 0, 5
                if command[:2] == ["bitloops", "init"]:
                    rendered = config_path.read_text(encoding="utf-8")
                    self.assertIn('embedding_mode = "deterministic"', rendered)
                    self.assertIn('summary_mode = "auto"', rendered)
                    self.assertEqual(cwd, str(workspace))
                    return "Bitloops init completed", "", 0, 16
                raise AssertionError(f"Unexpected command: {command}")

            with patch.object(
                common,
                "_ensure_git_branch_for_bitloops_sync",
                return_value=(False, False, None, None, 0),
            ), patch.object(common, "call_command", side_effect=fake_call_command):
                metadata = common.setup_bitloops_for_workspace(
                    agent_name="claude-code",
                    bitloops_bin="bitloops",
                    timeout_seconds=30,
                    ingest=True,
                    embeddings_runtime="local",
                    summary_mode="off",
                    embedding_mode="deterministic",
                    cwd=str(workspace),
                )
                rendered = config_path.read_text(encoding="utf-8")

        self.assertEqual(
            call_log[1],
            [
                "bitloops",
                "init",
                "--agent",
                "claude-code",
                "--telemetry=false",
                "--sync=true",
                "--ingest=true",
                "--embeddings-runtime",
                "local",
                "--no-summaries",
            ],
        )
        self.assertTrue(metadata["bitloops_no_summaries"])
        self.assertEqual(metadata["bitloops_summary_mode"], "off")
        self.assertEqual(metadata["bitloops_embedding_mode"], "deterministic")
        self.assertIn('summary_mode = "auto"', rendered)
        self.assertIn('embedding_mode = "deterministic"', rendered)

    def test_setup_bitloops_defaults_to_no_embeddings_and_no_summaries(self) -> None:
        responses = [
            ("Bitloops daemon: running\n", "", 0, 5),
            ("Bitloops init completed", "", 0, 16),
        ]
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            with patch.object(
                common,
                "_ensure_git_branch_for_bitloops_sync",
                return_value=(False, False, None, None, 0),
            ), patch.object(common, "call_command", side_effect=responses) as mock_call:
                metadata = common.setup_bitloops_for_workspace(
                    agent_name="claude-code",
                    bitloops_bin="bitloops",
                    timeout_seconds=30,
                    cwd=str(workspace),
                )

        self.assertEqual(
            mock_call.call_args_list[1].args[0],
            [
                "bitloops",
                "init",
                "--agent",
                "claude-code",
                "--telemetry=false",
                "--sync=true",
                "--ingest=true",
                "--no-embeddings",
                "--no-summaries",
            ],
        )
        self.assertTrue(metadata["bitloops_no_embeddings"])
        self.assertTrue(metadata["bitloops_no_summaries"])
        self.assertEqual(metadata["bitloops_summary_mode"], "off")

    def test_setup_bitloops_supports_no_summaries_flag(self) -> None:
        responses = [
            ("Bitloops daemon: running\n", "", 0, 5),
            ("Bitloops init completed", "", 0, 16),
        ]
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            with patch.object(
                common,
                "_ensure_git_branch_for_bitloops_sync",
                return_value=(False, False, None, None, 0),
            ), patch.object(common, "call_command", side_effect=responses) as mock_call:
                metadata = common.setup_bitloops_for_workspace(
                    agent_name="claude-code",
                    bitloops_bin="bitloops",
                    timeout_seconds=30,
                    no_summaries=True,
                    cwd=str(workspace),
                )

        self.assertEqual(
            mock_call.call_args_list[1].args[0],
            [
                "bitloops",
                "init",
                "--agent",
                "claude-code",
                "--telemetry=false",
                "--sync=true",
                "--ingest=true",
                "--no-embeddings",
                "--no-summaries",
            ],
        )
        self.assertTrue(metadata["bitloops_no_summaries"])
        self.assertEqual(metadata["bitloops_summary_mode"], "off")

    def test_setup_bitloops_falls_back_when_no_summaries_flag_is_unsupported(self) -> None:
        responses = [
            ("Bitloops daemon: running\n", "", 0, 5),
            (
                "",
                "error: unexpected argument '--no-summaries' found\nUsage: bitloops init ...",
                1,
                9,
            ),
            ("Bitloops init completed", "", 0, 16),
        ]
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            with patch.object(
                common,
                "_ensure_git_branch_for_bitloops_sync",
                return_value=(False, False, None, None, 0),
            ), patch.object(common, "call_command", side_effect=responses) as mock_call:
                metadata = common.setup_bitloops_for_workspace(
                    agent_name="claude-code",
                    bitloops_bin="bitloops",
                    timeout_seconds=30,
                    no_summaries=True,
                    cwd=str(workspace),
                )

            rendered = (workspace / "config.toml").read_text(encoding="utf-8")

        self.assertEqual(
            mock_call.call_args_list[1].args[0],
            [
                "bitloops",
                "init",
                "--agent",
                "claude-code",
                "--telemetry=false",
                "--sync=true",
                "--ingest=true",
                "--no-embeddings",
                "--no-summaries",
            ],
        )
        self.assertEqual(
            mock_call.call_args_list[2].args[0],
            [
                "bitloops",
                "init",
                "--agent",
                "claude-code",
                "--telemetry=false",
                "--sync=true",
                "--ingest=true",
                "--no-embeddings",
            ],
        )
        self.assertTrue(metadata["bitloops_init_fallback_used"])
        self.assertTrue(metadata["bitloops_no_summaries"])
        self.assertIn('summary_mode = "off"', rendered)

    def test_setup_bitloops_supports_no_embeddings_flag(self) -> None:
        responses = [
            ("Bitloops daemon: running\n", "", 0, 5),
            ("Bitloops init completed", "", 0, 16),
        ]
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            config_path = workspace / "config.toml"
            with patch.object(
                common,
                "_ensure_git_branch_for_bitloops_sync",
                return_value=(False, False, None, None, 0),
            ), patch.object(common, "call_command", side_effect=responses) as mock_call:
                metadata = common.setup_bitloops_for_workspace(
                    agent_name="claude-code",
                    bitloops_bin="bitloops",
                    timeout_seconds=30,
                    no_embeddings=True,
                    cwd=str(workspace),
                )

        self.assertEqual(
            mock_call.call_args_list[1].args[0],
            [
                "bitloops",
                "init",
                "--agent",
                "claude-code",
                "--telemetry=false",
                "--sync=true",
                "--ingest=true",
                "--no-embeddings",
                "--no-summaries",
            ],
        )
        self.assertTrue(metadata["bitloops_no_embeddings"])
        self.assertFalse(config_path.exists())

    def test_setup_bitloops_omits_embeddings_runtime_when_no_embeddings_is_true(self) -> None:
        responses = [
            ("Bitloops daemon: running\n", "", 0, 5),
            ("Bitloops init completed", "", 0, 16),
        ]
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            with patch.object(
                common,
                "_ensure_git_branch_for_bitloops_sync",
                return_value=(False, False, None, None, 0),
            ), patch.object(common, "call_command", side_effect=responses) as mock_call:
                metadata = common.setup_bitloops_for_workspace(
                    agent_name="claude-code",
                    bitloops_bin="bitloops",
                    timeout_seconds=30,
                    embeddings_runtime="platform",
                    no_embeddings=True,
                    no_summaries=True,
                    summary_mode="auto",
                    cwd=str(workspace),
                )

        self.assertEqual(
            mock_call.call_args_list[1].args[0],
            [
                "bitloops",
                "init",
                "--agent",
                "claude-code",
                "--telemetry=false",
                "--sync=true",
                "--ingest=true",
                "--no-embeddings",
                "--no-summaries",
            ],
        )
        self.assertTrue(metadata["bitloops_no_embeddings"])
        self.assertTrue(metadata["bitloops_no_summaries"])
        self.assertEqual(metadata["bitloops_embeddings_runtime"], "platform")

    def test_setup_bitloops_auto_summary_mode_keeps_summaries_enabled(self) -> None:
        responses = [
            ("Bitloops daemon: running\n", "", 0, 5),
            ("Bitloops init completed", "", 0, 16),
        ]
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            with patch.object(
                common,
                "_ensure_git_branch_for_bitloops_sync",
                return_value=(False, False, None, None, 0),
            ), patch.object(common, "call_command", side_effect=responses) as mock_call:
                metadata = common.setup_bitloops_for_workspace(
                    agent_name="claude-code",
                    bitloops_bin="bitloops",
                    timeout_seconds=30,
                    embeddings_runtime="platform",
                    summary_mode="auto",
                    cwd=str(workspace),
                )

        self.assertEqual(
            mock_call.call_args_list[1].args[0],
            [
                "bitloops",
                "init",
                "--agent",
                "claude-code",
                "--telemetry=false",
                "--sync=true",
                "--ingest=true",
                "--embeddings-runtime",
                "platform",
                "--summaries-runtime",
                "platform",
            ],
        )
        self.assertFalse(metadata["bitloops_no_summaries"])
        self.assertEqual(metadata["bitloops_summary_mode"], "auto")

    def test_setup_bitloops_summary_mode_on_forces_summaries_enabled(self) -> None:
        responses = [
            ("Bitloops daemon: running\n", "", 0, 5),
            ("Bitloops init completed", "", 0, 16),
        ]
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            with patch.object(
                common_impl,
                "_ensure_git_branch_for_bitloops_sync",
                return_value=(False, False, None, None, 0),
            ), patch.object(common_impl, "call_command", side_effect=responses) as mock_call:
                metadata = common_impl.setup_bitloops_for_workspace(
                    agent_name="codex",
                    bitloops_bin="bitloops",
                    timeout_seconds=30,
                    embeddings_runtime="platform",
                    summary_mode="on",
                    cwd=str(workspace),
                )

        self.assertEqual(
            mock_call.call_args_list[1].args[0],
            [
                "bitloops",
                "init",
                "--agent",
                "codex",
                "--telemetry=false",
                "--sync=true",
                "--ingest=true",
                "--embeddings-runtime",
                "platform",
                "--summaries-runtime",
                "platform",
            ],
        )
        self.assertFalse(metadata["bitloops_no_summaries"])
        self.assertEqual(metadata["bitloops_summary_mode"], "on")

    def test_setup_bitloops_rejects_conflicting_summary_controls(self) -> None:
        with patch.object(
            common_impl,
            "_ensure_git_branch_for_bitloops_sync",
        ) as mock_git_sync, patch.object(common_impl, "call_command") as mock_call:
            with self.assertRaisesRegex(
                ValueError,
                "cannot be combined with --bitloops-summary-mode on",
            ):
                common_impl.setup_bitloops_for_workspace(
                    agent_name="codex",
                    bitloops_bin="bitloops",
                    timeout_seconds=30,
                    no_summaries=True,
                    summary_mode="on",
                )

        mock_git_sync.assert_not_called()
        mock_call.assert_not_called()

    def test_setup_bitloops_records_global_lock_wait_metadata(self) -> None:
        responses = [
            ("Bitloops daemon: running\n", "", 0, 5),
            ("Bitloops init completed", "", 0, 16),
        ]

        @contextlib.contextmanager
        def fake_lock(**_kwargs):
            yield 12

        with patch.object(
            common,
            "_ensure_git_branch_for_bitloops_sync",
            return_value=(False, False, None, None, 0),
        ), patch.object(
            common,
            "_acquire_bitloops_global_lock",
            side_effect=fake_lock,
        ), patch.object(common, "call_command", side_effect=responses):
            metadata = common.setup_bitloops_for_workspace(
                agent_name="cursor",
                bitloops_bin="bitloops",
                timeout_seconds=30,
            )

        self.assertTrue(metadata["bitloops_global_lock_enabled"])
        self.assertTrue(metadata["bitloops_global_lock_acquired"])
        self.assertEqual(metadata["bitloops_global_lock_wait_elapsed_ms"], 12)
        self.assertTrue(metadata["bitloops_setup_serialized"])

    def test_build_bitloops_task_environment_sets_home_and_xdg_roots(self) -> None:
        sandbox = {
            "mode": "per_task_daemon",
            "home_root": "/tmp/benchkit/home",
            "xdg_config_home": "/tmp/benchkit/home/xdg",
            "xdg_state_home": "/tmp/benchkit/home/xdg-state",
            "xdg_cache_home": "/tmp/benchkit/home/xdg-cache",
            "xdg_data_home": "/tmp/benchkit/home/xdg-data",
        }
        with patch.dict(common.os.environ, {"PATH": "/usr/bin", "HOME": "/Users/tester"}, clear=True):
            env = common.build_bitloops_task_environment(sandbox)

        assert env is not None
        self.assertEqual(env["HOME"], sandbox["home_root"])
        self.assertEqual(env["USERPROFILE"], sandbox["home_root"])
        self.assertEqual(env["XDG_CONFIG_HOME"], sandbox["xdg_config_home"])
        self.assertEqual(env["XDG_STATE_HOME"], sandbox["xdg_state_home"])
        self.assertEqual(env["XDG_CACHE_HOME"], sandbox["xdg_cache_home"])
        self.assertEqual(env["XDG_DATA_HOME"], sandbox["xdg_data_home"])
        self.assertEqual(env["CARGO_HOME"], "/Users/tester/.cargo")
        self.assertEqual(env["RUSTUP_HOME"], "/Users/tester/.rustup")
        self.assertEqual(env["CODEX_HOME"], "/Users/tester/.codex")
        self.assertEqual(env["AWS_CONFIG_FILE"], "/Users/tester/.aws/config")
        self.assertEqual(
            env["AWS_SHARED_CREDENTIALS_FILE"],
            "/Users/tester/.aws/credentials",
        )
        self.assertEqual(
            env["BITLOOPS_DAEMON_CONFIG_PATH_OVERRIDE"],
            "/tmp/benchkit/home/Library/Application Support/bitloops/config.toml",
        )
        self.assertEqual(env["BITLOOPS_BENCHKIT_SANDBOX_MODE"], "per_task_daemon")
        self.assertEqual(env["PATH"], "/usr/bin")

    def test_seed_bitloops_cloud_inference_profiles_writes_sandbox_config(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            home_root = Path(temp_dir) / "home"
            sandbox = {
                "mode": "per_task_daemon",
                "home_root": str(home_root),
            }

            config_path = common_impl._seed_bitloops_cloud_inference_profiles(
                sandbox,
                summary_mode="auto",
            )

            assert config_path is not None
            rendered = config_path.read_text(encoding="utf-8")

        self.assertIn("[telemetry]\nenabled = false", rendered)
        self.assertIn("[inference.runtimes.bitloops_platform_embeddings]", rendered)
        self.assertIn('driver = "bitloops_embeddings_ipc"', rendered)
        self.assertIn("[inference.profiles.summary_llm]", rendered)
        self.assertIn('driver = "bitloops_platform_chat"', rendered)
        self.assertIn('model = "ministral-3-3b-instruct"', rendered)
        self.assertIn('summary_mode = "auto"', rendered)
        self.assertNotIn('summary_generation = "summary_llm"', rendered)
        self.assertNotIn("ollama", rendered.lower())

    def test_seed_bitloops_cloud_inference_profiles_can_bind_cloud_semantics(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            home_root = Path(temp_dir) / "home"
            sandbox = {
                "mode": "per_task_daemon",
                "home_root": str(home_root),
            }

            config_path = common_impl._seed_bitloops_cloud_inference_profiles(
                sandbox,
                summary_mode="auto",
                bind_semantic_inference=True,
                summaries_runtime="platform",
            )

            assert config_path is not None
            rendered = config_path.read_text(encoding="utf-8")

        self.assertIn("[semantic_clones.inference]", rendered)
        self.assertIn('code_embeddings = "platform_code"', rendered)
        self.assertIn('summary_generation = "summary_llm"', rendered)
        self.assertIn('summary_embeddings = "platform_code"', rendered)
        self.assertNotIn("summary_local", rendered)
        self.assertNotIn("ollama", rendered.lower())

    def test_apply_bitloops_repo_semantic_modes_writes_cloud_bindings(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)

            config_path = common_impl._apply_bitloops_repo_semantic_modes(
                summary_mode="auto",
                embedding_profile="platform_code",
                summary_generation_profile="summary_llm",
                summary_embedding_profile="platform_code",
                cwd=str(workspace),
            )

            assert config_path is not None
            rendered = config_path.read_text(encoding="utf-8")

        self.assertIn("[semantic_clones]", rendered)
        self.assertIn('summary_mode = "auto"', rendered)
        self.assertIn("[semantic_clones.inference]", rendered)
        self.assertIn('code_embeddings = "platform_code"', rendered)
        self.assertIn('summary_generation = "summary_llm"', rendered)
        self.assertIn('summary_embeddings = "platform_code"', rendered)

    def test_mirror_bitloops_auth_state_uses_application_support_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            original_home = root / "original"
            sandbox_home = root / "sandbox"
            runtime_source = (
                original_home
                / "Library"
                / "Application Support"
                / "bitloops"
                / "stores"
                / "runtime"
                / "runtime.sqlite"
            )
            runtime_source.parent.mkdir(parents=True)
            connection = sqlite3.connect(runtime_source)
            try:
                connection.execute(
                    """
                    CREATE TABLE runtime_documents (
                        document_kind TEXT PRIMARY KEY,
                        payload TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    )
                    """
                )
                connection.execute(
                    """
                    INSERT INTO runtime_documents (document_kind, payload, updated_at)
                    VALUES (?, ?, ?)
                    """,
                    (
                        "workos_auth_session_state",
                        json.dumps({"access_token": "token"}),
                        "2026-05-21 00:00:00",
                    ),
                )
                connection.commit()
            finally:
                connection.close()

            with patch.object(common_impl.sys, "platform", "darwin"):
                common_impl._mirror_bitloops_auth_state_into_sandbox(
                    original_home=original_home,
                    sandbox_home=sandbox_home,
                )

            runtime_destination = (
                sandbox_home
                / ".local"
                / "state"
                / "bitloops"
                / "daemon"
                / "runtime.sqlite"
            )
            copied = sqlite3.connect(runtime_destination).execute(
                "SELECT payload FROM runtime_documents WHERE document_kind = ?",
                ("workos_auth_session_state",),
            ).fetchone()

        self.assertIsNotNone(copied)
        self.assertEqual(json.loads(copied[0])["access_token"], "token")

    def test_bitloops_authorization_metadata_accepts_gateway_token(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime_db_path = Path(temp_dir) / "runtime.sqlite"

            metadata = common_impl._bitloops_authorization_metadata(
                runtime_db_path,
                env={"BITLOOPS_PLATFORM_GATEWAY_TOKEN": "token"},
            )

        self.assertTrue(metadata["bitloops_cloud_authorized"])
        self.assertEqual(
            metadata["bitloops_cloud_authorization_source"],
            "env:BITLOOPS_PLATFORM_GATEWAY_TOKEN",
        )
        self.assertTrue(metadata["bitloops_platform_gateway_token_present"])
        self.assertEqual(metadata["bitloops_auth_runtime_db_path"], str(runtime_db_path))

    def test_bitloops_authorization_metadata_probes_login_token(self) -> None:
        with patch.object(
            common_impl,
            "call_command",
            return_value=("fresh-token\n", "", 0, 12),
        ) as mock_call:
            metadata = common_impl._bitloops_authorization_metadata(
                None,
                env={"HOME": "/tmp/benchkit/home"},
                binary="bitloops",
                cwd="/tmp/workspace",
            )

        self.assertTrue(metadata["bitloops_cloud_authorized"])
        self.assertEqual(metadata["bitloops_cloud_authorization_source"], "login_token_probe")
        self.assertTrue(metadata["bitloops_login_token_probe_attempted"])
        self.assertTrue(metadata["bitloops_login_token_probe_ok"])
        self.assertEqual(metadata["bitloops_login_token_probe_elapsed_ms"], 12)
        mock_call.assert_called_once_with(
            ["bitloops", "login", "token"],
            15,
            env={"HOME": "/tmp/benchkit/home"},
            cwd="/tmp/workspace",
        )

    def test_bitloops_authorization_metadata_rejects_static_token_when_probe_fails(self) -> None:
        with patch.object(
            common_impl,
            "call_command",
            return_value=("", "expired", 1, 12),
        ):
            metadata = common_impl._bitloops_authorization_metadata(
                None,
                env={"BITLOOPS_PLATFORM_GATEWAY_TOKEN": "expired-token"},
                binary="bitloops",
                cwd="/tmp/workspace",
            )

        self.assertFalse(metadata["bitloops_cloud_authorized"])
        self.assertIsNone(metadata["bitloops_cloud_authorization_source"])
        self.assertTrue(metadata["bitloops_platform_gateway_token_present"])
        self.assertTrue(metadata["bitloops_login_token_probe_attempted"])
        self.assertFalse(metadata["bitloops_login_token_probe_ok"])
        self.assertEqual(metadata["bitloops_login_token_probe_return_code"], 1)

    def test_bitloops_init_environment_injects_fresh_token_only_for_init(self) -> None:
        base_env = {"HOME": "/tmp/benchkit/home"}
        with patch.object(
            common_impl,
            "call_command",
            return_value=("fresh-token\n", "", 0, 12),
        ) as mock_call:
            init_env, metadata = common_impl._bitloops_init_environment_with_fresh_platform_token(
                binary="bitloops",
                env=base_env,
                cwd="/tmp/workspace",
            )

        self.assertNotIn("BITLOOPS_PLATFORM_GATEWAY_TOKEN", base_env)
        self.assertEqual(init_env["BITLOOPS_PLATFORM_GATEWAY_TOKEN"], "fresh-token")
        self.assertTrue(metadata["bitloops_init_platform_token_probe_ok"])
        self.assertTrue(metadata["bitloops_init_platform_token_injected"])
        mock_call.assert_called_once_with(
            ["bitloops", "login", "token"],
            15,
            env=base_env,
            cwd="/tmp/workspace",
        )

    def test_build_bitloops_task_environment_strips_platform_gateway_token(self) -> None:
        sandbox = {
            "mode": "per_task_daemon",
            "home_root": "/tmp/benchkit/home",
            "xdg_config_home": "/tmp/benchkit/home/xdg",
            "xdg_state_home": "/tmp/benchkit/home/xdg-state",
            "xdg_cache_home": "/tmp/benchkit/home/xdg-cache",
            "xdg_data_home": "/tmp/benchkit/home/xdg-data",
        }
        with patch.dict(
            common_impl.os.environ,
            {
                "HOME": "/Users/tester",
                "BITLOOPS_PLATFORM_GATEWAY_TOKEN": "short-lived-token",
            },
            clear=True,
        ), patch.object(
            common_impl,
            "_mirror_aws_auth_cache_into_sandbox",
        ), patch.object(
            common_impl,
            "_mirror_bitloops_auth_state_into_sandbox",
        ), patch.object(
            common_impl,
            "_ensure_macos_default_keychain_for_sandbox",
        ):
            env = common_impl.build_bitloops_task_environment(sandbox)

        assert env is not None
        self.assertNotIn("BITLOOPS_PLATFORM_GATEWAY_TOKEN", env)
        self.assertEqual(env["BENCHKIT_BITLOOPS_STRIPPED_PLATFORM_GATEWAY_TOKEN"], "1")

    def test_build_bitloops_task_environment_preserves_existing_rust_cache_env(self) -> None:
        sandbox = {
            "mode": "per_task_daemon",
            "home_root": "/tmp/benchkit/home",
            "xdg_config_home": "/tmp/benchkit/home/xdg",
            "xdg_state_home": "/tmp/benchkit/home/xdg-state",
            "xdg_cache_home": "/tmp/benchkit/home/xdg-cache",
            "xdg_data_home": "/tmp/benchkit/home/xdg-data",
        }
        with patch.dict(
            common.os.environ,
            {
                "HOME": "/Users/tester",
                "CARGO_HOME": "/shared/cargo",
                "RUSTUP_HOME": "/shared/rustup",
            },
            clear=True,
        ):
            env = common.build_bitloops_task_environment(sandbox)

        assert env is not None
        self.assertEqual(env["HOME"], sandbox["home_root"])
        self.assertEqual(env["CARGO_HOME"], "/shared/cargo")
        self.assertEqual(env["RUSTUP_HOME"], "/shared/rustup")

    def test_build_bitloops_task_environment_preserves_existing_codex_home(self) -> None:
        sandbox = {
            "mode": "per_task_daemon",
            "home_root": "/tmp/benchkit/home",
            "xdg_config_home": "/tmp/benchkit/home/xdg",
            "xdg_state_home": "/tmp/benchkit/home/xdg-state",
            "xdg_cache_home": "/tmp/benchkit/home/xdg-cache",
            "xdg_data_home": "/tmp/benchkit/home/xdg-data",
        }
        with patch.dict(
            common.os.environ,
            {
                "HOME": "/Users/tester",
                "CODEX_HOME": "/custom/codex-home",
            },
            clear=True,
        ):
            env = common.build_bitloops_task_environment(sandbox)

        assert env is not None
        self.assertEqual(env["HOME"], sandbox["home_root"])
        self.assertEqual(env["CODEX_HOME"], "/custom/codex-home")

    def test_build_bitloops_task_environment_reanchors_zsh_startup_to_sandbox_home(self) -> None:
        sandbox = {
            "mode": "per_task_daemon",
            "home_root": "/tmp/benchkit/home",
            "xdg_config_home": "/tmp/benchkit/home/xdg",
            "xdg_state_home": "/tmp/benchkit/home/xdg-state",
            "xdg_cache_home": "/tmp/benchkit/home/xdg-cache",
            "xdg_data_home": "/tmp/benchkit/home/xdg-data",
        }
        with patch.dict(
            common.os.environ,
            {
                "HOME": "/Users/tester",
                "ZDOTDIR": "/Users/tester/.config/zsh",
            },
            clear=True,
        ):
            env = common.build_bitloops_task_environment(sandbox)

        assert env is not None
        self.assertEqual(env["HOME"], sandbox["home_root"])
        self.assertEqual(env["ZDOTDIR"], sandbox["home_root"])

    def test_build_bitloops_task_environment_preserves_existing_aws_env(self) -> None:
        sandbox = {
            "mode": "per_task_daemon",
            "home_root": "/tmp/benchkit/home",
            "xdg_config_home": "/tmp/benchkit/home/xdg",
            "xdg_state_home": "/tmp/benchkit/home/xdg-state",
            "xdg_cache_home": "/tmp/benchkit/home/xdg-cache",
            "xdg_data_home": "/tmp/benchkit/home/xdg-data",
        }
        with patch.dict(
            common.os.environ,
            {
                "HOME": "/Users/tester",
                "AWS_CONFIG_FILE": "/custom/aws/config",
                "AWS_SHARED_CREDENTIALS_FILE": "/custom/aws/credentials",
            },
            clear=True,
        ):
            env = common.build_bitloops_task_environment(sandbox)

        assert env is not None
        self.assertEqual(env["HOME"], sandbox["home_root"])
        self.assertEqual(env["AWS_CONFIG_FILE"], "/custom/aws/config")
        self.assertEqual(
            env["AWS_SHARED_CREDENTIALS_FILE"],
            "/custom/aws/credentials",
        )

    def test_build_bitloops_task_environment_mirrors_aws_login_cache(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            original_home = root / "real-home"
            sandbox_home = root / "sandbox-home"
            login_cache = original_home / ".aws" / "login" / "cache"
            sso_cache = original_home / ".aws" / "sso" / "cache"
            login_cache.mkdir(parents=True)
            sso_cache.mkdir(parents=True)
            (login_cache / "token.json").write_text('{"token":"abc"}', encoding="utf-8")
            (sso_cache / "token.json").write_text('{"token":"xyz"}', encoding="utf-8")
            sandbox = {
                "mode": "per_task_daemon",
                "home_root": str(sandbox_home),
                "xdg_config_home": str(sandbox_home / "xdg"),
                "xdg_state_home": str(sandbox_home / "xdg-state"),
                "xdg_cache_home": str(sandbox_home / "xdg-cache"),
                "xdg_data_home": str(sandbox_home / "xdg-data"),
            }
            with patch.dict(
                common.os.environ,
                {"HOME": str(original_home)},
                clear=True,
            ):
                env = common.build_bitloops_task_environment(sandbox)
                assert env is not None
                self.assertEqual(env["HOME"], str(sandbox_home))
                self.assertTrue((sandbox_home / ".aws" / "login" / "cache" / "token.json").exists())
                self.assertTrue((sandbox_home / ".aws" / "sso" / "cache" / "token.json").exists())

    def test_build_bitloops_task_environment_mirrors_opencode_auth_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            original_home = root / "real-home"
            sandbox_home = root / "sandbox-home"
            auth_path = original_home / ".local" / "share" / "opencode" / "auth.json"
            config_path = original_home / ".config" / "opencode" / "opencode.json"
            auth_path.parent.mkdir(parents=True)
            config_path.parent.mkdir(parents=True)
            auth_path.write_text('{"fireworks-ai":{"type":"api","key":"secret"}}', encoding="utf-8")
            config_path.write_text('{"model":"fireworks-ai/accounts/fireworks/models/qwen3p6-plus"}', encoding="utf-8")
            sandbox = {
                "mode": "per_task_daemon",
                "home_root": str(sandbox_home),
                "xdg_config_home": str(sandbox_home / "xdg"),
                "xdg_state_home": str(sandbox_home / "xdg-state"),
                "xdg_cache_home": str(sandbox_home / "xdg-cache"),
                "xdg_data_home": str(sandbox_home / "xdg-data"),
            }
            with patch.dict(
                common.os.environ,
                {"HOME": str(original_home)},
                clear=True,
            ):
                env = common.build_bitloops_task_environment(sandbox)

            assert env is not None
            mirrored_auth = sandbox_home / "xdg-data" / "opencode" / "auth.json"
            mirrored_config = sandbox_home / "xdg" / "opencode" / "opencode.json"
            self.assertEqual(mirrored_auth.read_text(encoding="utf-8"), auth_path.read_text(encoding="utf-8"))
            self.assertEqual(
                mirrored_config.read_text(encoding="utf-8"),
                config_path.read_text(encoding="utf-8"),
            )

    def test_build_bitloops_task_environment_mirrors_bitloops_auth_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            original_home = root / "real-home"
            sandbox_home = root / "sandbox-home"
            runtime_state_path = (
                original_home / ".local" / "state" / "bitloops" / "daemon" / "runtime.sqlite"
            )
            runtime_state_path.parent.mkdir(parents=True)
            connection = sqlite3.connect(runtime_state_path)
            try:
                connection.execute(
                    """
                    CREATE TABLE runtime_documents (
                        document_kind TEXT PRIMARY KEY,
                        payload TEXT NOT NULL,
                        updated_at TEXT NOT NULL DEFAULT (datetime('now'))
                    )
                    """
                )
                connection.executemany(
                    """
                    INSERT INTO runtime_documents (document_kind, payload)
                    VALUES (?, ?)
                    """,
                    [
                        ("workos_auth_session_state", '{"token":"abc"}'),
                        ("supervisor_service_metadata", '{"service":"global"}'),
                    ],
                )
                connection.commit()
            finally:
                connection.close()
            sandbox = {
                "mode": "per_task_daemon",
                "home_root": str(sandbox_home),
                "xdg_config_home": str(sandbox_home / "xdg"),
                "xdg_state_home": str(sandbox_home / "xdg-state"),
                "xdg_cache_home": str(sandbox_home / "xdg-cache"),
                "xdg_data_home": str(sandbox_home / "xdg-data"),
            }
            with patch.dict(
                common.os.environ,
                {"HOME": str(original_home)},
                clear=True,
            ):
                env = common.build_bitloops_task_environment(sandbox)

            assert env is not None
            mirrored_runtime_state = (
                sandbox_home / ".local" / "state" / "bitloops" / "daemon" / "runtime.sqlite"
            )
            mirrored_connection = sqlite3.connect(mirrored_runtime_state)
            try:
                rows = mirrored_connection.execute(
                    """
                    SELECT document_kind, payload
                    FROM runtime_documents
                    ORDER BY document_kind
                    """
                ).fetchall()
            finally:
                mirrored_connection.close()
            self.assertEqual(rows, [("workos_auth_session_state", '{"token":"abc"}')])

    def test_wait_for_task_daemon_ready_uses_port_probe_only(self) -> None:
        process = SimpleNamespace(poll=lambda: None)

        class _Response:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

        with patch.object(common.urllib.request, "urlopen", return_value=_Response()) as mock_urlopen:
            common._wait_for_task_daemon_ready(
                timeout=1,
                port=43210,
                process=process,
                stderr_log_path=Path("/tmp/nonexistent-daemon.stderr.log"),
            )

        mock_urlopen.assert_called_once_with("http://127.0.0.1:43210/devql/sdl", timeout=1)

    def test_wait_for_task_daemon_ready_reports_early_process_exit(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            stderr_log_path = Path(temp_dir) / "daemon.stderr.log"
            stderr_log_path.write_text("launch failed", encoding="utf-8")
            process = SimpleNamespace(poll=lambda: 17)

            with self.assertRaisesRegex(
                RuntimeError,
                r"task daemon exited before becoming ready \(exit=17\)",
            ):
                common._wait_for_task_daemon_ready(
                    timeout=1,
                    port=43210,
                    process=process,
                    stderr_log_path=stderr_log_path,
                )

    def test_build_bitloops_task_environment_seeds_macos_keychain_lookup(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            sandbox_home = root / "sandbox-home"
            sandbox = {
                "mode": "per_task_daemon",
                "home_root": str(sandbox_home),
                "xdg_config_home": str(sandbox_home / "xdg"),
                "xdg_state_home": str(sandbox_home / "xdg-state"),
                "xdg_cache_home": str(sandbox_home / "xdg-cache"),
                "xdg_data_home": str(sandbox_home / "xdg-data"),
            }

            responses = iter(
                [
                    ("", "missing", 1, 0),
                    ('"/Users/tester/Library/Keychains/login.keychain-db"\n', "", 0, 0),
                    ("", "partial-write", 1, 0),
                ]
            )

            def fake_call_command(command, timeout_seconds, *, env=None, cwd=None):
                if command[:3] == ["security", "default-keychain", "-d"]:
                    if len(command) == 4:
                        return next(responses)
                    self.assertEqual(
                        command,
                        [
                            "security",
                            "default-keychain",
                            "-d",
                            "user",
                            "-s",
                            "/Users/tester/Library/Keychains/login.keychain-db",
                        ],
                    )
                    return next(responses)
                self.fail(f"unexpected command: {command}")

            with patch.object(common.sys, "platform", "darwin"), patch.dict(
                common.os.environ,
                {"HOME": "/Users/tester"},
                clear=True,
            ), patch.object(common, "call_command", side_effect=fake_call_command):
                env = common.build_bitloops_task_environment(sandbox)

            assert env is not None
            self.assertTrue(
                (sandbox_home / "Library" / "Preferences").is_dir(),
                "sandbox preferences directory should be created for macOS keychain lookups",
            )

    def test_setup_bitloops_uses_per_task_sandbox_without_global_lock(self) -> None:
        sandbox = {
            "mode": "per_task_daemon",
            "sandbox_root": "/tmp/benchkit/sandbox",
            "home_root": "/tmp/benchkit/home",
            "xdg_config_home": "/tmp/benchkit/home/xdg",
            "xdg_state_home": "/tmp/benchkit/home/xdg-state",
            "xdg_cache_home": "/tmp/benchkit/home/xdg-cache",
            "xdg_data_home": "/tmp/benchkit/home/xdg-data",
        }
        handle = SimpleNamespace(
            port=43123,
            process=SimpleNamespace(pid=9981),
            stderr_log_path=Path("/tmp/benchkit/daemon.stderr.log"),
        )
        init_metadata = {
            "bitloops_init_command": ["bitloops", "init", "--agent", "claude-code"],
            "bitloops_install_default_daemon": False,
            "bitloops_init_fallback_used": False,
            "bitloops_init_elapsed_ms": 11,
        }
        call_order: list[str] = []

        def fake_run_bitloops_init(**_kwargs):
            call_order.append("init")
            return init_metadata

        def fake_wait_for_runtime_ready(**kwargs):
            call_order.append("runtime_ready")
            self.assertTrue(kwargs["required"])
            return {
                "bitloops_runtime_ready_wait_attempted": True,
                "bitloops_runtime_ready": True,
                "bitloops_runtime_ready_wait_elapsed_ms": 123,
                "bitloops_runtime_ready_stable_polls": 3,
                "bitloops_runtime_ready_metadata": {"repo_root": "/tmp/workspace"},
            }

        with patch.object(
            common_impl,
            "_ensure_git_branch_for_bitloops_sync",
            return_value=(False, False, None, None, 0),
        ), patch.object(
            common_impl,
            "_run_bitloops_init",
            side_effect=fake_run_bitloops_init,
        ) as mock_init, patch.object(
            common_impl,
            "_seed_bitloops_cloud_inference_profiles",
            return_value=Path("/tmp/benchkit/home/Library/Application Support/bitloops/config.toml"),
        ), patch.object(
            common_impl,
            "_bitloops_authorization_metadata",
            return_value={"bitloops_cloud_authorized": True},
        ), patch.object(
            common_impl,
            "_bitloops_init_environment_with_fresh_platform_token",
            return_value=({"HOME": sandbox["home_root"], "BITLOOPS_PLATFORM_GATEWAY_TOKEN": "token"}, {}),
        ), patch.object(common_impl, "_acquire_bitloops_global_lock") as mock_lock:
            with patch.object(
                common_impl,
                "_wait_for_bitloops_runtime_ready",
                side_effect=fake_wait_for_runtime_ready,
            ) as mock_wait:
                metadata = common_impl.setup_bitloops_for_workspace(
                    agent_name="claude-code",
                    bitloops_bin="bitloops",
                    timeout_seconds=30,
                    sandbox=sandbox,
                    env={"HOME": sandbox["home_root"]},
                    cwd="/tmp/workspace",
                    task_daemon_handle=handle,
                )

        mock_lock.assert_not_called()
        mock_init.assert_called_once()
        mock_wait.assert_called_once()
        self.assertEqual(call_order, ["init", "runtime_ready"])
        self.assertFalse(metadata["bitloops_global_lock_enabled"])
        self.assertFalse(metadata["bitloops_setup_serialized"])
        self.assertTrue(metadata["bitloops_runtime_ready"])
        self.assertTrue(metadata["bitloops_task_daemon_enabled"])
        self.assertEqual(metadata["bitloops_task_daemon_port"], 43123)
        self.assertEqual(metadata["bitloops_task_daemon_pid"], 9981)
        self.assertEqual(
            metadata["bitloops_task_daemon_stderr_log_path"],
            "/tmp/benchkit/daemon.stderr.log",
        )
        self.assertEqual(metadata["bitloops_task_sandbox_mode"], "per_task_daemon")
        self.assertEqual(metadata["bitloops_task_sandbox_root"], sandbox["sandbox_root"])
        self.assertEqual(metadata["bitloops_task_home_root"], sandbox["home_root"])
        self.assertEqual(
            metadata["bitloops_task_xdg_config_home"],
            sandbox["xdg_config_home"],
        )

    def test_setup_bitloops_per_task_sandbox_honors_install_default_daemon_request(self) -> None:
        sandbox = {
            "mode": "per_task_daemon",
            "sandbox_root": "/tmp/benchkit/sandbox",
            "home_root": "/tmp/benchkit/home",
            "xdg_config_home": "/tmp/benchkit/home/xdg",
            "xdg_state_home": "/tmp/benchkit/home/xdg-state",
            "xdg_cache_home": "/tmp/benchkit/home/xdg-cache",
            "xdg_data_home": "/tmp/benchkit/home/xdg-data",
        }
        handle = SimpleNamespace(
            port=43123,
            process=SimpleNamespace(pid=9981),
            stderr_log_path=Path("/tmp/benchkit/daemon.stderr.log"),
        )

        with patch.object(
            common_impl,
            "_ensure_git_branch_for_bitloops_sync",
            return_value=(False, False, None, None, 0),
        ), patch.object(
            common_impl,
            "_run_bitloops_init",
            return_value={
                "bitloops_init_command": ["bitloops", "init", "--agent", "claude-code"],
                "bitloops_install_default_daemon": True,
                "bitloops_init_fallback_used": False,
                "bitloops_init_elapsed_ms": 11,
            },
        ) as mock_init, patch.object(
            common_impl,
            "_wait_for_bitloops_runtime_ready",
            return_value={
                "bitloops_runtime_ready_wait_attempted": True,
                "bitloops_runtime_ready": True,
                "bitloops_runtime_ready_wait_elapsed_ms": 1,
                "bitloops_runtime_ready_stable_polls": 3,
                "bitloops_runtime_ready_metadata": {},
            },
        ), patch.object(
            common_impl,
            "_seed_bitloops_cloud_inference_profiles",
            return_value=Path("/tmp/benchkit/home/Library/Application Support/bitloops/config.toml"),
        ), patch.object(
            common_impl,
            "_bitloops_authorization_metadata",
            return_value={"bitloops_cloud_authorized": True},
        ), patch.object(
            common_impl,
            "_bitloops_init_environment_with_fresh_platform_token",
            return_value=({"HOME": sandbox["home_root"], "BITLOOPS_PLATFORM_GATEWAY_TOKEN": "token"}, {}),
        ):
            common_impl.setup_bitloops_for_workspace(
                agent_name="claude-code",
                bitloops_bin="bitloops",
                timeout_seconds=30,
                install_default_daemon=True,
                embeddings_runtime="platform",
                sandbox=sandbox,
                env={"HOME": sandbox["home_root"]},
                cwd="/tmp/workspace",
                task_daemon_handle=handle,
            )

        self.assertTrue(mock_init.call_args.kwargs["install_default_daemon"])
        self.assertEqual(mock_init.call_args.kwargs["embeddings_runtime"], "platform")

    def test_stop_bitloops_task_daemon_stops_sandbox_daemon_via_cli_when_handle_exited(self) -> None:
        handle = SimpleNamespace(
            process=SimpleNamespace(
                poll=lambda: 0,
                terminate=lambda: None,
                wait=lambda timeout=None: None,
                kill=lambda: None,
            ),
            port=43123,
            stderr_log_path=Path("/tmp/benchkit/daemon.stderr.log"),
            binary="bitloops",
            env={"HOME": "/tmp/benchkit/home"},
            cwd="/tmp/workspace",
        )

        with patch.object(
            common,
            "call_command",
            return_value=("Bitloops daemon stopped.\n", "", 0, 9),
        ) as mock_call:
            common.stop_bitloops_task_daemon(handle)

        mock_call.assert_called_once_with(
            ["bitloops", "daemon", "stop"],
            30,
            env={"HOME": "/tmp/benchkit/home"},
            cwd="/tmp/workspace",
        )

    def test_stop_bitloops_task_daemon_stops_cli_then_terminates_original_process(self) -> None:
        state = {"running": True}
        events: list[tuple[str, int | None] | str] = []

        def poll() -> int | None:
            return None if state["running"] else 0

        def terminate() -> None:
            events.append("terminate")
            state["running"] = False

        def wait(timeout: int | None = None) -> None:
            events.append(("wait", timeout))

        def kill() -> None:
            events.append("kill")
            state["running"] = False

        handle = SimpleNamespace(
            process=SimpleNamespace(
                poll=poll,
                terminate=terminate,
                wait=wait,
                kill=kill,
            ),
            port=43123,
            stderr_log_path=Path("/tmp/benchkit/daemon.stderr.log"),
            binary="bitloops",
            env={"HOME": "/tmp/benchkit/home"},
            cwd="/tmp/workspace",
        )

        with patch.object(
            common,
            "call_command",
            return_value=("Bitloops daemon stopped.\n", "", 0, 11),
        ) as mock_call:
            common.stop_bitloops_task_daemon(handle)

        mock_call.assert_called_once_with(
            ["bitloops", "daemon", "stop"],
            30,
            env={"HOME": "/tmp/benchkit/home"},
            cwd="/tmp/workspace",
        )
        self.assertEqual(events, ["terminate", ("wait", 5)])

    def test_run_bitloops_init_uses_runtime_ready_shortcut_for_per_task_sync(self) -> None:
        shortcut_metadata = {
            "repo_root": "/tmp/workspace",
            "sync_status": "completed",
            "follow_up_sync_satisfied": True,
            "embeddings_gate_readiness": "ready",
        }
        with patch.object(
            common_impl,
            "_run_command_with_runtime_ready_shortcut",
            return_value=("", "", 0, 19, shortcut_metadata),
        ) as mock_shortcut, patch.object(
            common_impl,
            "_run_command_with_init_status_shortcut",
            side_effect=AssertionError("init status shortcut should not be used for sync"),
        ) as mock_status_shortcut:
            metadata = common_impl._run_bitloops_init(
                binary="bitloops",
                timeout=30,
                agent_name="claude-code",
                sync=True,
                ingest=False,
                install_default_daemon=False,
                embeddings_runtime=None,
                no_embeddings=False,
                disable_devql_guidance=True,
                env={
                    "BITLOOPS_BENCHKIT_SANDBOX_MODE": "per_task_daemon",
                },
                cwd="/tmp/workspace",
            )

        mock_shortcut.assert_called_once()
        self.assertIn(
            "--disable-devql-guidance",
            mock_shortcut.call_args.kwargs["command"],
        )
        mock_status_shortcut.assert_not_called()
        self.assertFalse(metadata["bitloops_init_status_shortcut_used"])
        self.assertIsNone(metadata["bitloops_init_status_command"])
        self.assertTrue(metadata["bitloops_init_runtime_ready_shortcut_used"])
        self.assertTrue(metadata["bitloops_disable_devql_guidance"])
        ready = metadata["bitloops_init_runtime_ready_shortcut"]
        assert isinstance(ready, dict)
        self.assertEqual(ready["sync_status"], "completed")
        self.assertEqual(ready["embeddings_gate_readiness"], "ready")

    def test_run_command_with_init_status_shortcut_returns_when_session_completes(self) -> None:
        class _HangingProcess:
            def __init__(self) -> None:
                self.returncode = None
                self.terminated = False

            def poll(self) -> int | None:
                return None if not self.terminated else 0

            def terminate(self) -> None:
                self.terminated = True
                self.returncode = 0

            def kill(self) -> None:
                self.terminated = True
                self.returncode = -9

            def communicate(self, timeout: float | None = None) -> tuple[str, str]:
                _ = timeout
                return "", ""

        status_payloads = [
            {
                "repoId": "repo-1",
                "requestedSessionId": None,
                "currentInitSessionId": None,
                "session": None,
            },
            {
                "repoId": "repo-1",
                "requestedSessionId": None,
                "currentInitSessionId": "init-session-1",
                "session": {
                    "initSessionId": "init-session-1",
                    "status": "completed",
                    "statusLabel": "Completed",
                    "followUpSyncRequired": False,
                    "summaryText": "Setup tasks completed",
                    "terminalError": None,
                    "lanes": [
                        {
                            "title": "Code Embeddings",
                            "label": "code_embeddings",
                            "status": "completed",
                            "statusLabel": "Completed",
                            "summaryText": "Code embeddings complete",
                            "queue": {"queued": 0, "running": 0, "failed": 0},
                            "warnings": [],
                        }
                    ],
                },
            },
        ]
        status_calls = [
            (json.dumps(payload), "", 0, 4)
            for payload in status_payloads
        ]
        process = _HangingProcess()

        with patch.object(common_impl.subprocess, "Popen", return_value=process), patch.object(
            common_impl,
            "call_command",
            side_effect=status_calls,
        ) as mock_call, patch.object(common_impl.time, "sleep") as mock_sleep:
            stdout, stderr, return_code, elapsed_ms, metadata = (
                common_impl._run_command_with_init_status_shortcut(
                    command=["bitloops", "init", "--agent", "claude-code"],
                    timeout_seconds=30,
                    env={"BITLOOPS_BENCHKIT_SANDBOX_MODE": "per_task_daemon"},
                    cwd="/tmp/workspace",
                )
            )

        self.assertEqual(stdout, "")
        self.assertEqual(stderr, "")
        self.assertEqual(return_code, 0)
        self.assertGreaterEqual(elapsed_ms, 0)
        self.assertTrue(process.terminated)
        assert isinstance(metadata, dict)
        self.assertEqual(metadata["current_init_session_id"], "init-session-1")
        session = metadata["session"]
        assert isinstance(session, dict)
        self.assertEqual(session["status"], "completed")
        self.assertEqual(session["statusLabel"], "Completed")
        self.assertEqual(session["summaryText"], "Setup tasks completed")
        first_call = mock_call.call_args_list[0].args[0]
        self.assertEqual(first_call, ["bitloops", "init", "status", "--json"])
        self.assertEqual(mock_sleep.call_count, 1)

    def test_run_command_with_init_status_shortcut_tolerates_status_poll_timeout(self) -> None:
        class _HangingProcess:
            def __init__(self) -> None:
                self.returncode = None
                self.terminated = False

            def poll(self) -> int | None:
                return None if not self.terminated else 0

            def terminate(self) -> None:
                self.terminated = True
                self.returncode = 0

            def kill(self) -> None:
                self.terminated = True
                self.returncode = -9

            def communicate(self, timeout: float | None = None) -> tuple[str, str]:
                _ = timeout
                return "", ""

        status_payload = {
            "repoId": "repo-1",
            "requestedSessionId": None,
            "currentInitSessionId": "init-session-1",
            "session": {
                "initSessionId": "init-session-1",
                "status": "completed",
                "statusLabel": "Completed",
                "followUpSyncRequired": False,
                "summaryText": "Setup tasks completed",
                "terminalError": None,
                "lanes": [],
            },
        }
        process = _HangingProcess()
        status_timeout = common_impl.subprocess.TimeoutExpired(
            cmd=["bitloops", "init", "status", "--json"],
            timeout=10,
        )

        with patch.object(common_impl.subprocess, "Popen", return_value=process), patch.object(
            common_impl,
            "call_command",
            side_effect=[
                status_timeout,
                (json.dumps(status_payload), "", 0, 4),
            ],
        ) as mock_call, patch.object(common_impl.time, "sleep") as mock_sleep:
            stdout, stderr, return_code, elapsed_ms, metadata = (
                common_impl._run_command_with_init_status_shortcut(
                    command=["bitloops", "init", "--agent", "opencode"],
                    timeout_seconds=30,
                    env={"BITLOOPS_BENCHKIT_SANDBOX_MODE": "per_task_daemon"},
                    cwd="/tmp/workspace",
                )
            )

        self.assertEqual(stdout, "")
        self.assertEqual(stderr, "")
        self.assertEqual(return_code, 0)
        self.assertGreaterEqual(elapsed_ms, 0)
        self.assertTrue(process.terminated)
        assert isinstance(metadata, dict)
        self.assertEqual(metadata["current_init_session_id"], "init-session-1")
        session = metadata["session"]
        assert isinstance(session, dict)
        self.assertEqual(session["status"], "completed")
        self.assertEqual(mock_call.call_count, 2)
        self.assertEqual(mock_sleep.call_count, 1)

    def test_run_command_with_init_status_shortcut_tolerates_invalid_utf8_output(self) -> None:
        status_payload = {
            "repoId": "repo-1",
            "requestedSessionId": None,
            "currentInitSessionId": "init-session-1",
            "session": {
                "initSessionId": "init-session-1",
                "status": "completed",
                "statusLabel": "Completed",
                "followUpSyncRequired": False,
                "summaryText": "Setup tasks completed",
                "terminalError": None,
                "lanes": [],
            },
        }

        def _status_call(*_args, **_kwargs):
            common_impl.time.sleep(0.1)
            return json.dumps(status_payload), "", 0, 4

        with patch.object(
            common_impl,
            "call_command",
            side_effect=_status_call,
        ):
            stdout, stderr, return_code, elapsed_ms, metadata = (
                common_impl._run_command_with_init_status_shortcut(
                    command=[
                        common_impl.sys.executable,
                        "-c",
                        (
                            "import sys, time; "
                            "sys.stdout.buffer.write(b'\\xe2'); "
                            "sys.stdout.flush(); "
                            "time.sleep(30)"
                        ),
                    ],
                    timeout_seconds=30,
                    env=None,
                    cwd=None,
                )
            )

        self.assertEqual(stdout, "\ufffd")
        self.assertEqual(stderr, "")
        self.assertEqual(return_code, 0)
        self.assertGreaterEqual(elapsed_ms, 0)
        self.assertIsInstance(metadata, dict)

    def test_run_bitloops_init_uses_init_status_shortcut_even_when_sync_is_false(self) -> None:
        shortcut_metadata = {
            "current_init_session_id": "init-session-1",
            "session": {
                "initSessionId": "init-session-1",
                "status": "completed",
                "statusLabel": "Completed",
                "followUpSyncRequired": False,
                "summaryText": "Setup tasks completed",
                "terminalError": None,
                "lanes": [],
            },
        }
        with patch.object(
            common_impl,
            "_run_command_with_init_status_shortcut",
            return_value=("", "", 0, 17, shortcut_metadata),
            create=True,
        ) as mock_shortcut, patch.object(
            common_impl,
            "call_command",
            side_effect=AssertionError("plain init execution should not be used"),
        ):
            metadata = common_impl._run_bitloops_init(
                binary="bitloops",
                timeout=30,
                agent_name="claude-code",
                sync=False,
                ingest=False,
                install_default_daemon=False,
                embeddings_runtime=None,
                no_embeddings=False,
                env={"BITLOOPS_BENCHKIT_SANDBOX_MODE": "per_task_daemon"},
                cwd="/tmp/workspace",
            )

        mock_shortcut.assert_called_once()
        self.assertTrue(metadata["bitloops_init_status_shortcut_used"])
        self.assertEqual(metadata["bitloops_init_elapsed_ms"], 17)
        self.assertEqual(
            metadata["bitloops_init_command"],
            [
                "bitloops",
                "init",
                "--agent",
                "claude-code",
                "--telemetry=false",
                "--sync=false",
                "--ingest=false",
            ],
        )
        session = metadata["bitloops_init_status_shortcut"]["session"]
        self.assertEqual(session["status"], "completed")

    def test_debug_log_does_not_post_without_opt_in(self) -> None:
        with patch.object(common, "DEBUG_LOG_PATH", Path("/dev/null/debug.log")), patch.object(
            common.urllib.request,
            "urlopen",
        ) as mock_urlopen:
            common._debug_log(
                hypothesis_id="H-test",
                location="tests",
                message="no http fallback by default",
                data={"k": "v"},
            )
        mock_urlopen.assert_not_called()

    def test_bitloops_runtime_ready_waits_for_embeddings_gate(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            home = root / "home"
            state_root = home / ".local" / "state" / "bitloops" / "daemon"
            state_root.mkdir(parents=True)
            runtime_db = state_root / "runtime.sqlite"
            workspace = root / "workspace"
            workspace.mkdir()

            connection = sqlite3.connect(runtime_db)
            try:
                connection.execute(
                    "CREATE TABLE runtime_documents (document_kind TEXT PRIMARY KEY, payload TEXT NOT NULL)"
                )
                queue_payload = {
                    "version": 1,
                    "tasks": [
                        {
                            "task_id": "sync-task-1",
                            "repo_root": str(workspace),
                            "source": "init",
                            "kind": "sync",
                            "status": "completed",
                            "submitted_at_unix": 100,
                            "init_session_id": "init-session-1",
                            "progress": {
                                "type": "sync",
                                "value": {
                                    "phase": "complete",
                                    "pathsTotal": 10,
                                    "pathsCompleted": 10,
                                    "pathsRemaining": 0,
                                    "parseErrors": 0,
                                },
                            },
                            "result": {
                                "type": "sync",
                                "value": {"success": True},
                            },
                        }
                    ],
                }
                init_payload = {
                    "version": 1,
                    "sessions": [
                        {
                            "init_session_id": "init-session-1",
                            "repo_root": str(workspace),
                            "daemon_config_root": str(workspace / ".bitloops"),
                            "embeddings_bootstrap_task_id": "bootstrap-task-1",
                            "follow_up_sync_required": False,
                            "initial_sync_completion_seq": 1,
                            "submitted_at_unix": 100,
                            "selections": {
                                "run_sync": True,
                                "embeddings_bootstrap": {
                                    "config_path": str(workspace / ".bitloops" / "config.toml"),
                                    "profile_name": "platform_default",
                                    "mode": "platform",
                                },
                            },
                        }
                    ],
                }
                enrichment_payload = {
                    "version": 1,
                    "jobs": [],
                    "last_action": "completed",
                }
                embeddings_payload = {
                    "version": 1,
                    "entries": {
                        str((workspace / ".bitloops" / "config.toml").resolve()): {
                            "config_path": str((workspace / ".bitloops" / "config.toml").resolve()),
                            "profile_name": "platform_default",
                            "readiness": "pending",
                            "active_task_id": "bootstrap-task-1",
                            "last_error": None,
                            "last_updated_unix": 123,
                        }
                    },
                    "last_action": "embeddings_bootstrap_gate_updated",
                    "updated_at_unix": 123,
                }
                for kind, payload in (
                    ("devql_task_queue_state", queue_payload),
                    ("init_session_state", init_payload),
                    ("enrichment_queue_state", enrichment_payload),
                    ("embeddings_bootstrap_state", embeddings_payload),
                ):
                    connection.execute(
                        "INSERT INTO runtime_documents(document_kind, payload) VALUES (?, ?)",
                        (kind, json.dumps(payload)),
                    )
                connection.commit()
            finally:
                connection.close()

            ready, metadata = common_impl._bitloops_init_ready_via_runtime_state(
                runtime_db_path=runtime_db,
                repo_root=str(workspace),
        )

        self.assertFalse(ready)
        assert isinstance(metadata, dict)
        self.assertEqual(metadata["embeddings_gate_readiness"], "pending")
        self.assertTrue(metadata["embeddings_gate_blocked"])
        self.assertEqual(metadata["embeddings_gate_active_task_id"], "bootstrap-task-1")

    def test_bitloops_runtime_ready_allows_ready_embeddings_gate(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            home = root / "home"
            state_root = home / ".local" / "state" / "bitloops" / "daemon"
            state_root.mkdir(parents=True)
            runtime_db = state_root / "runtime.sqlite"
            workspace = root / "workspace"
            workspace.mkdir()
            daemon_config_root = workspace / ".bitloops"
            daemon_config_root.mkdir()
            config_path = (daemon_config_root / "config.toml").resolve()

            connection = sqlite3.connect(runtime_db)
            try:
                connection.execute(
                    "CREATE TABLE runtime_documents (document_kind TEXT PRIMARY KEY, payload TEXT NOT NULL)"
                )
                queue_payload = {
                    "version": 1,
                    "tasks": [
                        {
                            "task_id": "sync-task-1",
                            "repo_root": str(workspace),
                            "source": "init",
                            "kind": "sync",
                            "status": "completed",
                            "submitted_at_unix": 100,
                            "init_session_id": "init-session-1",
                            "progress": {
                                "type": "sync",
                                "value": {
                                    "phase": "complete",
                                    "pathsTotal": 10,
                                    "pathsCompleted": 10,
                                    "pathsRemaining": 0,
                                    "parseErrors": 0,
                                },
                            },
                            "result": {
                                "type": "sync",
                                "value": {"success": True},
                            },
                        }
                    ],
                }
                init_payload = {
                    "version": 1,
                    "sessions": [
                        {
                            "init_session_id": "init-session-1",
                            "repo_root": str(workspace),
                            "daemon_config_root": str(daemon_config_root),
                            "embeddings_bootstrap_task_id": "bootstrap-task-1",
                            "embeddings_bootstrap_completion_seq": 2,
                            "follow_up_sync_required": False,
                            "initial_sync_completion_seq": 1,
                            "submitted_at_unix": 100,
                            "selections": {
                                "run_sync": True,
                                "embeddings_bootstrap": {
                                    "config_path": str(config_path),
                                    "profile_name": "platform_default",
                                    "mode": "platform",
                                },
                            },
                        }
                    ],
                }
                enrichment_payload = {
                    "version": 1,
                    "jobs": [],
                    "last_action": "completed",
                }
                embeddings_payload = {
                    "version": 1,
                    "entries": {
                        str(config_path): {
                            "config_path": str(config_path),
                            "profile_name": "platform_default",
                            "readiness": "ready",
                            "active_task_id": None,
                            "last_error": None,
                            "last_updated_unix": 124,
                        }
                    },
                    "last_action": "embeddings_bootstrap_gate_updated",
                    "updated_at_unix": 124,
                }
                for kind, payload in (
                    ("devql_task_queue_state", queue_payload),
                    ("init_session_state", init_payload),
                    ("enrichment_queue_state", enrichment_payload),
                    ("embeddings_bootstrap_state", embeddings_payload),
                ):
                    connection.execute(
                        "INSERT INTO runtime_documents(document_kind, payload) VALUES (?, ?)",
                        (kind, json.dumps(payload)),
                    )
                connection.commit()
            finally:
                connection.close()

            with patch.object(
                common_impl,
                "_active_bitloops_summary_inference_commands",
                return_value=["bitloops-inference run --profile summary_llm"],
            ):
                blocked_ready, blocked_metadata = (
                    common_impl._bitloops_init_ready_via_runtime_state(
                        runtime_db_path=runtime_db,
                        repo_root=str(workspace),
                    )
                )
            ready, metadata = common_impl._bitloops_init_ready_via_runtime_state(
                runtime_db_path=runtime_db,
                repo_root=str(workspace),
        )

        self.assertFalse(blocked_ready)
        assert isinstance(blocked_metadata, dict)
        self.assertEqual(blocked_metadata["active_summary_inference_processes"], 1)
        self.assertTrue(ready)
        assert isinstance(metadata, dict)
        self.assertTrue(metadata["embeddings_bootstrap_requested"])
        self.assertEqual(metadata["embeddings_gate_readiness"], "ready")
        self.assertFalse(metadata["embeddings_gate_blocked"])
        self.assertEqual(metadata["active_summary_inference_processes"], 0)

    def test_bitloops_runtime_ready_requires_materialized_summaries(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            runtime_db = root / "runtime.sqlite"
            workspace = root / "workspace"
            workspace.mkdir()
            daemon_config_root = workspace / ".bitloops"
            relational_root = daemon_config_root / "stores" / "relational"
            relational_root.mkdir(parents=True)
            relational_db = relational_root / "relational.db"
            relational_connection = sqlite3.connect(relational_db)
            try:
                relational_connection.execute(
                    "CREATE TABLE symbol_semantics_current (artefact_id TEXT)"
                )
                relational_connection.execute(
                    "CREATE TABLE symbol_embeddings_current (representation_kind TEXT)"
                )
                relational_connection.commit()
            finally:
                relational_connection.close()

            connection = sqlite3.connect(runtime_db)
            try:
                connection.execute(
                    "CREATE TABLE runtime_documents (document_kind TEXT PRIMARY KEY, payload TEXT NOT NULL)"
                )
                queue_payload = {
                    "version": 1,
                    "tasks": [
                        {
                            "task_id": "sync-task-1",
                            "repo_root": str(workspace),
                            "source": "init",
                            "kind": "sync",
                            "status": "completed",
                            "submitted_at_unix": 100,
                            "init_session_id": "init-session-1",
                            "progress": {
                                "type": "sync",
                                "value": {"phase": "complete"},
                            },
                            "result": {
                                "type": "sync",
                                "value": {"success": True},
                            },
                        },
                        {
                            "task_id": "summary-task-1",
                            "repo_root": str(workspace),
                            "source": "init",
                            "kind": "summary_bootstrap",
                            "status": "completed",
                            "submitted_at_unix": 101,
                            "init_session_id": "init-session-1",
                        },
                    ],
                }
                init_payload = {
                    "version": 1,
                    "sessions": [
                        {
                            "init_session_id": "init-session-1",
                            "repo_root": str(workspace),
                            "daemon_config_root": str(daemon_config_root),
                            "follow_up_sync_required": False,
                            "initial_sync_completion_seq": 1,
                            "submitted_at_unix": 100,
                            "selections": {
                                "run_sync": True,
                                "run_summaries": True,
                                "run_summary_embeddings": True,
                            },
                        }
                    ],
                }
                for kind, payload in (
                    ("devql_task_queue_state", queue_payload),
                    ("init_session_state", init_payload),
                    ("enrichment_queue_state", {"version": 1, "jobs": []}),
                ):
                    connection.execute(
                        "INSERT INTO runtime_documents(document_kind, payload) VALUES (?, ?)",
                        (kind, json.dumps(payload)),
                    )
                connection.commit()
            finally:
                connection.close()

            ready, metadata = common_impl._bitloops_init_ready_via_runtime_state(
                runtime_db_path=runtime_db,
                repo_root=str(workspace),
            )

        self.assertFalse(ready)
        assert isinstance(metadata, dict)
        self.assertTrue(metadata["summary_materialization_required"])
        self.assertTrue(metadata["summary_embeddings_required"])
        self.assertEqual(metadata["summary_rows"], 0)
        self.assertEqual(metadata["summary_embedding_rows"], 0)
        self.assertFalse(metadata["summary_materialization_ready"])
        self.assertFalse(metadata["summary_embeddings_ready"])

    def test_bitloops_runtime_ready_accepts_materialized_summaries_without_summary_task(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            runtime_db = root / "runtime.sqlite"
            workspace = root / "workspace"
            workspace.mkdir()
            daemon_config_root = workspace / ".bitloops"
            relational_root = daemon_config_root / "stores" / "relational"
            relational_root.mkdir(parents=True)
            relational_db = relational_root / "relational.db"
            relational_connection = sqlite3.connect(relational_db)
            try:
                relational_connection.execute(
                    "CREATE TABLE symbol_semantics_current (artefact_id TEXT)"
                )
                relational_connection.execute(
                    "CREATE TABLE symbol_embeddings_current (artefact_id TEXT, representation_kind TEXT)"
                )
                relational_connection.executemany(
                    "INSERT INTO symbol_semantics_current(artefact_id) VALUES (?)",
                    [("symbol-1",), ("symbol-2",)],
                )
                relational_connection.executemany(
                    "INSERT INTO symbol_embeddings_current(artefact_id, representation_kind) VALUES (?, ?)",
                    [("symbol-1", "summary"), ("symbol-2", "summary")],
                )
                relational_connection.commit()
            finally:
                relational_connection.close()

            connection = sqlite3.connect(runtime_db)
            try:
                connection.execute(
                    "CREATE TABLE runtime_documents (document_kind TEXT PRIMARY KEY, payload TEXT NOT NULL)"
                )
                queue_payload = {
                    "version": 1,
                    "tasks": [
                        {
                            "task_id": "sync-task-1",
                            "repo_root": str(workspace),
                            "source": "init",
                            "kind": "sync",
                            "status": "completed",
                            "submitted_at_unix": 100,
                            "init_session_id": "init-session-1",
                            "progress": {
                                "type": "sync",
                                "value": {"phase": "complete"},
                            },
                            "result": {
                                "type": "sync",
                                "value": {"success": True},
                            },
                        },
                    ],
                }
                init_payload = {
                    "version": 1,
                    "sessions": [
                        {
                            "init_session_id": "init-session-1",
                            "repo_root": str(workspace),
                            "daemon_config_root": str(daemon_config_root),
                            "follow_up_sync_required": False,
                            "initial_sync_completion_seq": 1,
                            "submitted_at_unix": 100,
                            "selections": {
                                "run_sync": True,
                                "run_summaries": True,
                                "run_summary_embeddings": True,
                            },
                        }
                    ],
                }
                for kind, payload in (
                    ("devql_task_queue_state", queue_payload),
                    ("init_session_state", init_payload),
                    ("enrichment_queue_state", {"version": 1, "jobs": []}),
                ):
                    connection.execute(
                        "INSERT INTO runtime_documents(document_kind, payload) VALUES (?, ?)",
                        (kind, json.dumps(payload)),
                    )
                connection.commit()
            finally:
                connection.close()

            ready, metadata = common_impl._bitloops_init_ready_via_runtime_state(
                runtime_db_path=runtime_db,
                repo_root=str(workspace),
            )

        self.assertTrue(ready)
        assert isinstance(metadata, dict)
        self.assertEqual(metadata["init_queue_missing_kinds"], [])
        self.assertTrue(metadata["summary_materialization_ready"])
        self.assertTrue(metadata["summary_embeddings_ready"])
        self.assertEqual(metadata["summary_rows"], 2)
        self.assertEqual(metadata["summary_embedding_rows"], 2)

    def test_summary_materialization_requires_embedding_coverage(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            daemon_config_root = root / ".bitloops"
            relational_root = daemon_config_root / "stores" / "relational"
            relational_root.mkdir(parents=True)
            relational_db = relational_root / "relational.db"
            connection = sqlite3.connect(relational_db)
            try:
                connection.execute(
                    "CREATE TABLE symbol_semantics_current (artefact_id TEXT)"
                )
                connection.execute(
                    "CREATE TABLE symbol_embeddings_current (artefact_id TEXT, representation_kind TEXT)"
                )
                connection.executemany(
                    "INSERT INTO symbol_semantics_current(artefact_id) VALUES (?)",
                    [("symbol-1",), ("symbol-2",)],
                )
                connection.execute(
                    "INSERT INTO symbol_embeddings_current(artefact_id, representation_kind) VALUES (?, ?)",
                    ("symbol-1", "summary"),
                )
                connection.commit()
            finally:
                connection.close()

            ready, metadata = common_impl._summary_materialization_ready_for_session(
                {
                    "daemon_config_root": str(daemon_config_root),
                    "selections": {
                        "run_summaries": True,
                        "run_summary_embeddings": True,
                    },
                }
            )

        self.assertFalse(ready)
        self.assertEqual(metadata["summary_rows"], 2)
        self.assertEqual(metadata["summary_embedding_rows"], 1)
        self.assertEqual(metadata["summary_embedding_rows_missing"], 1)
        self.assertFalse(metadata["summary_embedding_coverage_complete"])
        self.assertFalse(metadata["summary_embeddings_ready"])

    def test_wait_for_bitloops_runtime_ready_requires_stable_ready_polls(self) -> None:
        with patch.dict(
            common_impl.os.environ,
            {
                "BITLOOPS_RUNTIME_READY_STABLE_POLLS": "2",
                "BITLOOPS_RUNTIME_READY_POLL_INTERVAL_SECONDS": "0.1",
            },
            clear=False,
        ), patch.object(
            common_impl,
            "_resolve_bitloops_runtime_db_path",
            return_value=Path("/tmp/runtime.sqlite"),
        ), patch.object(
            common_impl,
            "_bitloops_init_ready_via_runtime_state",
            side_effect=[
                (False, None),
                (True, {"ready_seq": 1}),
                (True, {"ready_seq": 2}),
            ],
        ) as mock_ready, patch.object(common_impl.time, "sleep") as mock_sleep:
            metadata = common_impl._wait_for_bitloops_runtime_ready(
                timeout_seconds=30,
                env={"HOME": "/tmp/home"},
                cwd="/tmp/workspace",
                required=True,
            )

        self.assertEqual(mock_ready.call_count, 3)
        self.assertEqual(mock_sleep.call_count, 2)
        self.assertTrue(metadata["bitloops_runtime_ready"])
        self.assertEqual(metadata["bitloops_runtime_ready_stable_polls"], 2)
        self.assertEqual(
            metadata["bitloops_runtime_ready_metadata"],
            {"ready_seq": 2},
        )

    def test_follow_up_sync_satisfied_by_completed_watcher_sync(self) -> None:
        satisfied, task = common_impl._follow_up_sync_satisfied(
            latest_session={
                "follow_up_sync_required": True,
                "submitted_at_unix": 100,
            },
            repo_tasks=[
                {
                    "task_id": "sync-task-init",
                    "source": "init",
                    "kind": "sync",
                    "submitted_at_unix": 100,
                    "status": "completed",
                    "progress": {
                        "type": "sync",
                        "value": {"phase": "complete"},
                    },
                    "result": {
                        "type": "sync",
                        "value": {"success": True},
                    },
                },
                {
                    "task_id": "sync-task-watcher",
                    "source": "watcher",
                    "kind": "sync",
                    "submitted_at_unix": 101,
                    "status": "completed",
                    "progress": {
                        "type": "sync",
                        "value": {"phase": "complete"},
                    },
                    "result": {
                        "type": "sync",
                        "value": {"success": True},
                    },
                },
            ],
        )

        self.assertTrue(satisfied)
        assert isinstance(task, dict)
        self.assertEqual(task["task_id"], "sync-task-watcher")

    def test_init_devql_tasks_ready_blocks_failed_ingest(self) -> None:
        ready, metadata = common_impl._init_devql_tasks_ready(
            latest_session={
                "init_session_id": "init-session-1",
                "selections": {"run_ingest": True},
            },
            repo_tasks=[
                {
                    "task_id": "sync-task-1",
                    "source": "init",
                    "kind": "sync",
                    "status": "completed",
                    "init_session_id": "init-session-1",
                },
                {
                    "task_id": "ingest-task-1",
                    "source": "init",
                    "kind": "ingest",
                    "status": "failed",
                    "init_session_id": "init-session-1",
                    "error": "joining SQLite query task: task was cancelled",
                },
            ],
        )

        self.assertFalse(ready)
        self.assertEqual(metadata["init_queue_missing_kinds"], ["ingest"])
        self.assertEqual(
            metadata["init_queue_incomplete_tasks"],
            [
                {
                    "task_id": "ingest-task-1",
                    "kind": "ingest",
                    "status": "failed",
                    "error": "joining SQLite query task: task was cancelled",
                }
            ],
        )
        self.assertEqual(metadata["init_queue_failed_tasks"], metadata["init_queue_incomplete_tasks"])

    def test_wait_for_bitloops_runtime_ready_fails_fast_on_failed_init_task(self) -> None:
        with patch.object(
            common_impl,
            "_resolve_bitloops_runtime_db_path",
            return_value=Path("/tmp/runtime.sqlite"),
        ), patch.object(
            common_impl,
            "_bitloops_init_ready_via_runtime_state",
            return_value=(
                False,
                {
                    "repo_root": "/tmp/workspace",
                    "init_queue_failed_tasks": [
                        {
                            "task_id": "ingest-task-1",
                            "kind": "ingest",
                            "status": "failed",
                            "error": "joining SQLite query task: task was cancelled",
                        }
                    ],
                },
            ),
        ), patch.object(common_impl.time, "sleep") as mock_sleep:
            with self.assertRaisesRegex(RuntimeError, "Bitloops init task failed"):
                common_impl._wait_for_bitloops_runtime_ready(
                    timeout_seconds=30,
                    env={"HOME": "/tmp/home"},
                    cwd="/tmp/workspace",
                    required=True,
                )

        mock_sleep.assert_not_called()

    def test_debug_log_posts_when_http_fallback_opted_in(self) -> None:
        with patch.dict(common.os.environ, {"BENCHKIT_DEBUG_HTTP_FALLBACK": "1"}), patch.object(
            common,
            "DEBUG_LOG_PATH",
            Path("/dev/null/debug.log"),
        ), patch.object(
            common.urllib.request,
            "urlopen",
            return_value=contextlib.nullcontext(),
        ) as mock_urlopen:
            common._debug_log(
                hypothesis_id="H-test",
                location="tests",
                message="http fallback opted in",
                data={"k": "v"},
            )
        mock_urlopen.assert_called_once()

    def test_run_agent_wrapper_returns_workspace_patch_and_stops_daemon(self) -> None:
        workspace = Path("/tmp/bench-workspace")
        args = SimpleNamespace(
            bitloops_init=True,
            bitloops_sync="true",
            bitloops_ingest="true",
            bitloops_embeddings_runtime=None,
            bitloops_no_embeddings=False,
            bitloops_no_summaries=False,
            bitloops_summary_mode=None,
            bitloops_embedding_mode=None,
        )
        daemon_handle = object()
        captured_call: dict[str, object] = {}

        def fake_runner(*, timeout_seconds: int, env: dict[str, str] | None, cwd: str) -> object:
            captured_call["timeout_seconds"] = timeout_seconds
            captured_call["env"] = env
            captured_call["cwd"] = cwd
            return common.AgentCommandResult(
                command=["agent", "--json"],
                stdout="ignored",
                stderr="",
                return_code=0,
                elapsed_ms=321,
            )

        with (
            patch.object(common, "resolve_workspace", return_value=workspace),
            patch.object(common, "resolve_bitloops_sandbox", return_value={"mode": "per_task_daemon"}),
            patch.object(common, "build_bitloops_task_environment", return_value={"BITLOOPS": "1"}),
            patch.object(common, "reset_workspace"),
            patch.object(common, "start_bitloops_task_daemon", return_value=daemon_handle),
            patch.object(common, "setup_bitloops_for_workspace", return_value={"bitloops_ready": True}),
            patch.object(common, "capture_workspace_patch", return_value="diff --git a/x b/x"),
            patch.object(common, "stop_bitloops_task_daemon") as mock_stop,
        ):
            result = common.run_agent_wrapper(
                payload={"run": {"workspace_root": str(workspace)}},
                args=args,
                agent_name="codex",
                bitloops_setup_timeout_seconds=1500,
                failure_message="agent command failed and no workspace changes were made",
                command_runner=fake_runner,
            )

        self.assertEqual(result.workspace, workspace)
        self.assertEqual(result.patch, "diff --git a/x b/x\n")
        self.assertEqual(result.patch_source, "workspace_git_diff")
        self.assertEqual(result.bitloops_metadata, {"bitloops_ready": True})
        self.assertEqual(result.command_env, {"BITLOOPS": "1"})
        self.assertEqual(captured_call["timeout_seconds"], 1500)
        self.assertEqual(captured_call["env"], {"BITLOOPS": "1"})
        self.assertEqual(captured_call["cwd"], str(workspace))
        mock_stop.assert_called_once_with(daemon_handle)

    def test_run_agent_wrapper_falls_back_to_stdout_patch_when_workspace_clean(self) -> None:
        workspace = Path("/tmp/bench-workspace")
        args = SimpleNamespace(bitloops_init=False)
        stdout = '{"type":"result","result":"diff --git a/x b/x\\n--- a/x\\n+++ b/x\\n@@ -1 +1 @@\\n-a\\n+b\\n"}'

        def fake_runner(*, timeout_seconds: int, env: dict[str, str] | None, cwd: str) -> object:
            return common.AgentCommandResult(
                command=["agent", "--json"],
                stdout=stdout,
                stderr="",
                return_code=0,
                elapsed_ms=111,
            )

        with (
            patch.object(common, "resolve_workspace", return_value=workspace),
            patch.object(common, "resolve_bitloops_sandbox", return_value=None),
            patch.object(common, "build_bitloops_task_environment", return_value=None),
            patch.object(common, "reset_workspace"),
            patch.object(common, "capture_workspace_patch", return_value=""),
            patch.object(common, "stop_bitloops_task_daemon") as mock_stop,
        ):
            result = common.run_agent_wrapper(
                payload={"run": {"workspace_root": str(workspace)}},
                args=args,
                agent_name="cursor",
                bitloops_setup_timeout_seconds=1500,
                failure_message="agent command failed and no workspace changes were made",
                command_runner=fake_runner,
            )

        self.assertTrue(result.patch.startswith("diff --git"))
        self.assertEqual(result.patch_source, "diff_header")
        mock_stop.assert_called_once_with(None)

    def test_run_agent_wrapper_fatal_on_failed_command_without_patch(self) -> None:
        workspace = Path("/tmp/bench-workspace")
        args = SimpleNamespace(bitloops_init=False)

        def fake_runner(*, timeout_seconds: int, env: dict[str, str] | None, cwd: str) -> object:
            return common.AgentCommandResult(
                command=["agent", "--json"],
                stdout='{"type":"result","result":"failure"}',
                stderr="boom",
                return_code=7,
                elapsed_ms=100,
            )

        with (
            patch.object(common, "resolve_workspace", return_value=workspace),
            patch.object(common, "resolve_bitloops_sandbox", return_value=None),
            patch.object(common, "build_bitloops_task_environment", return_value=None),
            patch.object(common, "reset_workspace"),
            patch.object(common, "capture_workspace_patch", return_value=""),
            patch.object(common, "stop_bitloops_task_daemon"),
            patch.object(common, "fatal_error", side_effect=RuntimeError("fatal")) as mock_fatal,
        ):
            with self.assertRaises(RuntimeError):
                common.run_agent_wrapper(
                    payload={"run": {"workspace_root": str(workspace)}},
                    args=args,
                    agent_name="claude-code",
                    bitloops_setup_timeout_seconds=1500,
                    failure_message="agent command failed and no workspace changes were made",
                    command_runner=fake_runner,
                    failure_details={"raw_stdout_path": "/tmp/out.jsonl"},
                )

        mock_fatal.assert_called_once()
        args_out, kwargs_out = mock_fatal.call_args
        self.assertEqual(args_out[0], "agent command failed and no workspace changes were made")
        self.assertEqual(kwargs_out["details"]["return_code"], 7)
        self.assertEqual(kwargs_out["details"]["command"], ["agent", "--json"])
        self.assertEqual(kwargs_out["details"]["raw_stdout_path"], "/tmp/out.jsonl")

    def test_run_agent_wrapper_stops_daemon_when_command_runner_raises(self) -> None:
        workspace = Path("/tmp/bench-workspace")
        args = SimpleNamespace(
            bitloops_init=True,
            bitloops_sync="true",
            bitloops_ingest="true",
            bitloops_embeddings_runtime=None,
            bitloops_no_embeddings=False,
            bitloops_no_summaries=False,
            bitloops_summary_mode=None,
            bitloops_embedding_mode=None,
        )
        daemon_handle = object()

        def fake_runner(*, timeout_seconds: int, env: dict[str, str] | None, cwd: str) -> object:
            raise ValueError("boom")

        with (
            patch.object(common, "resolve_workspace", return_value=workspace),
            patch.object(common, "resolve_bitloops_sandbox", return_value={"mode": "per_task_daemon"}),
            patch.object(common, "build_bitloops_task_environment", return_value={"BITLOOPS": "1"}),
            patch.object(common, "reset_workspace"),
            patch.object(common, "start_bitloops_task_daemon", return_value=daemon_handle),
            patch.object(common, "setup_bitloops_for_workspace", return_value={}),
            patch.object(common, "stop_bitloops_task_daemon") as mock_stop,
        ):
            with self.assertRaises(ValueError):
                common.run_agent_wrapper(
                    payload={"run": {"workspace_root": str(workspace)}},
                    args=args,
                    agent_name="opencode",
                    bitloops_setup_timeout_seconds=1500,
                    failure_message="agent command failed and no workspace changes were made",
                    command_runner=fake_runner,
                )

        mock_stop.assert_called_once_with(daemon_handle)


if __name__ == "__main__":
    unittest.main()
