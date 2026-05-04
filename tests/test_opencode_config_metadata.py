from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from benchkit.common.config import AgentConfig, EvaluationConfig, ModelConfig, RunConfig
from benchkit.swebench.agents.opencode.config import (
    build_opencode_run_metadata,
    default_opencode_json_path,
    format_opencode_plan_lines,
)
from benchkit.swebench.runner import _build_manifest


class OpencodeConfigMetadataTests(unittest.TestCase):
    def test_repo_opencode_json_stays_minimal_and_canonical(self) -> None:
        path = default_opencode_json_path()
        data = json.loads(path.read_text(encoding="utf-8"))

        self.assertEqual(
            data["model"],
            "fireworks-ai/accounts/vasilis-bitloops/deployments/d5hkfhu7",
        )
        self.assertEqual(data["small_model"], data["model"])
        self.assertEqual(set(data["agent"]), {"build", "plan"})
        self.assertEqual(set(data["provider"]), {"fireworks-ai"})

        for agent_name in ("build", "plan"):
            agent = data["agent"][agent_name]
            self.assertEqual(agent["temperature"], 0)
            self.assertEqual(agent["seed"], 42)
            self.assertEqual(agent["max_tokens"], 32768)
            self.assertEqual(agent["permission"]["task"]["*"], "deny")

        model_options = data["provider"]["fireworks-ai"]["models"][
            "accounts/vasilis-bitloops/deployments/d5hkfhu7"
        ]["options"]
        self.assertEqual(
            model_options,
            {
                "temperature": 0,
                "seed": 42,
                "max_tokens": 32768,
            },
        )

    def test_build_metadata_reads_temp_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "opencode.json"
            p.write_text(
                json.dumps(
                    {
                        "model": "openai/gpt-5",
                        "agent": {
                            "build": {"temperature": 0, "seed": 99, "max_tokens": 1000},
                            "plan": {"temperature": 0, "seed": 100, "max_tokens": 2000},
                        },
                    }
                ),
                encoding="utf-8",
            )
            meta = build_opencode_run_metadata(opencode_json_path=p)
        self.assertEqual(meta.get("sampling_source"), "repo_json")
        self.assertIn("config_sha256", meta)
        self.assertEqual(meta.get("declared_model"), "openai/gpt-5")
        self.assertEqual(meta.get("agent_build_sampling"), {"temperature": 0, "seed": 99, "max_tokens": 1000})
        self.assertEqual(meta.get("agent_plan_sampling"), {"temperature": 0, "seed": 100, "max_tokens": 2000})

    def test_build_metadata_missing_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "missing.json"
            meta = build_opencode_run_metadata(opencode_json_path=p)
        self.assertEqual(meta.get("error"), "file_not_found")

    def test_format_plan_lines_includes_error(self) -> None:
        lines = format_opencode_plan_lines({"config_path": "/x", "error": "file_not_found"})
        self.assertTrue(any("error" in line for line in lines))

    def test_build_manifest_includes_opencode_when_config_agent_opencode(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cfg = RunConfig(
                config_mode=None,
                benchmark="swebench_multilingual",
                dataset_path=root / "d.jsonl",
                split="test",
                language=None,
                condition="baseline",
                include_repos=[],
                include_instance_ids=[],
                max_instances=None,
                attempts=1,
                max_workers=1,
                timeout_seconds=60,
                output_root=root / "runs",
                prepare_workspace=False,
                workspace_isolation_mode="shared_repo_commit",
                bitloops_enabled=False,
                bitloops_sandbox_mode="disabled",
                repo_url_template="https://github.com/{repo}.git",
                git_bin="git",
                workspace_root=None,
                workspace_timeout_seconds=60,
                agent=AgentConfig(id="opencode", command=["python3", "w.py"], extra_args=[]),
                model=ModelConfig(provider="openai", name="gpt-5"),
                prompt_context=None,
                model_map={},
                evaluation=EvaluationConfig(enabled=False),
                source_path=root / "c.toml",
            )
            manifest = _build_manifest(
                config=cfg,
                agent_id="noop",
                model_resolution={
                    "canonical_name": "gpt-5",
                    "resolved_name": "openai/gpt-5",
                    "map_key": "opencode",
                    "source": "test",
                    "agent_id": "opencode",
                },
                run_id="rid",
                total_instances=1,
                attempts=1,
                max_workers=1,
                workspace_isolation_mode="shared_repo_commit",
                parallel_attempts_enabled=False,
                dry_run=True,
                started_at="2020-01-01T00:00:00+00:00",
            )
        self.assertIn("opencode", manifest)
        self.assertEqual(manifest["opencode"].get("sampling_source"), "repo_json")
        self.assertIn("config_sha256", manifest["opencode"])


if __name__ == "__main__":
    unittest.main()
