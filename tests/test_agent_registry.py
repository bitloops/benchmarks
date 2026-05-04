from __future__ import annotations

import unittest

from benchkit.common.config import AgentConfig
from benchkit.swebench.agents.codex import CodexAdapter
from benchkit.swebench.agents.opencode import OpencodeAdapter
from benchkit.swebench.agents.registry import build_agent_adapter


class AgentRegistryTests(unittest.TestCase):
    def test_build_returns_codex_adapter(self) -> None:
        adapter = build_agent_adapter(
            AgentConfig(
                id="codex",
                command=["python3", "-m", "benchkit.swebench.agents.codex.wrapper"],
                extra_args=[],
            )
        )
        self.assertIsInstance(adapter, CodexAdapter)

    def test_unsupported_error_lists_codex(self) -> None:
        with self.assertRaises(ValueError) as context:
            build_agent_adapter(AgentConfig(id="unknown", command=[], extra_args=[]))
        self.assertIn("codex", str(context.exception))

    def test_build_agent_adapter_supports_opencode(self) -> None:
        adapter = build_agent_adapter(
            AgentConfig(
                id="opencode",
                command=["python3", "-m", "benchkit.swebench.agents.opencode.wrapper"],
            )
        )

        self.assertIsInstance(adapter, OpencodeAdapter)

    def test_build_agent_adapter_error_lists_opencode(self) -> None:
        with self.assertRaises(ValueError) as raised:
            build_agent_adapter(AgentConfig(id="unknown", command=[]))

        self.assertIn("opencode", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
