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


if __name__ == "__main__":
    unittest.main()
