from __future__ import annotations

import importlib
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch


def _load_wrapper_module():
    return importlib.import_module("benchkit.swebench.agents.ollama.wrapper")


wrapper = _load_wrapper_module()


class OllamaWrapperTests(unittest.TestCase):
    def test_persist_raw_patch_writes_attempt_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            payload = {
                "instance_id": "tokio-rs__axum-1119",
                "run": {
                    "attempt_dir": temp_dir,
                },
            }

            patch_path = wrapper._persist_raw_patch(
                payload=payload,
                patch="diff --git a/foo b/foo\n",
                label="apply-failed",
            )

            self.assertEqual(
                Path(patch_path),
                Path(temp_dir) / "agent_raw" / "tokio-rs__axum-1119.ollama.apply-failed.patch",
            )
            self.assertEqual(
                Path(patch_path).read_text(encoding="utf-8"),
                "diff --git a/foo b/foo\n",
            )

    def test_resolve_repo_ollama_config_path_defaults_to_repo_json(self) -> None:
        with patch.dict(wrapper.os.environ, {}, clear=True):
            config_path = wrapper._resolve_repo_ollama_config_path()
        self.assertEqual(config_path.name, "ollama.json")

    def test_resolve_repo_ollama_config_path_ignores_env_override(self) -> None:
        with patch.dict(
            wrapper.os.environ,
            {"OLLAMA_CONFIG_PATH": "/tmp/custom-ollama.json"},
            clear=True,
        ):
            config_path = wrapper._resolve_repo_ollama_config_path()
        self.assertEqual(config_path.name, "ollama.json")

    def test_resolve_model_name_prefers_payload_over_env_override(self) -> None:
        payload = {"model": {"name": "deepseek-v4-flash:cloud"}}
        runtime_config = {"model": "deepseek-v4-flash"}
        with patch.dict(wrapper.os.environ, {"OLLAMA_MODEL": "gpt-oss:120b"}, clear=True):
            model_name = wrapper._resolve_model_name(payload, runtime_config)
        self.assertEqual(model_name, "deepseek-v4-flash:cloud")

    def test_resolve_model_name_uses_env_when_payload_missing(self) -> None:
        runtime_config = {"model": "deepseek-v4-flash"}
        with patch.dict(wrapper.os.environ, {"OLLAMA_MODEL": "gpt-oss:120b"}, clear=True):
            model_name = wrapper._resolve_model_name({}, runtime_config)
        self.assertEqual(model_name, "gpt-oss:120b")

    def test_resolve_auth_bearer_token_prefers_explicit_auth_token(self) -> None:
        with patch.dict(
            wrapper.os.environ,
            {
                "OLLAMA_AUTH_TOKEN": "auth-token-123",
            },
            clear=True,
        ):
            auth_token = wrapper._resolve_auth_bearer_token()
        self.assertEqual(auth_token, "auth-token-123")

    def test_resolve_auth_bearer_token_returns_none_when_unset(self) -> None:
        with patch.dict(wrapper.os.environ, {}, clear=True):
            auth_token = wrapper._resolve_auth_bearer_token()
        self.assertIsNone(auth_token)

    def test_build_apply_repair_prompt_includes_workspace_file_context(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            file_path = workspace / "src" / "routing" / "mod.rs"
            file_path.parent.mkdir(parents=True)
            file_path.write_text(
                "use crate::response::Redirect;\nfn handle() {}\n",
                encoding="utf-8",
            )

            prompt = wrapper._build_apply_repair_prompt(
                original_prompt="Fix the routing bug.",
                prior_response="--- a/src/routing/mod.rs",
                patch=(
                    "--- a/src/routing/mod.rs\n"
                    "+++ b/src/routing/mod.rs\n"
                    "@@ -1,1 +1,1 @@\n"
                    "-use crate::response::Redirect;\n"
                    "+use crate::response::Response;\n"
                ),
                apply_error="patch does not apply",
                workspace=workspace,
            )

        self.assertIn("Current workspace file content", prompt)
        self.assertIn("[start of src/routing/mod.rs", prompt)
        self.assertIn("use crate::response::Redirect;", prompt)

    def test_build_apply_repair_prompt_uses_hunk_line_windows(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            file_path = workspace / "src" / "routing" / "mod.rs"
            file_path.parent.mkdir(parents=True)
            content = "\n".join(f"line_{idx}" for idx in range(1, 181)) + "\n"
            file_path.write_text(content, encoding="utf-8")

            prompt = wrapper._build_apply_repair_prompt(
                original_prompt="Fix the routing bug.",
                prior_response="--- a/src/routing/mod.rs",
                patch=(
                    "--- a/src/routing/mod.rs\n"
                    "+++ b/src/routing/mod.rs\n"
                    "@@ -150,2 +150,2 @@\n"
                    "-line_150\n"
                    "+line_150_changed\n"
                ),
                apply_error="patch does not apply",
                workspace=workspace,
            )

        self.assertIn("line_150", prompt)
        self.assertNotIn("line_1\nline_2\nline_3\nline_4\nline_5\nline_6\nline_7\nline_8\nline_9\nline_10", prompt)

    def test_check_patch_applies_accepts_git_apply_even_if_patch_binary_rejects(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)

            with (
                patch.object(wrapper.shutil, "which", return_value="/usr/bin/patch"),
                patch.object(
                    wrapper.subprocess,
                    "run",
                    side_effect=[
                        subprocess.CompletedProcess(
                            args=["git", "apply"],
                            returncode=0,
                            stdout="",
                            stderr="",
                        ),
                        subprocess.CompletedProcess(
                            args=["patch", "--dry-run"],
                            returncode=2,
                            stdout="",
                            stderr="malformed patch",
                        ),
                    ],
                ),
            ):
                ok, error, events = wrapper._check_patch_applies(
                    workspace=workspace,
                    patch="--- a/foo\n+++ b/foo\n@@ -1 +1 @@\n-a\n+b\n",
                )

        self.assertTrue(ok)
        self.assertEqual(error, "")
        self.assertEqual(len(events), 2)


if __name__ == "__main__":
    unittest.main()
