from __future__ import annotations

from pathlib import Path
import json
import importlib
import sys
import tempfile
import unittest
from unittest.mock import patch


def _load_wrapper_module():
    return importlib.import_module("benchkit.swebench.agents.codex.wrapper")


wrapper = _load_wrapper_module()


class CodexWrapperTests(unittest.TestCase):
    def test_parse_args_accepts_bitloops_no_summaries_flag(self) -> None:
        with patch.object(sys, "argv", ["codex_wrapper.py", "--bitloops-no-summaries"]):
            args = wrapper.parse_args()
        self.assertTrue(args.bitloops_no_summaries)

    def test_parse_args_accepts_bitloops_summary_mode_on(self) -> None:
        with patch.object(
            sys,
            "argv",
            ["codex_wrapper.py", "--bitloops-summary-mode", "on"],
        ):
            args = wrapper.parse_args()
        self.assertEqual(args.bitloops_summary_mode, "on")

    def test_resolve_bitloops_setup_timeout_uses_25_minute_floor_for_short_runs(self) -> None:
        payload = {"run": {"timeout_seconds": 900}}
        with patch.dict(wrapper.os.environ, {}, clear=True):
            timeout = wrapper._resolve_bitloops_setup_timeout_seconds(payload)
        self.assertEqual(timeout, 1500)

    def test_resolve_bitloops_setup_timeout_uses_default_when_missing(self) -> None:
        with patch.dict(wrapper.os.environ, {}, clear=True):
            timeout = wrapper._resolve_bitloops_setup_timeout_seconds({})
        self.assertEqual(timeout, 1500)

    def test_build_codex_runtime_config_reads_repo_json(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "codex.json"
            config_path.write_text(
                json.dumps(
                    {
                        "model": "gpt-5.4",
                        "timeout_seconds": 1200,
                        "full_auto": True,
                    }
                ),
                encoding="utf-8",
            )

            runtime_config = wrapper._build_codex_runtime_config(
                existing_content="",
                repo_config_path=config_path,
            )

        self.assertEqual(runtime_config["model"], "gpt-5.4")
        self.assertEqual(runtime_config["timeout_seconds"], 1200)
        self.assertEqual(runtime_config["full_auto"], True)

    def test_build_codex_runtime_config_env_then_repo_override(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "codex.json"
            config_path.write_text(
                json.dumps(
                    {
                        "timeout_seconds": 1200,
                        "full_auto": True,
                    }
                ),
                encoding="utf-8",
            )

            runtime_config = wrapper._build_codex_runtime_config(
                existing_content=json.dumps({"timeout_seconds": 3000, "sandbox": "read-only"}),
                repo_config_path=config_path,
            )

        self.assertEqual(runtime_config["timeout_seconds"], 1200)
        self.assertEqual(runtime_config["sandbox"], "read-only")
        self.assertEqual(runtime_config["full_auto"], True)

    def test_resolve_codex_timeout_seconds_uses_largest_value(self) -> None:
        payload = {"run": {"timeout_seconds": 900}}
        runtime_config = {"timeout_seconds": 1200}
        with patch.dict(wrapper.os.environ, {"CODEX_TIMEOUT_SECONDS": "1800"}, clear=True):
            timeout = wrapper._resolve_codex_timeout_seconds(payload, runtime_config)
        self.assertEqual(timeout, 1800)

    def test_should_require_devql_invocation_defaults_on_for_bitloops_condition(self) -> None:
        payload = {"run": {"condition": "with_bitloops"}}
        with patch.dict(wrapper.os.environ, {}, clear=False):
            self.assertTrue(wrapper._should_require_devql_invocation(payload))

    def test_should_require_devql_invocation_respects_explicit_env_override(self) -> None:
        payload = {"run": {"condition": "with_bitloops"}}
        with patch.dict(wrapper.os.environ, {"BENCHKIT_REQUIRE_CODEX_DEVQL": "false"}, clear=False):
            self.assertFalse(wrapper._should_require_devql_invocation(payload))

    def test_has_devql_invocation_detects_bitloops_devql_shell_command(self) -> None:
        raw = [
            {
                "tool": "Bash",
                "input": {
                    "command": "/bin/zsh -lc \"bitloops devql query '{ selectArtefacts { summary } }'\"",
                    "status": "failed",
                    "exit_code": 1,
                },
            }
        ]
        self.assertTrue(wrapper._has_devql_invocation(raw))

    def test_resolve_missing_devql_invocation_error_requires_devql_for_bitloops_runs(self) -> None:
        payload = {"run": {"condition": "with_bitloops"}}
        message = wrapper._resolve_missing_devql_invocation_error(
            payload=payload,
            tool_invocations_raw=[{"tool": "Bash", "input": {"command": "rg -n foo src"}}],
        )
        self.assertIsInstance(message, str)
        assert isinstance(message, str)
        self.assertIn("devql", message.lower())

    def test_resolve_missing_devql_invocation_error_allows_runs_with_devql(self) -> None:
        payload = {"run": {"condition": "with_bitloops"}}
        message = wrapper._resolve_missing_devql_invocation_error(
            payload=payload,
            tool_invocations_raw=[
                {
                    "tool": "Bash",
                    "input": {
                        "command": "bitloops devql query '{ selectArtefacts(by: { fuzzyName: \"foo\" }) { summary } }'",
                        "status": "completed",
                        "exit_code": 0,
                    },
                }
            ],
        )
        self.assertIsNone(message)


if __name__ == "__main__":
    unittest.main()
