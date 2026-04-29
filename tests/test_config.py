from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from benchkit.common.config import load_run_config


class ConfigTests(unittest.TestCase):
    def test_load_run_config(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            instance_ids_file = temp_root / "ids.txt"
            instance_ids_file.write_text("tokio__a\n# comment\ntokio__b\n", encoding="utf-8")

            raw = f"""
[run]
benchmark = "swebench_multilingual"
dataset_path = "datasets/sample.jsonl"
language = "rust"
condition = "baseline"
include_repos = ["tokio-rs/tokio"]
include_instance_ids = ["tokio__a"]
instance_ids_file = "{instance_ids_file.name}"
attempts = 2
max_workers = 3
timeout_seconds = 60
prepare_workspace = true
repo_url_template = "https://github.com/{{repo}}.git"
workspace_timeout_seconds = 120

[agent]
id = "noop"

[model]
provider = "anthropic"
name = "opus-4-6"

[model_map.claude_code]
"opus-4-6" = "claude-opus-4-6"

[evaluation]
enabled = true
python_bin = "python3"
dataset_name = "SWE-bench/SWE-bench_Multilingual"
split = "dev"
max_workers = 8
timeout_seconds = 3600
            """.strip()
            path = temp_root / "config.toml"
            path.write_text(raw, encoding="utf-8")
            cfg = load_run_config(path)
            self.assertIsNone(cfg.config_mode)
            self.assertEqual(cfg.benchmark, "swebench_multilingual")
            self.assertEqual(cfg.language, "rust")
            self.assertEqual(cfg.condition, "baseline")
            self.assertEqual(cfg.attempts, 2)
            self.assertEqual(cfg.max_workers, 3)
            self.assertEqual(cfg.agent.id, "noop")
            self.assertEqual(cfg.model.name, "opus-4-6")
            self.assertEqual(cfg.include_repos, ["tokio-rs/tokio"])
            self.assertEqual(cfg.include_instance_ids, ["tokio__a", "tokio__b"])
            self.assertTrue(cfg.prepare_workspace)
            self.assertEqual(cfg.workspace_isolation_mode, "shared_repo_commit")
            self.assertFalse(cfg.bitloops_enabled)
            self.assertEqual(cfg.bitloops_sandbox_mode, "disabled")
            self.assertEqual(
                cfg.model_map["claude_code"]["opus-4-6"],
                "claude-opus-4-6",
            )
            self.assertTrue(cfg.evaluation.enabled)
            self.assertEqual(cfg.evaluation.dataset_name, "SWE-bench/SWE-bench_Multilingual")
            self.assertEqual(cfg.evaluation.max_workers, 8)

    def test_load_run_config_enables_task_scoped_isolation_for_bitloops(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            raw = """
[run]
benchmark = "swebench_multilingual"
dataset_path = "datasets/sample.jsonl"
condition = "with_bitloops"

[agent]
id = "claude_code"
extra_args = ["--bitloops-init"]

[model]
provider = "anthropic"
name = "opus-4-6"
            """.strip()
            path = temp_root / "config.toml"
            path.write_text(raw, encoding="utf-8")

            cfg = load_run_config(path)

            self.assertTrue(cfg.bitloops_enabled)
            self.assertEqual(cfg.workspace_isolation_mode, "task_scoped")
            self.assertEqual(cfg.bitloops_sandbox_mode, "per_task_daemon")

    def test_load_run_config_supports_opencode_agent_and_model_map(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            raw = """
[run]
benchmark = "swebench_multilingual"
dataset_path = "datasets/sample.jsonl"
condition = "baseline"

[agent]
id = "opencode"
command = ["python3", "scripts/agents/opencode_wrapper.py"]

[model]
provider = "openai"
name = "gpt-5"
temperature = 0.15
seed = 4242

[model_map.opencode]
"gpt-5" = "openai/gpt-5"
            """.strip()
            path = temp_root / "config.toml"
            path.write_text(raw, encoding="utf-8")

            cfg = load_run_config(path)

            self.assertEqual(cfg.agent.id, "opencode")
            self.assertEqual(
                cfg.agent.command,
                ["python3", "scripts/agents/opencode_wrapper.py"],
            )
            self.assertEqual(cfg.model.provider, "openai")
            self.assertEqual(cfg.model.name, "gpt-5")
            self.assertEqual(cfg.model.temperature, 0.15)
            self.assertEqual(cfg.model.seed, 4242)
            self.assertEqual(cfg.model_map["opencode"]["gpt-5"], "openai/gpt-5")

    def test_load_run_config_applies_baseline_mode_overlay(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            path = temp_root / "config.toml"
            path.write_text(_mode_config_text(), encoding="utf-8")

            cfg = load_run_config(path, mode="baseline")

            self.assertEqual(cfg.config_mode, "baseline")
            self.assertEqual(cfg.condition, "baseline")
            self.assertEqual(cfg.timeout_seconds, 900)
            self.assertEqual(cfg.agent.extra_args, [])
            self.assertFalse(cfg.bitloops_enabled)
            self.assertEqual(cfg.bitloops_sandbox_mode, "disabled")

    def test_load_run_config_applies_with_bitloops_mode_overlay(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            path = temp_root / "config.toml"
            path.write_text(_mode_config_text(), encoding="utf-8")

            cfg = load_run_config(path, mode="with_bitloops")

            self.assertEqual(cfg.config_mode, "with_bitloops")
            self.assertEqual(cfg.condition, "with_bitloops")
            self.assertEqual(cfg.timeout_seconds, 1500)
            self.assertEqual(cfg.workspace_timeout_seconds, 1800)
            self.assertEqual(cfg.bitloops_sandbox_mode, "per_task_daemon")
            self.assertEqual(
                cfg.agent.extra_args,
                ["--bitloops-init", "--bitloops-embeddings-runtime", "platform"],
            )
            self.assertTrue(cfg.bitloops_enabled)

    def test_load_run_config_unknown_mode_lists_available_modes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            path = temp_root / "config.toml"
            path.write_text(_mode_config_text(), encoding="utf-8")

            with self.assertRaisesRegex(
                ValueError,
                "Unknown config mode 'missing'.*baseline.*with_bitloops",
            ):
                load_run_config(path, mode="missing")

    def test_load_run_config_applies_codex_preset_baseline(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            path = temp_root / "config.toml"
            path.write_text(
                """
preset = "codex"

[run]
dataset_path = "datasets/sample.jsonl"
include_instance_ids = ["tokio__a"]
max_instances = 1

[model]
name = "gpt-5.4"
                """.strip(),
                encoding="utf-8",
            )

            cfg = load_run_config(path, mode="baseline")

            self.assertEqual(cfg.config_mode, "baseline")
            self.assertEqual(cfg.benchmark, "swebench_multilingual")
            self.assertEqual(cfg.split, "test")
            self.assertEqual(cfg.language, "rust")
            self.assertEqual(cfg.condition, "baseline")
            self.assertEqual(cfg.agent.id, "codex")
            self.assertEqual(
                cfg.agent.command,
                ["python3", "scripts/agents/codex_wrapper.py"],
            )
            self.assertEqual(cfg.agent.extra_args, [])
            self.assertEqual(cfg.model.provider, "openai")
            self.assertEqual(cfg.model.name, "gpt-5.4")
            self.assertTrue(cfg.prepare_workspace)
            self.assertEqual(cfg.repo_url_template, "https://github.com/{repo}.git")
            self.assertFalse(cfg.bitloops_enabled)
            self.assertEqual(cfg.bitloops_sandbox_mode, "disabled")
            self.assertTrue(cfg.evaluation.enabled)
            self.assertEqual(cfg.evaluation.python_bin, "./.venv/bin/python")
            self.assertEqual(cfg.evaluation.split, "test")

    def test_load_run_config_applies_codex_preset_with_bitloops(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            path = temp_root / "config.toml"
            path.write_text(
                """
preset = "codex"

[run]
dataset_path = "datasets/sample.jsonl"
timeout_seconds = 1800

[model]
name = "gpt-5.4"
                """.strip(),
                encoding="utf-8",
            )

            cfg = load_run_config(path, mode="with_bitloops")

            self.assertEqual(cfg.config_mode, "with_bitloops")
            self.assertEqual(cfg.condition, "with_bitloops")
            self.assertEqual(cfg.timeout_seconds, 1800)
            self.assertEqual(cfg.agent.extra_args, ["--bitloops-init"])
            self.assertTrue(cfg.bitloops_enabled)
            self.assertEqual(cfg.workspace_isolation_mode, "task_scoped")
            self.assertEqual(cfg.bitloops_sandbox_mode, "per_task_daemon")

    def test_load_run_config_applies_opencode_preset_with_bitloops(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            path = temp_root / "config.toml"
            path.write_text(
                """
preset = "opencode"

[run]
dataset_path = "datasets/sample.jsonl"
max_workers = 1

[model]
name = "deepseek-v4-pro"

[model_map.opencode]
"deepseek-v4-pro" = "fireworks-ai/accounts/example/deployments/model"
                """.strip(),
                encoding="utf-8",
            )

            cfg = load_run_config(path, mode="with_bitloops")

            self.assertEqual(cfg.config_mode, "with_bitloops")
            self.assertEqual(cfg.agent.id, "opencode")
            self.assertEqual(
                cfg.agent.command,
                ["python3", "scripts/agents/opencode_wrapper.py"],
            )
            self.assertEqual(cfg.model.provider, "fireworks-ai")
            self.assertEqual(cfg.timeout_seconds, 1500)
            self.assertEqual(cfg.workspace_timeout_seconds, 1800)
            self.assertEqual(cfg.bitloops_sandbox_mode, "per_task_daemon")
            self.assertEqual(
                cfg.agent.extra_args,
                [
                    "--bitloops-init",
                    "--bitloops-embeddings-runtime",
                    "platform",
                    "--bitloops-summary-mode",
                    "off",
                ],
            )
            self.assertEqual(
                cfg.model_map["opencode"]["deepseek-v4-pro"],
                "fireworks-ai/accounts/example/deployments/model",
            )

    def test_load_run_config_unknown_preset_lists_available_presets(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            path = temp_root / "config.toml"
            path.write_text('preset = "missing"', encoding="utf-8")

            with self.assertRaisesRegex(
                ValueError,
                "Unknown config preset 'missing'.*codex.*opencode",
            ):
                load_run_config(path)

def _mode_config_text() -> str:
    return """
[run]
benchmark = "swebench_multilingual"
dataset_path = "datasets/sample.jsonl"
include_repos = []
include_instance_ids = ["tokio__a"]
attempts = 1
max_workers = 1
output_root = "runs"
prepare_workspace = true
repo_url_template = "https://github.com/{repo}.git"
git_bin = "git"

[agent]
id = "opencode"
command = ["python3", "scripts/agents/opencode_wrapper.py"]
extra_args = ["base-arg"]

[model]
provider = "fireworks-ai"
name = "deepseek-v4-pro"

[model_map.opencode]
"deepseek-v4-pro" = "fireworks-ai/accounts/example/deployments/model"

[modes.baseline.run]
condition = "baseline"
timeout_seconds = 900
bitloops_sandbox_mode = "disabled"

[modes.baseline.agent]
extra_args = []

[modes.with_bitloops.run]
condition = "with_bitloops"
timeout_seconds = 1500
workspace_timeout_seconds = 1800
bitloops_sandbox_mode = "per_task_daemon"

[modes.with_bitloops.agent]
extra_args = ["--bitloops-init", "--bitloops-embeddings-runtime", "platform"]
    """.strip()


if __name__ == "__main__":
    unittest.main()
