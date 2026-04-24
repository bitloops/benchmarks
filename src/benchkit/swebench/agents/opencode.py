from __future__ import annotations

from .base import JsonCommandAgentAdapter


class OpencodeAdapter(JsonCommandAgentAdapter):
    @property
    def adapter_id(self) -> str:
        return "opencode"
