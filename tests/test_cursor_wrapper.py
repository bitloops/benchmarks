from __future__ import annotations

import importlib
import sys
import unittest
from unittest.mock import patch


def _load_wrapper_module():
    return importlib.import_module("benchkit.swebench.agents.cursor_wrapper")


wrapper = _load_wrapper_module()


class CursorWrapperTests(unittest.TestCase):
    def test_parse_args_accepts_bitloops_no_summaries_flag(self) -> None:
        with patch.object(sys, "argv", ["cursor_wrapper.py", "--bitloops-no-summaries"]):
            args = wrapper.parse_args()
        self.assertTrue(args.bitloops_no_summaries)

    def test_parse_args_accepts_bitloops_summary_mode_on(self) -> None:
        with patch.object(
            sys,
            "argv",
            ["cursor_wrapper.py", "--bitloops-summary-mode", "on"],
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


if __name__ == "__main__":
    unittest.main()
