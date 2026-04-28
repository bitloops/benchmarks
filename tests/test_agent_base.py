from __future__ import annotations

from pathlib import Path
import json
import tempfile
import unittest
from unittest.mock import patch

from benchkit.common.config import AgentConfig, ModelConfig
from benchkit.swebench.agents.base import (
    JsonCommandAgentAdapter,
    RunContext,
    _heartbeat_interval_seconds,
    _resolve_relative_command_paths,
    _summarize_failed_adapter_stdout,
)
from benchkit.swebench.types import BenchmarkInstance


class AgentBaseTests(unittest.TestCase):
    def test_json_command_adapter_extends_wrapper_timeout_for_bitloops_runs(self) -> None:
        adapter = JsonCommandAgentAdapter(
            AgentConfig(
                id="opencode",
                command=["python3", "wrapper.py"],
                extra_args=["--bitloops-init"],
            )
        )
        instance = BenchmarkInstance(
            instance_id="tokio__1",
            repo="tokio-rs/tokio",
            base_commit="abc123",
            problem_statement="Fix the failing behavior.",
            language="rust",
            metadata={},
        )
        context = RunContext(
            attempt=1,
            timeout_seconds=900,
            workspace_root=Path("/tmp/workspace"),
            model=ModelConfig(provider="openai", name="openai/gpt-5"),
            canonical_model_name="gpt-5",
            run_id="run-123",
            benchmark="swebench_multilingual",
            condition="with_bitloops",
        )
        captured_timeout: int | None = None

        def fake_run(*args, **kwargs):
            del args
            nonlocal captured_timeout
            captured_timeout = kwargs["timeout"]

            class Completed:
                returncode = 0
                stdout = '{"patch":"","metadata":{}}'
                stderr = ""

            return Completed()

        with patch("benchkit.swebench.agents.base.subprocess.run", side_effect=fake_run):
            adapter.generate_patch(instance, context)

        self.assertEqual(captured_timeout, 2400)

    def test_json_command_adapter_keeps_baseline_wrapper_timeout_unchanged(self) -> None:
        adapter = JsonCommandAgentAdapter(
            AgentConfig(id="opencode", command=["python3", "wrapper.py"])
        )
        instance = BenchmarkInstance(
            instance_id="tokio__1",
            repo="tokio-rs/tokio",
            base_commit="abc123",
            problem_statement="Fix the failing behavior.",
            language="rust",
            metadata={},
        )
        context = RunContext(
            attempt=1,
            timeout_seconds=900,
            workspace_root=Path("/tmp/workspace"),
            model=ModelConfig(provider="openai", name="openai/gpt-5"),
            canonical_model_name="gpt-5",
            run_id="run-123",
            benchmark="swebench_multilingual",
            condition="baseline",
        )
        captured_timeout: int | None = None

        def fake_run(*args, **kwargs):
            del args
            nonlocal captured_timeout
            captured_timeout = kwargs["timeout"]

            class Completed:
                returncode = 0
                stdout = '{"patch":"","metadata":{}}'
                stderr = ""

            return Completed()

        with patch("benchkit.swebench.agents.base.subprocess.run", side_effect=fake_run):
            adapter.generate_patch(instance, context)

        self.assertEqual(captured_timeout, 900)

    def test_json_command_adapter_includes_run_timeout_in_payload(self) -> None:
        adapter = JsonCommandAgentAdapter(
            AgentConfig(id="claude_code", command=["python3", "wrapper.py"])
        )
        instance = BenchmarkInstance(
            instance_id="ruff__1",
            repo="astral-sh/ruff",
            base_commit="abc123",
            problem_statement="Fix the failing lint behavior.",
            language="rust",
            metadata={},
        )
        context = RunContext(
            attempt=2,
            timeout_seconds=3600,
            workspace_root=Path("/tmp/workspace"),
            model=ModelConfig(provider="anthropic", name="opus-4-6"),
            canonical_model_name="opus-4-6",
            run_id="run-123",
            benchmark="swebench_multilingual",
            condition="with_bitloops",
        )
        captured_payload: dict[str, object] = {}

        def fake_run(*args, **kwargs):
            del args
            captured_payload.update(json.loads(kwargs["input"]))

            class Completed:
                returncode = 0
                stdout = '{"patch":"","metadata":{}}'
                stderr = ""

            return Completed()

        with patch("benchkit.swebench.agents.base.subprocess.run", side_effect=fake_run):
            adapter.generate_patch(instance, context)

        self.assertEqual(captured_payload["run"]["timeout_seconds"], 3600)

    def test_json_command_adapter_includes_optional_seed_in_payload(self) -> None:
        adapter = JsonCommandAgentAdapter(
            AgentConfig(id="opencode", command=["python3", "wrapper.py"])
        )
        instance = BenchmarkInstance(
            instance_id="tokio__1",
            repo="tokio-rs/tokio",
            base_commit="abc123",
            problem_statement="Fix the failing behavior.",
            language="rust",
            metadata={},
        )
        context = RunContext(
            attempt=1,
            timeout_seconds=900,
            workspace_root=Path("/tmp/workspace"),
            model=ModelConfig(provider="openai", name="openai/gpt-5", seed=1234),
            canonical_model_name="gpt-5",
            run_id="run-123",
            benchmark="swebench_multilingual",
            condition="baseline",
        )
        captured_payload: dict[str, object] = {}

        def fake_run(*args, **kwargs):
            del args
            captured_payload.update(json.loads(kwargs["input"]))

            class Completed:
                returncode = 0
                stdout = '{"patch":"","metadata":{}}'
                stderr = ""

            return Completed()

        with patch("benchkit.swebench.agents.base.subprocess.run", side_effect=fake_run):
            adapter.generate_patch(instance, context)

        self.assertEqual(captured_payload["model"]["seed"], 1234)

    def test_json_command_adapter_includes_attempt_dir_in_payload(self) -> None:
        adapter = JsonCommandAgentAdapter(
            AgentConfig(id="opencode", command=["python3", "wrapper.py"])
        )
        instance = BenchmarkInstance(
            instance_id="tokio__1",
            repo="tokio-rs/tokio",
            base_commit="abc123",
            problem_statement="Fix the failing behavior.",
            language="rust",
            metadata={},
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            attempt_dir = Path(temp_dir) / "attempt-01"
            context = RunContext(
                attempt=1,
                timeout_seconds=900,
                workspace_root=Path("/tmp/workspace"),
                attempt_dir=attempt_dir,
                model=ModelConfig(provider="openai", name="openai/gpt-5"),
                canonical_model_name="gpt-5",
                run_id="run-123",
                benchmark="swebench_multilingual",
                condition="with_bitloops",
            )
            captured_payload: dict[str, object] = {}

            def fake_run(*args, **kwargs):
                del args
                captured_payload.update(json.loads(kwargs["input"]))

                class Completed:
                    returncode = 0
                    stdout = '{"patch":"","metadata":{}}'
                    stderr = ""

                return Completed()

            with patch("benchkit.swebench.agents.base.subprocess.run", side_effect=fake_run):
                adapter.generate_patch(instance, context)

        self.assertEqual(captured_payload["run"]["attempt_dir"], str(attempt_dir.resolve()))

    def test_resolves_relative_script_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir)
            script_dir = base_dir / "scripts" / "agents"
            script_dir.mkdir(parents=True)
            script_path = script_dir / "wrapper.py"
            script_path.write_text("print('ok')\n", encoding="utf-8")

            command = ["python3", "scripts/agents/wrapper.py", "--flag"]
            resolved = _resolve_relative_command_paths(command, base_dir=base_dir)

            self.assertEqual(resolved[0], "python3")
            self.assertEqual(Path(resolved[1]).resolve(), script_path.resolve())
            self.assertEqual(resolved[2], "--flag")

    def test_keeps_non_path_tokens_unchanged(self) -> None:
        resolved = _resolve_relative_command_paths(
            ["claude", "--print", "hello"],
            base_dir=Path.cwd(),
        )
        self.assertEqual(resolved, ["claude", "--print", "hello"])

    def test_keeps_missing_relative_paths_unchanged(self) -> None:
        base_dir = Path.cwd()
        command = ["python3", "scripts/agents/missing_wrapper.py"]
        resolved = _resolve_relative_command_paths(command, base_dir=base_dir)
        self.assertEqual(resolved, command)

    def test_heartbeat_interval_defaults_to_20_seconds(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            self.assertEqual(_heartbeat_interval_seconds(), 20.0)

    def test_heartbeat_interval_can_be_disabled(self) -> None:
        with patch.dict("os.environ", {"BENCHKIT_AGENT_HEARTBEAT_SECONDS": "off"}):
            self.assertIsNone(_heartbeat_interval_seconds())

    def test_heartbeat_interval_uses_custom_positive_value(self) -> None:
        with patch.dict("os.environ", {"BENCHKIT_AGENT_HEARTBEAT_SECONDS": "7.5"}):
            self.assertEqual(_heartbeat_interval_seconds(), 7.5)

    def test_summarize_failed_adapter_stdout_prefers_json_error(self) -> None:
        stdout = '{"error":"wrapper failed","details":{"return_code":1}}'
        self.assertEqual(
            _summarize_failed_adapter_stdout(stdout),
            '{"error": "wrapper failed", "details": {"return_code": 1}}',
        )

    def test_summarize_failed_adapter_stdout_uses_result_text(self) -> None:
        stdout = '{"type":"result","is_error":true,"result":"API Error: invalid"}'
        self.assertEqual(
            _summarize_failed_adapter_stdout(stdout),
            "API Error: invalid",
        )


if __name__ == "__main__":
    unittest.main()
