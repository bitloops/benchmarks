from __future__ import annotations

from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from benchkit.common.config import (
    AgentConfig,
    EvaluationConfig,
    ModelConfig,
    RunConfig,
)
from benchkit.common.io import read_json, read_jsonl, write_jsonl
from benchkit.swebench.runner import execute_run
from benchkit.swebench.types import AgentResult, BenchmarkInstance


class _RecordingAdapter:
    def __init__(self, delay_seconds: float = 0.08) -> None:
        self.delay_seconds = delay_seconds
        self._lock = threading.Lock()
        self.active_calls = 0
        self.max_active_calls = 0

    def generate_patch(self, instance: BenchmarkInstance, context: object) -> AgentResult:
        _ = context
        with self._lock:
            self.active_calls += 1
            self.max_active_calls = max(self.max_active_calls, self.active_calls)
        try:
            time.sleep(self.delay_seconds)
            return AgentResult(
                patch=(
                    f"diff --git a/{instance.instance_id}.txt b/{instance.instance_id}.txt\n"
                    "--- a/file.txt\n"
                    "+++ b/file.txt\n"
                    "@@ -1 +1 @@\n"
                    "-old\n"
                    "+new\n"
                ),
                metadata={"elapsed_ms": int(self.delay_seconds * 1000)},
            )
        finally:
            with self._lock:
                self.active_calls -= 1


class RunnerTests(unittest.TestCase):
    def test_execute_run_parallelizes_instances_when_max_workers_gt_one(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config = _make_config(root, max_workers=3)
            adapter = _RecordingAdapter()

            with patch("benchkit.swebench.runner.build_agent_adapter", return_value=adapter):
                result = execute_run(config)

            self.assertEqual(result.total_instances, 4)
            self.assertGreaterEqual(adapter.max_active_calls, 2)
            self.assertEqual(len(read_jsonl(result.prediction_files[0])), 4)
            summary = read_json(result.run_root / "summary.json")
            self.assertEqual(summary["max_workers"], 3)
            self.assertEqual(summary["workspace_isolation_mode"], "shared_repo_commit")
            self.assertEqual(summary["bitloops_sandbox_mode"], "disabled")

    def test_execute_run_respects_cli_max_workers_override(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config = _make_config(root, max_workers=4)
            adapter = _RecordingAdapter()

            with patch("benchkit.swebench.runner.build_agent_adapter", return_value=adapter):
                result = execute_run(config, max_workers=1)

            self.assertEqual(adapter.max_active_calls, 1)
            manifest = read_json(result.run_root / "run_manifest.json")
            summary = read_json(result.run_root / "summary.json")
            self.assertEqual(manifest["max_workers"], 1)
            self.assertEqual(summary["max_workers"], 1)
            self.assertEqual(manifest["workspace"]["isolation_mode"], "shared_repo_commit")
            self.assertEqual(manifest["bitloops_sandbox_mode"], "disabled")
            self.assertEqual(manifest["model"]["seed"], 1234)

    def test_execute_run_records_task_scoped_isolation_for_bitloops(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config = _make_config(root, max_workers=2, condition="with_bitloops")
            adapter = _RecordingAdapter()

            with patch("benchkit.swebench.runner.build_agent_adapter", return_value=adapter):
                result = execute_run(config)

            manifest = read_json(result.run_root / "run_manifest.json")
            summary = read_json(result.run_root / "summary.json")
            self.assertTrue(manifest["bitloops_enabled"])
            self.assertEqual(manifest["workspace"]["isolation_mode"], "task_scoped")
            self.assertEqual(summary["workspace_isolation_mode"], "task_scoped")
            self.assertEqual(manifest["bitloops_sandbox_mode"], "per_task_daemon")
            trace_rows = read_jsonl(result.trace_files[0])
            sandbox = trace_rows[0]["metadata"]["bitloops_sandbox"]
            self.assertEqual(sandbox["mode"], "per_task_daemon")
            self.assertTrue(sandbox["home_root"].endswith("/home"))
            self.assertIn("__bitloops", sandbox["sandbox_root"])


def _make_config(root: Path, max_workers: int, condition: str = "baseline") -> RunConfig:
    dataset_path = root / "dataset.jsonl"
    source_path = root / "config.toml"
    output_root = root / "runs"
    source_path.write_text("# test config\n", encoding="utf-8")

    instances = [
        BenchmarkInstance(
            instance_id=f"tokio__{idx}",
            repo="tokio-rs/tokio",
            base_commit=f"deadbeef{idx:032d}"[:40],
            problem_statement=f"Problem {idx}",
            language="rust",
            metadata={},
        ).to_row()
        for idx in range(1, 5)
    ]
    write_jsonl(dataset_path, instances)

    return RunConfig(
        benchmark="swebench_multilingual",
        dataset_path=dataset_path,
        split="test",
        language=None,
        condition=condition,
        include_repos=[],
        include_instance_ids=[],
        max_instances=None,
        attempts=1,
        max_workers=max_workers,
        timeout_seconds=60,
        output_root=output_root,
        prepare_workspace=False,
        workspace_isolation_mode="task_scoped" if condition == "with_bitloops" else "shared_repo_commit",
        bitloops_enabled=condition == "with_bitloops",
        bitloops_sandbox_mode="per_task_daemon" if condition == "with_bitloops" else "disabled",
        repo_url_template="https://github.com/{repo}.git",
        git_bin="git",
        workspace_root=None,
        workspace_timeout_seconds=60,
        agent=AgentConfig(id="noop", command=[], extra_args=[]),
        model=ModelConfig(provider="test", name="test-model", seed=1234),
        prompt_context=None,
        model_map={},
        evaluation=EvaluationConfig(enabled=False),
        source_path=source_path,
    )


if __name__ == "__main__":
    unittest.main()
