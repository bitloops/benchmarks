from __future__ import annotations

from ..base import JsonCommandAgentAdapter


class OllamaAdapter(JsonCommandAgentAdapter):
    @property
    def adapter_id(self) -> str:
        return "ollama"
