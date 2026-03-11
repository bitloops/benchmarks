from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from benchkit.swebench.agents.base import (
    _heartbeat_interval_seconds,
    _resolve_relative_command_paths,
)


class AgentBaseTests(unittest.TestCase):
    def test_resolves_relative_script_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir)
            script_dir = base_dir / "scripts" / "agents"
            script_dir.mkdir(parents=True)
            script_path = script_dir / "wrapper.py"
            script_path.write_text("print('ok')\n", encoding="utf-8")

            command = ["python3", "scripts/agents/wrapper.py", "--flag"]
            resolved = _resolve_relative_command_paths(command, base_dir=base_dir)

            self.assertEqual(resolved[0], "python3")
            self.assertEqual(Path(resolved[1]).resolve(), script_path.resolve())
            self.assertEqual(resolved[2], "--flag")

    def test_keeps_non_path_tokens_unchanged(self) -> None:
        resolved = _resolve_relative_command_paths(
            ["claude", "--print", "hello"],
            base_dir=Path.cwd(),
        )
        self.assertEqual(resolved, ["claude", "--print", "hello"])

    def test_keeps_missing_relative_paths_unchanged(self) -> None:
        base_dir = Path.cwd()
        command = ["python3", "scripts/agents/missing_wrapper.py"]
        resolved = _resolve_relative_command_paths(command, base_dir=base_dir)
        self.assertEqual(resolved, command)

    def test_heartbeat_interval_defaults_to_20_seconds(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            self.assertEqual(_heartbeat_interval_seconds(), 20.0)

    def test_heartbeat_interval_can_be_disabled(self) -> None:
        with patch.dict("os.environ", {"BENCHKIT_AGENT_HEARTBEAT_SECONDS": "off"}):
            self.assertIsNone(_heartbeat_interval_seconds())

    def test_heartbeat_interval_uses_custom_positive_value(self) -> None:
        with patch.dict("os.environ", {"BENCHKIT_AGENT_HEARTBEAT_SECONDS": "7.5"}):
            self.assertEqual(_heartbeat_interval_seconds(), 7.5)


if __name__ == "__main__":
    unittest.main()
