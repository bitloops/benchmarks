from __future__ import annotations

from .base import JsonCommandAgentAdapter


class CursorAdapter(JsonCommandAgentAdapter):
    @property
    def adapter_id(self) -> str:
        return "cursor"
