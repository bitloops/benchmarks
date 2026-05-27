from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from benchkit.common.config import AgentConfig, EvaluationConfig, ModelConfig, RunConfig
from benchkit.swebench.agents.codex.config import (
    build_codex_run_metadata,
    default_codex_json_path,
    format_codex_plan_lines,
)
from benchkit.swebench.runner import _build_manifest


class CodexConfigMetadataTests(unittest.TestCase):
    def test_repo_codex_json_contains_expected_defaults(self) -> None:
        path = default_codex_json_path()
        data = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(data["model"], "gpt-5.4")
        self.assertEqual(data["model_reasoning_effort"], "high")
        self.assertEqual(data["model_verbosity"], "low")
        self.assertEqual(data["model_reasoning_summary"], "none")
        self.assertEqual(data["timeout_seconds"], 1200)
        self.assertEqual(data["full_auto"], True)

    def test_build_metadata_reads_temp_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "codex.json"
            p.write_text(
                json.dumps(
                    {
                        "model": "gpt-5.4",
                        "model_reasoning_effort": "high",
                        "timeout_seconds": 1200,
                    }
                ),
                encoding="utf-8",
            )
            meta = build_codex_run_metadata(codex_json_path=p)
        self.assertEqual(meta.get("config_source"), "repo_json")
        self.assertIn("config_sha256", meta)
        self.assertEqual(meta.get("model"), "gpt-5.4")
        self.assertEqual(meta.get("model_reasoning_effort"), "high")
        self.assertEqual(meta.get("timeout_seconds"), 1200)

    def test_build_metadata_missing_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "missing.json"
            meta = build_codex_run_metadata(codex_json_path=p)
        self.assertEqual(meta.get("error"), "file_not_found")

    def test_format_plan_lines_includes_error(self) -> None:
        lines = format_codex_plan_lines({"config_path": "/x", "error": "file_not_found"})
        self.assertTrue(any("error" in line for line in lines))

    def test_build_manifest_includes_codex_when_config_agent_codex(self) -> None:
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
                artifact_retention_policy="appendix_summary",
                repo_url_template="https://github.com/{repo}.git",
                git_bin="git",
                workspace_root=None,
                workspace_timeout_seconds=60,
                agent=AgentConfig(id="codex", command=["python3", "w.py"], extra_args=[]),
                model=ModelConfig(provider="openai", name="gpt-5.4"),
                prompt_context=None,
                model_map={},
                evaluation=EvaluationConfig(enabled=False),
                source_path=root / "c.toml",
            )
            manifest = _build_manifest(
                config=cfg,
                agent_id="noop",
                model_resolution={
                    "canonical_name": "gpt-5.4",
                    "resolved_name": "gpt-5.4",
                    "map_key": "codex",
                    "source": "test",
                    "agent_id": "codex",
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
        self.assertIn("codex", manifest)
        self.assertEqual(manifest["codex"].get("config_source"), "repo_json")
        self.assertIn("config_sha256", manifest["codex"])


if __name__ == "__main__":
    unittest.main()
