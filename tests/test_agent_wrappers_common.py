from __future__ import annotations

from pathlib import Path
import contextlib
import importlib.util
import json
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch


def _load_common_module():
    module_path = Path(__file__).resolve().parents[1] / "scripts" / "agents" / "common.py"
    spec = importlib.util.spec_from_file_location("agent_common", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Failed to load scripts/agents/common.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


common = _load_common_module()


class AgentWrapperCommonTests(unittest.TestCase):
    def test_render_task_prompt_prefixes_devql_issue_text_for_bitloops_condition(self) -> None:
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

        self.assertIn("Issue:\nUsing DevQL\n\nFix the failing lint behavior.", prompt)
        self.assertIn(
            "- For code understanding and exploration, you must use `bitloops devql` first.\n",
            prompt,
        )
        self.assertIn(
            "- Only fall back to grep/read/glob or directory crawling if DevQL returns nothing useful.\n",
            prompt,
        )

    def test_render_task_prompt_leaves_non_bitloops_issue_text_unchanged(self) -> None:
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

        self.assertIn("Issue:\nFix the failing lint behavior.", prompt)
        self.assertNotIn("Issue:\nUsing DevQL\n\nFix the failing lint behavior.", prompt)
        self.assertNotIn("bitloops devql", prompt)

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

    def test_extract_git_patch_from_markdown_fence(self) -> None:
        raw = """Here is the fix:\n```diff\ndiff --git a/a b/a\n--- a/a\n+++ b/a\n@@ -1 +1 @@\n-old\n+new\n```\n"""
        patch, source = common.extract_git_patch(raw)
        self.assertEqual(source, "diff_header")
        self.assertTrue(patch.startswith("diff --git"))

    def test_extract_git_patch_returns_empty_when_missing(self) -> None:
        patch, source = common.extract_git_patch("No code changes required.")
        self.assertEqual(patch, "")
        self.assertEqual(source, "no_patch_found")

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
        self.assertEqual(metadata["bitloops_start_command"], ["bitloops", "start", "--detached"])
        self.assertIsNone(metadata["bitloops_bootstrap_command"])
        first_command = mock_call.call_args_list[0].args[0]
        second_command = mock_call.call_args_list[1].args[0]
        self.assertEqual(first_command, ["bitloops", "status"])
        self.assertEqual(second_command, ["bitloops", "start", "--detached"])

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
        self.assertEqual(metadata["bitloops_start_command"], ["bitloops", "start", "--detached"])
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
        self.assertEqual(
            init_command,
            [
                "bitloops",
                "init",
                "--agent",
                "claude-code",
                "--telemetry=false",
                "--sync=true",
                "--ingest=false",
            ],
        )
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
                "--ingest=false",
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

    def test_setup_bitloops_supports_sync_ingest_embeddings_and_semantic_modes(self) -> None:
        responses = [
            ("Bitloops daemon: running\n", "", 0, 5),
            ("Bitloops init completed", "", 0, 16),
        ]
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            config_path = workspace / "config.toml"
            config_path.write_text("[semantic_clones]\nsummary_mode = \"auto\"\n", encoding="utf-8")
            with patch.object(
                common,
                "_ensure_git_branch_for_bitloops_sync",
                return_value=(False, False, None, None, 0),
            ), patch.object(common, "call_command", side_effect=responses) as mock_call, patch.object(
                common.Path,
                "cwd",
                return_value=workspace,
            ):
                metadata = common.setup_bitloops_for_workspace(
                    agent_name="claude-code",
                    bitloops_bin="bitloops",
                    timeout_seconds=30,
                    ingest=True,
                    embeddings_runtime="local",
                    summary_mode="off",
                    embedding_mode="deterministic",
                )
                rendered = config_path.read_text(encoding="utf-8")

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
                "local",
            ],
        )
        self.assertEqual(metadata["bitloops_summary_mode"], "off")
        self.assertEqual(metadata["bitloops_embedding_mode"], "deterministic")
        self.assertIn('summary_mode = "off"', rendered)
        self.assertIn('embedding_mode = "deterministic"', rendered)

    def test_setup_bitloops_supports_no_embeddings_flag(self) -> None:
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
                no_embeddings=True,
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
                "--ingest=false",
                "--no-embeddings",
            ],
        )
        self.assertTrue(metadata["bitloops_no_embeddings"])

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
        self.assertEqual(env["BITLOOPS_BENCHKIT_SANDBOX_MODE"], "per_task_daemon")
        self.assertEqual(env["PATH"], "/usr/bin")

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

        with patch.object(
            common,
            "_ensure_git_branch_for_bitloops_sync",
            return_value=(False, False, None, None, 0),
        ), patch.object(
            common,
            "_run_bitloops_init",
            return_value=init_metadata,
        ) as mock_init, patch.object(common, "_acquire_bitloops_global_lock") as mock_lock:
            metadata = common.setup_bitloops_for_workspace(
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
        self.assertFalse(metadata["bitloops_global_lock_enabled"])
        self.assertFalse(metadata["bitloops_setup_serialized"])
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

    def test_run_bitloops_init_shortcuts_when_runtime_sync_is_complete(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            workspace = root / "workspace"
            workspace.mkdir()
            home = root / "home"
            runtime_dir = home / ".local" / "state" / "bitloops" / "daemon"
            runtime_dir.mkdir(parents=True)
            runtime_db = runtime_dir / "runtime.sqlite"

            connection = sqlite3.connect(runtime_db)
            try:
                connection.execute(
                    "CREATE TABLE runtime_documents (document_kind TEXT PRIMARY KEY, payload TEXT NOT NULL, updated_at TEXT DEFAULT CURRENT_TIMESTAMP)"
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
                            "init_session_id": "init-session-1",
                            "submitted_at_unix": 100,
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
                            "follow_up_sync_required": False,
                            "initial_sync_completion_seq": 1,
                            "submitted_at_unix": 100,
                            "selections": {"run_sync": True},
                        }
                    ],
                }
                enrichment_payload = {
                    "version": 1,
                    "jobs": [],
                    "last_action": "completed",
                }
                for kind, payload in (
                    ("devql_task_queue_state", queue_payload),
                    ("init_session_state", init_payload),
                    ("enrichment_queue_state", enrichment_payload),
                ):
                    connection.execute(
                        "INSERT INTO runtime_documents(document_kind, payload) VALUES (?, ?)",
                        (kind, json.dumps(payload)),
                    )
                connection.commit()
            finally:
                connection.close()

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

            process = _HangingProcess()
            with patch.object(common.subprocess, "Popen", return_value=process):
                metadata = common._run_bitloops_init(
                    binary="bitloops",
                    timeout=30,
                    agent_name="claude-code",
                    sync=True,
                    ingest=False,
                    install_default_daemon=False,
                    embeddings_runtime=None,
                    no_embeddings=False,
                    env={
                        "HOME": str(home),
                        "BITLOOPS_BENCHKIT_SANDBOX_MODE": "per_task_daemon",
                    },
                    cwd=str(workspace),
                )

        self.assertTrue(metadata["bitloops_init_runtime_ready_shortcut_used"])
        self.assertEqual(metadata["bitloops_init_command"][0:4], ["bitloops", "init", "--agent", "claude-code"])
        ready = metadata["bitloops_init_runtime_ready_shortcut"]
        assert isinstance(ready, dict)
        self.assertEqual(ready["sync_phase"], "complete")
        self.assertEqual(ready["paths_completed"], 10)
        self.assertEqual(ready["paths_remaining"], 0)

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


if __name__ == "__main__":
    unittest.main()
