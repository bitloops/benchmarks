from __future__ import annotations

from .base import JsonCommandAgentAdapter


class ClaudeCodeAdapter(JsonCommandAgentAdapter):
    @property
    def adapter_id(self) -> str:
        return "claude_code"
