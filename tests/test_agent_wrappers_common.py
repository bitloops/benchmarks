from __future__ import annotations

from pathlib import Path
import importlib.util
import tempfile
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
    def test_parse_agent_output_prefers_json_result(self) -> None:
        raw = '{"type":"result","result":"diff --git a/x b/x\\n--- a/x\\n+++ b/x\\n@@ -1 +1 @@\\n-a\\n+b\\n"}'
        text = common.parse_agent_output(raw)
        self.assertIn("diff --git", text)

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
        self.assertEqual(metrics.get("search_actions"), 2)
        self.assertEqual(metrics.get("tool_calls"), 5)

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
            {"token_input": 100, "tool_calls": 2},
            {"token_input": 200, "shell_commands": 5, "hook_metrics_path": "/tmp/hook.jsonl"},
        )
        self.assertEqual(merged["token_input"], 100)
        self.assertEqual(merged["tool_calls"], 2)
        self.assertEqual(merged["shell_commands"], 5)
        self.assertEqual(merged["hook_metrics_path"], "/tmp/hook.jsonl")


if __name__ == "__main__":
    unittest.main()
