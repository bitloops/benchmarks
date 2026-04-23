from __future__ import annotations

from .base import JsonCommandAgentAdapter


class CodexAdapter(JsonCommandAgentAdapter):
    @property
    def adapter_id(self) -> str:
        return "codex"
