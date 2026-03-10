from __future__ import annotations

from pathlib import Path
import importlib.util
import unittest


def _load_common_module():
    module_path = Path(__file__).resolve().parents[1] / "scripts" / "agents" / "common.py"
    spec = importlib.util.spec_from_file_location("agent_common", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Failed to load scripts/agents/common.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


common = _load_common_module()


class AgentWrapperCommonTests(unittest.TestCase):
    def test_parse_agent_output_prefers_json_result(self) -> None:
        raw = '{"type":"result","result":"diff --git a/x b/x\\n--- a/x\\n+++ b/x\\n@@ -1 +1 @@\\n-a\\n+b\\n"}'
        text = common.parse_agent_output(raw)
        self.assertIn("diff --git", text)

    def test_extract_git_patch_from_markdown_fence(self) -> None:
        raw = """Here is the fix:\n```diff\ndiff --git a/a b/a\n--- a/a\n+++ b/a\n@@ -1 +1 @@\n-old\n+new\n```\n"""
        patch, source = common.extract_git_patch(raw)
        self.assertEqual(source, "diff_header")
        self.assertTrue(patch.startswith("diff --git"))

    def test_extract_git_patch_returns_empty_when_missing(self) -> None:
        patch, source = common.extract_git_patch("No code changes required.")
        self.assertEqual(patch, "")
        self.assertEqual(source, "no_patch_found")


if __name__ == "__main__":
    unittest.main()
