from __future__ import annotations

import unittest

from benchkit.common.config import AgentConfig
<<<<<<< Updated upstream
from benchkit.swebench.agents.codex import CodexAdapter
=======
from benchkit.swebench.agents.opencode import OpencodeAdapter
>>>>>>> Stashed changes
from benchkit.swebench.agents.registry import build_agent_adapter


class AgentRegistryTests(unittest.TestCase):
<<<<<<< Updated upstream
    def test_build_returns_codex_adapter(self) -> None:
        adapter = build_agent_adapter(
            AgentConfig(
                id="codex",
                command=["python3", "scripts/agents/codex_wrapper.py"],
                extra_args=[],
            )
        )
        self.assertIsInstance(adapter, CodexAdapter)

    def test_unsupported_error_lists_codex(self) -> None:
        with self.assertRaises(ValueError) as context:
            build_agent_adapter(AgentConfig(id="unknown", command=[], extra_args=[]))
        self.assertIn("codex", str(context.exception))
=======
    def test_build_agent_adapter_supports_opencode(self) -> None:
        adapter = build_agent_adapter(
            AgentConfig(
                id="opencode",
                command=["python3", "scripts/agents/opencode_wrapper.py"],
            )
        )

        self.assertIsInstance(adapter, OpencodeAdapter)

    def test_build_agent_adapter_error_lists_opencode(self) -> None:
        with self.assertRaises(ValueError) as raised:
            build_agent_adapter(AgentConfig(id="unknown", command=[]))

        self.assertIn("opencode", str(raised.exception))
>>>>>>> Stashed changes


if __name__ == "__main__":
    unittest.main()
