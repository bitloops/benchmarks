from __future__ import annotations

import unittest

from benchkit.swebench.model_mapper import resolve_model_name, suggest_model_id_for_agent


class ModelMapperTests(unittest.TestCase):
    def test_resolve_uses_agent_specific_entry(self) -> None:
        result = resolve_model_name(
            canonical_name="opus-4-6",
            agent_id="claude_code",
            model_map={
                "claude_code": {"opus-4-6": "claude-opus-4-6"},
                "cursor": {"opus-4-6": "some-other-id"},
            },
        )
        self.assertEqual(result.resolved_name, "claude-opus-4-6")
        self.assertEqual(result.map_key, "claude_code")

    def test_resolve_falls_back_to_default(self) -> None:
        result = resolve_model_name(
            canonical_name="opus-4-6",
            agent_id="claude_code",
            model_map={
                "default": {"opus-4-6": "claude-opus-4-6"},
            },
        )
        self.assertEqual(result.resolved_name, "claude-opus-4-6")
        self.assertEqual(result.map_key, "default")

    def test_resolve_identity_when_missing(self) -> None:
        result = resolve_model_name(
            canonical_name="gpt-5.4-high",
            agent_id="cursor",
            model_map={},
        )
        self.assertEqual(result.resolved_name, "gpt-5.4-high")
        self.assertEqual(result.source, "identity")

    def test_resolve_uses_normalized_map_key(self) -> None:
        result = resolve_model_name(
            canonical_name="opus-4-6",
            agent_id="cursor",
            model_map={
                "cursor": {"opus-4.6": "opus-4.6"},
            },
        )
        self.assertEqual(result.resolved_name, "opus-4.6")
        self.assertEqual(result.source, "config:model_map_normalized")

    def test_resolve_fails_fast_on_agent_model_mismatch(self) -> None:
        with self.assertRaises(ValueError):
            resolve_model_name(
                canonical_name="opus-4-6",
                agent_id="cursor",
                model_map={
                    "cursor": {"opus-4-6": "claude-opus-4-6"},
                },
            )

    def test_resolve_can_disable_strict_validation(self) -> None:
        result = resolve_model_name(
            canonical_name="opus-4-6",
            agent_id="cursor",
            model_map={},
            strict=False,
        )
        self.assertEqual(result.resolved_name, "opus-4-6")
        self.assertEqual(result.source, "identity")

    def test_resolve_codex_identity_mapping(self) -> None:
        result = resolve_model_name(
            canonical_name="gpt-5.4",
            agent_id="codex",
            model_map={},
        )
        self.assertEqual(result.resolved_name, "gpt-5.4")
        self.assertEqual(result.source, "identity")

    def test_resolve_codex_uses_agent_specific_map(self) -> None:
        result = resolve_model_name(
            canonical_name="gpt-5.4",
            agent_id="codex",
            model_map={
                "codex": {"gpt-5.4": "gpt-5.4"},
            },
        )
        self.assertEqual(result.resolved_name, "gpt-5.4")
        self.assertEqual(result.map_key, "codex")

    def test_suggest_model_id_for_agent(self) -> None:
        self.assertEqual(
            suggest_model_id_for_agent("opus-4-6", "claude_code"),
            "claude-opus-4-6",
        )
        self.assertEqual(
            suggest_model_id_for_agent("opus-4-6", "cursor"),
            "opus-4.6",
        )
        self.assertIsNone(suggest_model_id_for_agent("gpt-5.4", "codex"))


if __name__ == "__main__":
    unittest.main()
