from __future__ import annotations

import importlib
import sys
import unittest
from unittest.mock import patch


def _load_wrapper_module():
    return importlib.import_module("benchkit.swebench.agents.claude_code_wrapper")


wrapper = _load_wrapper_module()


class ClaudeCodeWrapperTests(unittest.TestCase):
    def test_parse_args_accepts_bitloops_no_summaries_flag(self) -> None:
        with patch.object(sys, "argv", ["claude_code_wrapper.py", "--bitloops-no-summaries"]):
            args = wrapper.parse_args()
        self.assertTrue(args.bitloops_no_summaries)

    def test_resolve_timeout_uses_larger_env_override(self) -> None:
        payload = {"run": {"timeout_seconds": 3600}}
        with patch.dict(wrapper.os.environ, {"CLAUDE_TIMEOUT_SECONDS": "7200"}, clear=True):
            timeout = wrapper._resolve_claude_timeout_seconds(payload)
        self.assertEqual(timeout, 7200)

    def test_resolve_timeout_does_not_allow_smaller_env_override(self) -> None:
        payload = {"run": {"timeout_seconds": 3600}}
        with patch.dict(wrapper.os.environ, {"CLAUDE_TIMEOUT_SECONDS": "1800"}, clear=True):
            timeout = wrapper._resolve_claude_timeout_seconds(payload)
        self.assertEqual(timeout, 3600)

    def test_resolve_timeout_falls_back_to_run_timeout(self) -> None:
        payload = {"run": {"timeout_seconds": 3600}}
        with patch.dict(wrapper.os.environ, {}, clear=True):
            timeout = wrapper._resolve_claude_timeout_seconds(payload)
        self.assertEqual(timeout, 3600)

    def test_resolve_timeout_uses_default_when_missing(self) -> None:
        with patch.dict(wrapper.os.environ, {}, clear=True):
            timeout = wrapper._resolve_claude_timeout_seconds({})
        self.assertEqual(timeout, 900)

    def test_resolve_bitloops_setup_timeout_uses_larger_env_override(self) -> None:
        payload = {"run": {"timeout_seconds": 3600}}
        with patch.dict(wrapper.os.environ, {"BITLOOPS_SETUP_TIMEOUT_SECONDS": "7200"}, clear=True):
            timeout = wrapper._resolve_bitloops_setup_timeout_seconds(payload)
        self.assertEqual(timeout, 7200)

    def test_resolve_bitloops_setup_timeout_does_not_allow_smaller_env_override(self) -> None:
        payload = {"run": {"timeout_seconds": 3600}}
        with patch.dict(wrapper.os.environ, {"BITLOOPS_SETUP_TIMEOUT_SECONDS": "600"}, clear=True):
            timeout = wrapper._resolve_bitloops_setup_timeout_seconds(payload)
        self.assertEqual(timeout, 3600)

    def test_resolve_bitloops_setup_timeout_falls_back_to_run_timeout(self) -> None:
        payload = {"run": {"timeout_seconds": 3600}}
        with patch.dict(wrapper.os.environ, {}, clear=True):
            timeout = wrapper._resolve_bitloops_setup_timeout_seconds(payload)
        self.assertEqual(timeout, 3600)

    def test_resolve_bitloops_setup_timeout_uses_25_minute_floor_for_short_runs(self) -> None:
        payload = {"run": {"timeout_seconds": 900}}
        with patch.dict(wrapper.os.environ, {}, clear=True):
            timeout = wrapper._resolve_bitloops_setup_timeout_seconds(payload)
        self.assertEqual(timeout, 1500)

    def test_resolve_bitloops_setup_timeout_uses_default_when_missing(self) -> None:
        with patch.dict(wrapper.os.environ, {}, clear=True):
            timeout = wrapper._resolve_bitloops_setup_timeout_seconds({})
        self.assertEqual(timeout, 1500)


if __name__ == "__main__":
    unittest.main()
