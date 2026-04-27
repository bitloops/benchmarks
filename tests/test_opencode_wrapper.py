from __future__ import annotations

from pathlib import Path
import json
import importlib.util
import sys
import tempfile
import unittest
from unittest.mock import patch


def _load_wrapper_module():
    module_path = (
        Path(__file__).resolve().parents[1] / "scripts" / "agents" / "opencode_wrapper.py"
    )
    sys.path.insert(0, str(module_path.parent))
    try:
        spec = importlib.util.spec_from_file_location("opencode_wrapper", module_path)
        if spec is None or spec.loader is None:
            raise RuntimeError("Failed to load scripts/agents/opencode_wrapper.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.pop(0)


wrapper = _load_wrapper_module()


class OpencodeWrapperTests(unittest.TestCase):
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
        self.assertIn("tool invocations", message.lower())

    def test_resolve_missing_tool_capture_error_allows_captured_tools(self) -> None:
        payload = {"run": {"condition": "with_bitloops"}}

        message = wrapper._resolve_missing_tool_capture_error(
            payload=payload,
            tool_invocations_raw=[{"tool": "Bash"}],
            tool_usage_breakdown={},
        )

        self.assertIsNone(message)

    def test_normalize_model_reference_rewrites_legacy_fireworks_prefix(self) -> None:
        self.assertEqual(
            wrapper._normalize_opencode_model_reference(
                "fireworks/accounts/fireworks/models/qwen3p6-plus"
            ),
            "fireworks-ai/accounts/fireworks/models/qwen3p6-plus",
        )

    def test_build_runtime_config_normalizes_legacy_fireworks_provider_alias(self) -> None:
        payload = {
            "model": {
                "provider": "fireworks",
                "temperature": 0.2,
                "seed": 4242,
            }
        }

        config = wrapper._build_opencode_runtime_config(
            payload=payload,
            resolved_model_name="fireworks/accounts/firefunction-v2",
        )

        self.assertEqual(
            config,
            {
                "$schema": "https://opencode.ai/config.json",
                "provider": {
                    "fireworks-ai": {
                        "models": {
                            "accounts/firefunction-v2": {
                                "options": {
                                    "temperature": 0.2,
                                    "seed": 4242,
                                }
                            }
                        }
                    }
                },
            },
        )

    def test_build_runtime_config_uses_payload_provider_when_model_name_has_no_prefix(self) -> None:
        payload = {
            "model": {
                "provider": "openai",
                "temperature": 0.0,
                "seed": 7,
            }
        }

        config = wrapper._build_opencode_runtime_config(
            payload=payload,
            resolved_model_name="gpt-5",
        )

        self.assertEqual(
            config,
            {
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
            },
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

    def test_build_invocation_config_merges_existing_repo_and_runtime_layers(self) -> None:
        runtime_config = {
            "$schema": "https://opencode.ai/config.json",
            "provider": {
                "fireworks-ai": {
                    "models": {
                        "accounts/fireworks/models/qwen3p6-plus": {
                            "options": {
                                "temperature": 0.0,
                                "seed": 7,
                            }
                        }
                    }
                }
            },
        }

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
                runtime_config=runtime_config,
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
                "temperature": 0.0,
                "seed": 7,
                "max_tokens": 32768,
            },
        )

    def test_build_runtime_config_returns_none_when_no_overrides_present(self) -> None:
        payload = {"model": {"provider": "openai"}}
        config = wrapper._build_opencode_runtime_config(
            payload=payload,
            resolved_model_name="openai/gpt-5",
        )
        self.assertIsNone(config)

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


if __name__ == "__main__":
    unittest.main()
