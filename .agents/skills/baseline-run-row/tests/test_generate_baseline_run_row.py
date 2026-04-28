import csv
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[4]
SCRIPT = (
    REPO_ROOT
    / ".agents"
    / "skills"
    / "baseline-run-row"
    / "scripts"
    / "generate_baseline_run_row.py"
)
UPLOAD_SCRIPT = SCRIPT.with_name("upload_trace_jsonl_to_drive.py")


def load_upload_module():
    spec = importlib.util.spec_from_file_location("upload_trace_jsonl_to_drive", UPLOAD_SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec is not None
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class GenerateBaselineRunRowTests(unittest.TestCase):
    def test_log_jsonl_link_can_be_overridden(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            report_dir = Path(tmpdir)
            with (report_dir / "run_summary.csv").open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(
                    handle,
                    fieldnames=[
                        "run_id",
                        "run_datetime",
                        "engineer",
                        "agent",
                        "model_canonical",
                        "log_jsonl_link",
                        "runtime_total_sec",
                        "input_tokens_total",
                        "output_tokens_total",
                        "cache_read_input_tokens_total",
                        "cache_creation_input_tokens_total",
                        "derived_total_input_processed_tokens",
                        "derived_total_processed_tokens",
                        "result",
                        "internal_tool_calls",
                    ],
                )
                writer.writeheader()
                writer.writerow(
                    {
                        "run_id": "run-1",
                        "run_datetime": "2026-04-28T07:01:09Z",
                        "engineer": "markos",
                        "agent": "opencode",
                        "model_canonical": "qwen3.6-plus",
                        "log_jsonl_link": "runs/run-1/attempts/attempt-01/trace.jsonl",
                        "runtime_total_sec": "12.3",
                        "input_tokens_total": "10",
                        "output_tokens_total": "2",
                        "cache_read_input_tokens_total": "3",
                        "cache_creation_input_tokens_total": "4",
                        "derived_total_input_processed_tokens": "17",
                        "derived_total_processed_tokens": "19",
                        "result": "unsolved",
                        "internal_tool_calls": "5",
                    }
                )

            completed = subprocess.run(
                [
                    "python3",
                    str(SCRIPT),
                    str(report_dir),
                    "--log-jsonl-link",
                    "https://drive.google.com/file/d/uploaded/view",
                ],
                cwd=REPO_ROOT,
                check=True,
                text=True,
                capture_output=True,
            )

        cells = completed.stdout.rstrip("\n").split("\t")
        self.assertEqual(cells[6], "https://drive.google.com/file/d/uploaded/view")

    def test_instance_id_filters_row_metrics_from_per_task_log(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            report_dir = Path(tmpdir)
            write_summary_csv(report_dir)
            write_per_task_csv(
                report_dir,
                [
                    {
                        "task_id": "repo__one-1",
                        "attempt": "1",
                        "agent": "opencode",
                        "model_version": "model/full",
                        "status": "unsolved",
                        "runtime_sec": "10",
                        "token_input": "100",
                        "token_output": "20",
                        "cache_read_input_tokens": "7",
                        "cache_creation_input_tokens": "3",
                        "total_tokens": "130",
                        "tool_calls": "5",
                    },
                    {
                        "task_id": "repo__two-2",
                        "attempt": "1",
                        "agent": "opencode",
                        "model_version": "model/full",
                        "status": "solved",
                        "runtime_sec": "30",
                        "token_input": "200",
                        "token_output": "40",
                        "cache_read_input_tokens": "11",
                        "cache_creation_input_tokens": "13",
                        "total_tokens": "264",
                        "tool_calls": "9",
                    },
                ],
            )

            completed = subprocess.run(
                ["python3", str(SCRIPT), str(report_dir), "--instance-id", "repo__two-2"],
                cwd=REPO_ROOT,
                check=True,
                text=True,
                capture_output=True,
            )

        cells = completed.stdout.rstrip("\n").split("\t")
        self.assertEqual(cells[7], "30")
        self.assertEqual(cells[8], "200")
        self.assertEqual(cells[9], "40")
        self.assertEqual(cells[10], "11")
        self.assertEqual(cells[11], "13")
        self.assertEqual(cells[12], "224")
        self.assertEqual(cells[13], "264")
        self.assertEqual(cells[14], "solved")
        self.assertEqual(cells[15], "9")


class UploadTraceJsonlToDriveTests(unittest.TestCase):
    def test_extracts_folder_id_from_drive_folder_url(self) -> None:
        module = load_upload_module()

        self.assertEqual(
            module.extract_folder_id(
                "https://drive.google.com/drive/folders/1tTJyjLxX67ETCoPPq5u48CWvunv4ni8D"
            ),
            "1tTJyjLxX67ETCoPPq5u48CWvunv4ni8D",
        )

    def test_resolves_raw_stdout_path_from_trace_metadata_first(self) -> None:
        module = load_upload_module()
        row = {
            "run_id": "20260428_070109_c48cd0",
            "trace_jsonl_paths": (
                "runs/swebench_multilingual/20260428/20260428_070109_c48cd0/"
                "attempts/attempt-01/trace.jsonl"
            ),
        }

        uploads = module.resolve_trace_uploads(row, REPO_ROOT)

        self.assertEqual(len(uploads), 1)
        self.assertEqual(
            uploads[0].path,
            REPO_ROOT
            / "runs"
            / "swebench_multilingual"
            / "20260428"
            / "20260428_070109_c48cd0"
            / "attempts"
            / "attempt-01"
            / "agent_raw"
            / "tokio-rs__tokio-4384.opencode.stdout.jsonl",
        )
        self.assertEqual(
            uploads[0].name,
            "20260428_070109_c48cd0_attempt-01_tokio-rs__tokio-4384.opencode.stdout.jsonl",
        )

    def test_filters_multiple_attempts_and_instances(self) -> None:
        module = load_upload_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            trace_paths = []
            for attempt in ("attempt-01", "attempt-02"):
                attempt_dir = root / "runs" / "run-1" / "attempts" / attempt
                raw_dir = attempt_dir / "agent_raw"
                raw_dir.mkdir(parents=True)
                trace_path = attempt_dir / "trace.jsonl"
                trace_paths.append(str(trace_path.relative_to(root)))
                rows = []
                for instance_id in ("repo__one-1", "repo__two-2"):
                    raw_path = raw_dir / f"{instance_id}.opencode.stdout.jsonl"
                    raw_path.write_text('{"type":"text"}\n', encoding="utf-8")
                    rows.append(
                        {
                            "instance_id": instance_id,
                            "metadata": {"raw_stdout_path": str(raw_path)},
                        }
                    )
                trace_path.write_text(
                    "".join(json.dumps(row) + "\n" for row in rows),
                    encoding="utf-8",
                )

            uploads = module.resolve_trace_uploads(
                {"run_id": "run-1", "trace_jsonl_paths": ";".join(trace_paths)},
                root,
                attempt_filters=["2"],
                instance_id_filters=["repo__two-2"],
            )

        self.assertEqual(len(uploads), 1)
        self.assertEqual(uploads[0].path.name, "repo__two-2.opencode.stdout.jsonl")
        self.assertEqual(uploads[0].name, "run-1_attempt-02_repo__two-2.opencode.stdout.jsonl")


def write_summary_csv(report_dir: Path) -> None:
    with (report_dir / "run_summary.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "run_id",
                "run_datetime",
                "engineer",
                "agent",
                "model_canonical",
                "model_resolved",
                "log_jsonl_link",
                "runtime_total_sec",
                "input_tokens_total",
                "output_tokens_total",
                "cache_read_input_tokens_total",
                "cache_creation_input_tokens_total",
                "derived_total_input_processed_tokens",
                "derived_total_processed_tokens",
                "result",
                "internal_tool_calls",
            ],
        )
        writer.writeheader()
        writer.writerow(
            {
                "run_id": "run-1",
                "run_datetime": "2026-04-28T07:01:09Z",
                "engineer": "markos",
                "agent": "opencode",
                "model_canonical": "qwen3.6-plus",
                "model_resolved": "model/full",
                "log_jsonl_link": "runs/run-1/attempts/attempt-01/trace.jsonl",
                "runtime_total_sec": "40",
                "input_tokens_total": "300",
                "output_tokens_total": "60",
                "cache_read_input_tokens_total": "18",
                "cache_creation_input_tokens_total": "16",
                "derived_total_input_processed_tokens": "334",
                "derived_total_processed_tokens": "394",
                "result": "partially_solved",
                "internal_tool_calls": "14",
            }
        )


def write_per_task_csv(report_dir: Path, rows: list[dict[str, str]]) -> None:
    with (report_dir / "appendix_minimal_per_task_log.csv").open(
        "w", encoding="utf-8", newline=""
    ) as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "task_id",
                "attempt",
                "agent",
                "model_version",
                "status",
                "runtime_sec",
                "token_input",
                "token_output",
                "cache_read_input_tokens",
                "cache_creation_input_tokens",
                "total_tokens",
                "tool_calls",
            ],
        )
        writer.writeheader()
        writer.writerows(rows)


if __name__ == "__main__":
    unittest.main()
