from __future__ import annotations

import json
import importlib
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch


def _load_wrapper_module():
    return importlib.import_module("benchkit.swebench.agents.opencode.wrapper")


wrapper = _load_wrapper_module()


class OpencodeWrapperTests(unittest.TestCase):
    def test_parse_args_accepts_bitloops_no_summaries_flag(self) -> None:
        with patch.object(sys, "argv", ["opencode_wrapper.py", "--bitloops-no-summaries"]):
            args = wrapper.parse_args()
        self.assertTrue(args.bitloops_no_summaries)

    def test_resolve_opencode_bin_prefers_env_override(self) -> None:
        with patch.dict(wrapper.os.environ, {"OPENCODE_BIN": "/tmp/custom-opencode"}, clear=False):
            self.assertEqual(wrapper.resolve_opencode_bin(), "/tmp/custom-opencode")

    def test_resolve_opencode_bin_uses_path_lookup(self) -> None:
        with patch.dict(wrapper.os.environ, {}, clear=False):
            with patch.object(wrapper.shutil, "which", return_value="/usr/local/bin/opencode") as mock_which:
                self.assertEqual(wrapper.resolve_opencode_bin(), "/usr/local/bin/opencode")
        mock_which.assert_called_once_with("opencode")

    def test_resolve_opencode_bin_falls_back_to_home_install(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            install_path = Path(temp_dir) / ".opencode" / "bin" / "opencode"
            install_path.parent.mkdir(parents=True, exist_ok=True)
            install_path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            install_path.chmod(0o755)

            with patch.dict(wrapper.os.environ, {}, clear=False):
                with patch.object(wrapper.shutil, "which", return_value=None):
                    with patch.object(wrapper.Path, "home", return_value=Path(temp_dir)):
                        self.assertEqual(wrapper.resolve_opencode_bin(), str(install_path))

    def test_resolve_raw_output_paths_uses_attempt_dir_and_instance_id(self) -> None:
        payload = {
            "instance_id": "tokio-rs__axum-1119",
            "run": {
                "attempt_dir": "/tmp/attempt-01",
            },
        }

        stdout_path, stderr_path = wrapper._resolve_raw_output_paths(payload)

        self.assertEqual(
            stdout_path,
            Path("/tmp/attempt-01/agent_raw/tokio-rs__axum-1119.opencode.stdout.jsonl"),
        )
        self.assertEqual(
            stderr_path,
            Path("/tmp/attempt-01/agent_raw/tokio-rs__axum-1119.opencode.stderr.log"),
        )

    def test_persist_raw_output_writes_attempt_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            payload = {
                "instance_id": "tokio-rs__axum-1119",
                "run": {
                    "attempt_dir": temp_dir,
                },
            }

            stdout_path, stderr_path = wrapper._persist_raw_opencode_output(
                payload=payload,
                stdout='{"type":"message.updated"}\n',
                stderr="migration complete\n",
            )

            self.assertEqual(
                Path(stdout_path),
                Path(temp_dir) / "agent_raw" / "tokio-rs__axum-1119.opencode.stdout.jsonl",
            )
            self.assertEqual(
                Path(stderr_path),
                Path(temp_dir) / "agent_raw" / "tokio-rs__axum-1119.opencode.stderr.log",
            )
            self.assertEqual(Path(stdout_path).read_text(encoding="utf-8"), '{"type":"message.updated"}\n')
            self.assertEqual(Path(stderr_path).read_text(encoding="utf-8"), "migration complete\n")

    def test_should_require_tool_invocations_defaults_on_for_bitloops_condition(self) -> None:
        payload = {"run": {"condition": "with_bitloops"}}
        with patch.dict(wrapper.os.environ, {}, clear=False):
            self.assertTrue(wrapper._should_require_tool_invocations(payload))

    def test_should_require_tool_invocations_respects_explicit_env_override(self) -> None:
        payload = {"run": {"condition": "with_bitloops"}}
        with patch.dict(
            wrapper.os.environ,
            {"BENCHKIT_REQUIRE_OPENCODE_TOOL_EVENTS": "false"},
            clear=False,
        ):
            self.assertFalse(wrapper._should_require_tool_invocations(payload))

    def test_resolve_missing_tool_capture_error_requires_tools_for_bitloops_runs(self) -> None:
        payload = {"run": {"condition": "with_bitloops"}}

        message = wrapper._resolve_missing_tool_capture_error(
            payload=payload,
            tool_invocations_raw=[],
            tool_usage_breakdown={},
        )

        self.assertIsInstance(message, str)
        assert isinstance(message, str)
        self.assertIn("bitloops devql query", message.lower())

    def test_resolve_missing_tool_capture_error_allows_captured_tools(self) -> None:
        payload = {"run": {"condition": "with_bitloops"}}

        message = wrapper._resolve_missing_tool_capture_error(
            payload=payload,
            tool_invocations_raw=[
                {
                    "tool": "Bash",
                    "input": {
                        "command": "bitloops devql query '{ selectArtefacts { summary } }'",
                    },
                }
            ],
            tool_usage_breakdown={},
        )

        self.assertIsNone(message)

    def test_has_devql_invocation_detects_bitloops_query(self) -> None:
        raw = [
            {
                "tool": "Bash",
                "input": {
                    "command": "/bin/zsh -lc \"bitloops devql query '{ selectArtefacts { summary } }'\"",
                },
            }
        ]
        self.assertTrue(wrapper._has_devql_invocation(raw))

    def test_normalize_model_reference_rewrites_legacy_fireworks_prefix(self) -> None:
        self.assertEqual(
            wrapper._normalize_opencode_model_reference(
                "fireworks/accounts/fireworks/models/qwen3p6-plus"
            ),
            "fireworks-ai/accounts/fireworks/models/qwen3p6-plus",
        )

    def test_merge_runtime_config_content_preserves_existing_env_config(self) -> None:
        runtime_config = {
            "$schema": "https://opencode.ai/config.json",
            "provider": {
                "openai": {
                    "models": {
                        "gpt-5": {
                            "options": {
                                "temperature": 0.0,
                                "seed": 7,
                            }
                        }
                    }
                }
            },
        }

        merged = wrapper._merge_opencode_config_content(
            json.dumps(
                {
                    "default_agent": "plan",
                    "provider": {
                        "anthropic": {
                            "options": {
                                "timeout": 600000,
                            }
                        }
                    },
                }
            ),
            runtime_config,
        )

        self.assertEqual(merged["default_agent"], "plan")
        self.assertEqual(merged["provider"]["anthropic"]["options"]["timeout"], 600000)
        self.assertEqual(
            merged["provider"]["openai"]["models"]["gpt-5"]["options"]["seed"],
            7,
        )

    def test_load_opencode_config_file_reads_json_object(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "opencode.json"
            config_path.write_text(
                json.dumps(
                    {
                        "model": "fireworks/accounts/fireworks/models/qwen3p6-plus",
                        "agent": {
                            "build": {
                                "temperature": 0.0,
                                "seed": 42,
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )

            loaded = wrapper._load_opencode_config_file(config_path)

        self.assertEqual(
            loaded,
            {
                "model": "fireworks/accounts/fireworks/models/qwen3p6-plus",
                "agent": {
                    "build": {
                        "temperature": 0.0,
                        "seed": 42,
                    }
                },
            },
        )

    def test_build_invocation_config_merges_env_and_repo_without_toml_runtime_layer(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_config_path = Path(temp_dir) / "opencode.json"
            repo_config_path.write_text(
                json.dumps(
                    {
                        "model": "fireworks/accounts/fireworks/models/qwen3p6-plus",
                        "provider": {
                            "fireworks-ai": {
                                "models": {
                                    "accounts/fireworks/models/qwen3p6-plus": {
                                        "options": {
                                            "temperature": 0.4,
                                            "seed": 42,
                                            "max_tokens": 32768,
                                        }
                                    }
                                }
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )

            merged = wrapper._build_opencode_invocation_config(
                existing_content=json.dumps({"small_model": "fireworks/accounts/fireworks/models/qwen3p6-plus"}),
                repo_config_path=repo_config_path,
            )

        self.assertIsNotNone(merged)
        assert merged is not None
        self.assertEqual(
            merged["small_model"],
            "fireworks/accounts/fireworks/models/qwen3p6-plus",
        )
        self.assertEqual(
            merged["model"],
            "fireworks/accounts/fireworks/models/qwen3p6-plus",
        )
        self.assertEqual(
            merged["provider"]["fireworks-ai"]["models"][
                "accounts/fireworks/models/qwen3p6-plus"
            ]["options"],
            {
                "temperature": 0.4,
                "seed": 42,
                "max_tokens": 32768,
            },
        )

    def test_build_ollama_provider_overlay_uses_openai_compatible_base_url(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_config_path = Path(temp_dir) / "ollama.json"
            repo_config_path.write_text(
                json.dumps(
                    {
                        "base_url": "http://localhost:11434",
                    }
                ),
                encoding="utf-8",
            )

            overlay = wrapper._build_ollama_opencode_provider_overlay(
                model_name="ollama/deepseek-v4-pro:cloud",
                repo_ollama_config_path=repo_config_path,
            )

        self.assertEqual(
            overlay,
            {
                "model": "ollama/deepseek-v4-pro:cloud",
                "small_model": "ollama/deepseek-v4-pro:cloud",
                "provider": {
                    "ollama": {
                        "npm": "@ai-sdk/openai-compatible",
                        "name": "Ollama",
                        "options": {
                            "baseURL": "http://localhost:11434/v1",
                        },
                        "models": {
                            "deepseek-v4-pro:cloud": {
                                "name": "deepseek-v4-pro:cloud",
                            }
                        },
                    }
                },
            },
        )

    def test_build_ollama_provider_overlay_ignores_non_ollama_models(self) -> None:
        self.assertIsNone(
            wrapper._build_ollama_opencode_provider_overlay(
                model_name="fireworks-ai/accounts/example/deployments/model",
            )
        )

    def test_resolve_timeout_uses_larger_env_override(self) -> None:
        payload = {"run": {"timeout_seconds": 3600}}
        with patch.dict(wrapper.os.environ, {"OPENCODE_TIMEOUT_SECONDS": "7200"}, clear=True):
            timeout = wrapper._resolve_opencode_timeout_seconds(payload)
        self.assertEqual(timeout, 7200)

    def test_resolve_timeout_does_not_allow_smaller_env_override(self) -> None:
        payload = {"run": {"timeout_seconds": 3600}}
        with patch.dict(wrapper.os.environ, {"OPENCODE_TIMEOUT_SECONDS": "1800"}, clear=True):
            timeout = wrapper._resolve_opencode_timeout_seconds(payload)
        self.assertEqual(timeout, 3600)

    def test_resolve_timeout_falls_back_to_run_timeout(self) -> None:
        payload = {"run": {"timeout_seconds": 3600}}
        with patch.dict(wrapper.os.environ, {}, clear=True):
            timeout = wrapper._resolve_opencode_timeout_seconds(payload)
        self.assertEqual(timeout, 3600)

    def test_resolve_timeout_uses_default_when_missing(self) -> None:
        with patch.dict(wrapper.os.environ, {}, clear=True):
            timeout = wrapper._resolve_opencode_timeout_seconds({})
        self.assertEqual(timeout, 900)

    def test_resolve_bitloops_setup_timeout_uses_25_minute_floor_for_short_runs(self) -> None:
        payload = {"run": {"timeout_seconds": 900}}
        with patch.dict(wrapper.os.environ, {}, clear=True):
            timeout = wrapper._resolve_bitloops_setup_timeout_seconds(payload)
        self.assertEqual(timeout, 1500)

    def test_resolve_bitloops_setup_timeout_uses_default_when_missing(self) -> None:
        with patch.dict(wrapper.os.environ, {}, clear=True):
            timeout = wrapper._resolve_bitloops_setup_timeout_seconds({})
        self.assertEqual(timeout, 1500)

    def test_clip_text_truncates_long_input(self) -> None:
        long = "x" * 5000
        out = wrapper._clip_text(long, limit=20)
        self.assertIn("truncated", out)
        self.assertLessEqual(len(out), 100)

    def test_summarize_opencode_stdout_errors_api_error(self) -> None:
        line = json.dumps(
            {
                "type": "error",
                "error": {
                    "name": "APIError",
                    "data": {
                        "message": "You must provide an API key.",
                        "statusCode": 401,
                    },
                },
            }
        )
        summary = wrapper._summarize_opencode_stdout_errors(line + "\n")
        self.assertIn("401", summary or "")
        self.assertIn("API key", summary or "")

    def test_summarize_opencode_stdout_errors_none_for_result_only(self) -> None:
        line = json.dumps({"type": "result", "ok": True})
        self.assertIsNone(wrapper._summarize_opencode_stdout_errors(line))

    def test_ensure_nonempty_patch_allows_empty_without_stream_error(self) -> None:
        with patch.object(wrapper, "fatal_error") as mock_fatal:
            wrapper._ensure_nonempty_patch_or_exit(
                patch="   ",
                return_code=0,
                patch_source="no_patch_found",
                stdout='{"type":"result"}\n',
                stderr="",
                command=["opencode", "run"],
                workspace=Path("/tmp/ws"),
                raw_stdout_path="/tmp/out.jsonl",
                raw_stderr_path="/tmp/err.log",
            )
        mock_fatal.assert_not_called()

    def test_ensure_nonempty_patch_fatal_prefers_api_error_summary(self) -> None:
        err_line = json.dumps(
            {
                "type": "error",
                "error": {
                    "name": "APIError",
                    "data": {"message": "Unauthorized", "statusCode": 401},
                },
            }
        )
        with patch.object(wrapper, "fatal_error", side_effect=RuntimeError("fatal")) as mock_fatal:
            with self.assertRaises(RuntimeError):
                wrapper._ensure_nonempty_patch_or_exit(
                    patch="",
                    return_code=0,
                    patch_source="no_patch_found",
                    stdout=err_line + "\n",
                    stderr="",
                    command=["opencode", "run"],
                    workspace=Path("/tmp/ws"),
                    raw_stdout_path="/tmp/out.jsonl",
                    raw_stderr_path="/tmp/err.log",
                )
        mock_fatal.assert_called_once()
        args, kwargs = mock_fatal.call_args
        self.assertIn("API error", args[0])
        self.assertIn("401", kwargs["details"]["error_summary"])
        self.assertIn("Unauthorized", kwargs["details"]["error_summary"])

    def test_ensure_nonempty_patch_noop_when_patch_nonempty(self) -> None:
        with patch.object(wrapper, "fatal_error") as mock_fatal:
            wrapper._ensure_nonempty_patch_or_exit(
                patch="diff --git a/x b/x\n",
                return_code=0,
                patch_source="diff_header",
                stdout="",
                stderr="",
                command=["opencode"],
                workspace=Path("/tmp/ws"),
                raw_stdout_path=None,
                raw_stderr_path=None,
            )
        mock_fatal.assert_not_called()


if __name__ == "__main__":
    unittest.main()
