from __future__ import annotations

from benchkit.common.config import AgentConfig

from .base import AgentAdapter, NoopAgentAdapter
from .claude_code import ClaudeCodeAdapter
<<<<<<< Updated upstream
from .codex import CodexAdapter
=======
from .opencode import OpencodeAdapter
>>>>>>> Stashed changes
from .cursor import CursorAdapter


def build_agent_adapter(config: AgentConfig) -> AgentAdapter:
    adapter_id = config.id.strip().lower()

    if adapter_id == "noop":
        return NoopAgentAdapter(config)
    if adapter_id == "claude_code":
        return ClaudeCodeAdapter(config)
    if adapter_id == "cursor":
        return CursorAdapter(config)
<<<<<<< Updated upstream
    if adapter_id == "codex":
        return CodexAdapter(config)

    raise ValueError(
        f"Unsupported agent id '{config.id}'. "
        "Supported: noop, claude_code, cursor, codex"
=======
    if adapter_id == "opencode":
        return OpencodeAdapter(config)

    raise ValueError(
        f"Unsupported agent id '{config.id}'. "
        "Supported: noop, claude_code, cursor, opencode"
>>>>>>> Stashed changes
    )
