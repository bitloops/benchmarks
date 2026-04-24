from __future__ import annotations

from pathlib import Path
import json
import importlib.util
import sys
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
    def test_build_runtime_config_includes_temperature_and_seed(self) -> None:
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
                    "fireworks": {
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


if __name__ == "__main__":
    unittest.main()
