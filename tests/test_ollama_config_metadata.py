from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from benchkit.common.config import AgentConfig, EvaluationConfig, ModelConfig, RunConfig
from benchkit.swebench.agents.opencode.config import (
    build_ollama_provider_run_metadata,
    format_ollama_provider_plan_lines,
)
from benchkit.swebench.agents.opencode.runtime import default_ollama_json_path
from benchkit.swebench.runner import _build_manifest


class OllamaConfigMetadataTests(unittest.TestCase):
    def test_repo_ollama_json_contains_expected_defaults(self) -> None:
        path = default_ollama_json_path()
        self.assertEqual(path.name, "ollama.json")
        self.assertEqual(path.parent.name, "opencode")
        data = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(data["base_url"], "http://localhost:11434")
        self.assertEqual(data["timeout_seconds"], 900)
        self.assertEqual(data["max_num_predict"], 4096)
        self.assertEqual(data["options"]["temperature"], 0)
        self.assertEqual(data["options"]["seed"], 42)

    def test_build_metadata_reads_temp_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "ollama.json"
            p.write_text(
                json.dumps(
                    {
                        "base_url": "http://localhost:9999",
                        "timeout_seconds": 1200,
                        "max_num_predict": 2048,
                        "options": {
                            "temperature": 0,
                            "seed": 42,
                            "num_predict": 2048,
                        },
                    }
                ),
                encoding="utf-8",
            )
            meta = build_ollama_provider_run_metadata(
                config=_build_ollama_config(Path(tmp)),
                resolved_model_name="ollama/deepseek-v4-flash:cloud",
                ollama_json_path=p,
            )
        self.assertEqual(meta.get("config_source"), "repo_json")
        self.assertIn("config_sha256", meta)
        self.assertEqual(meta.get("base_url"), "http://localhost:9999")
        self.assertEqual(meta.get("model"), "ollama/deepseek-v4-flash:cloud")
        self.assertEqual(meta.get("provider_model_id"), "deepseek-v4-flash:cloud")
        self.assertEqual(meta.get("openai_compatible_base_url"), "http://localhost:9999/v1")

    def test_format_plan_lines_includes_error(self) -> None:
        lines = format_ollama_provider_plan_lines({"config_path": "/x", "error": "file_not_found"})
        self.assertTrue(any("error" in line for line in lines))

    def test_build_manifest_includes_ollama_provider_when_model_provider_is_ollama(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cfg = _build_ollama_config(root)
            manifest = _build_manifest(
                config=cfg,
                agent_id="noop",
                model_resolution={
                    "canonical_name": "deepseek-v4-flash:cloud",
                    "resolved_name": "ollama/deepseek-v4-flash:cloud",
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
        self.assertIn("ollama", manifest)
        self.assertEqual(manifest["ollama"].get("config_source"), "repo_json")
        self.assertIn("config_sha256", manifest["ollama"])
        self.assertEqual(manifest["ollama"].get("model"), "ollama/deepseek-v4-flash:cloud")
        self.assertEqual(manifest["ollama"].get("provider_model_id"), "deepseek-v4-flash:cloud")
        self.assertEqual(manifest["ollama"].get("openai_compatible_base_url"), "http://localhost:11434/v1")


def _build_ollama_config(root: Path) -> RunConfig:
    return RunConfig(
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
        model=ModelConfig(provider="ollama", name="deepseek-v4-flash:cloud"),
        prompt_context=None,
        model_map={},
        evaluation=EvaluationConfig(enabled=False),
        source_path=root / "c.toml",
    )
