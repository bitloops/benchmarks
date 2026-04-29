import csv
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[4]
SCRIPT = (
    REPO_ROOT
    / ".agents"
    / "skills"
    / "benchmark-run-row"
    / "scripts"
    / "generate_benchmark_run_row.py"
)
UPLOAD_SCRIPT = SCRIPT.with_name("upload_trace_jsonl_to_drive.py")
REPORT_UPLOAD_SCRIPT = SCRIPT.with_name("upload_report_folder_to_drive.py")


def load_upload_module():
    spec = importlib.util.spec_from_file_location("upload_trace_jsonl_to_drive", UPLOAD_SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec is not None
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def load_report_upload_module():
    spec = importlib.util.spec_from_file_location(
        "upload_report_folder_to_drive", REPORT_UPLOAD_SCRIPT
    )
    module = importlib.util.module_from_spec(spec)
    assert spec is not None
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class GenerateBenchmarkRunRowTests(unittest.TestCase):
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
            write_per_task_csv(
                report_dir,
                [
                    {
                        "task_id": "repo__one-1",
                        "attempt": "1",
                        "agent": "opencode",
                        "model_version": "model/full",
                        "status": "unsolved",
                        "runtime_sec": "12.3",
                        "token_input": "10",
                        "token_output": "2",
                        "cache_read_input_tokens": "3",
                        "cache_creation_input_tokens": "4",
                        "total_tokens": "19",
                        "tool_calls": "5",
                    }
                ],
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
        self.assertEqual(cells[-1], "")

    def test_run_id_report_folder_can_be_overridden(self) -> None:
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
                        "runtime_sec": "12.3",
                        "token_input": "10",
                        "token_output": "2",
                        "cache_read_input_tokens": "3",
                        "cache_creation_input_tokens": "4",
                        "total_tokens": "19",
                        "tool_calls": "5",
                    }
                ],
            )

            completed = subprocess.run(
                [
                    "python3",
                    str(SCRIPT),
                    str(report_dir),
                    "--run-id-report-folder",
                    "https://drive.google.com/drive/folders/report",
                ],
                cwd=REPO_ROOT,
                check=True,
                text=True,
                capture_output=True,
            )

        cells = completed.stdout.rstrip("\n").split("\t")
        self.assertEqual(cells[-1], "https://drive.google.com/drive/folders/report")

    def test_report_folder_link_alias_still_works(self) -> None:
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
                        "runtime_sec": "12.3",
                        "token_input": "10",
                        "token_output": "2",
                        "cache_read_input_tokens": "3",
                        "cache_creation_input_tokens": "4",
                        "total_tokens": "19",
                        "tool_calls": "5",
                    }
                ],
            )

            completed = subprocess.run(
                [
                    "python3",
                    str(SCRIPT),
                    str(report_dir),
                    "--report-folder-link",
                    "https://drive.google.com/drive/folders/report",
                ],
                cwd=REPO_ROOT,
                check=True,
                text=True,
                capture_output=True,
            )

        cells = completed.stdout.rstrip("\n").split("\t")
        self.assertEqual(cells[-1], "https://drive.google.com/drive/folders/report")

    def test_run_id_report_folder_defaults_from_summary_row(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            report_dir = Path(tmpdir)
            write_summary_csv(
                report_dir,
                {"run_id_report_folder": "https://drive.google.com/drive/folders/from-summary"},
            )
            write_per_task_csv(
                report_dir,
                [
                    {
                        "task_id": "repo__one-1",
                        "attempt": "1",
                        "agent": "opencode",
                        "model_version": "model/full",
                        "status": "unsolved",
                        "runtime_sec": "12.3",
                        "token_input": "10",
                        "token_output": "2",
                        "cache_read_input_tokens": "3",
                        "cache_creation_input_tokens": "4",
                        "total_tokens": "19",
                        "tool_calls": "5",
                    }
                ],
            )

            completed = subprocess.run(
                ["python3", str(SCRIPT), str(report_dir)],
                cwd=REPO_ROOT,
                check=True,
                text=True,
                capture_output=True,
            )

        cells = completed.stdout.rstrip("\n").split("\t")
        self.assertEqual(cells[-1], "https://drive.google.com/drive/folders/from-summary")

    def test_uses_per_task_log_metrics_without_filters(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            report_dir = Path(tmpdir)
            write_summary_csv(
                report_dir,
                {
                    "runtime_total_sec": "999",
                    "input_tokens_total": "999",
                    "output_tokens_total": "999",
                    "cache_read_input_tokens_total": "999",
                    "cache_creation_input_tokens_total": "999",
                    "derived_total_input_processed_tokens": "999",
                    "derived_total_processed_tokens": "999",
                    "result": "unsolved",
                    "internal_tool_calls": "999",
                },
            )
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
                        "task_id": "repo__one-1",
                        "attempt": "2",
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
                ["python3", str(SCRIPT), str(report_dir)],
                cwd=REPO_ROOT,
                check=True,
                text=True,
                capture_output=True,
            )

        cells = completed.stdout.rstrip("\n").split("\t")
        self.assertEqual(cells[7], "40")
        self.assertEqual(cells[8], "300")
        self.assertEqual(cells[9], "60")
        self.assertEqual(cells[10], "18")
        self.assertEqual(cells[11], "16")
        self.assertEqual(cells[12], "334")
        self.assertEqual(cells[13], "394")
        self.assertEqual(cells[14], "solved")
        self.assertEqual(cells[15], "14")

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

    def test_bitloops_rows_split_devql_calls_from_internal_tool_calls(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            report_dir = Path(tmpdir)
            write_summary_csv(report_dir, {"condition": "with_bitloops", "internal_tool_calls": "999"})
            write_per_task_csv(
                report_dir,
                [
                    {
                        "task_id": "repo__one-1",
                        "attempt": "1",
                        "agent": "opencode",
                        "model_version": "model/full",
                        "condition": "with_bitloops",
                        "status": "solved",
                        "runtime_sec": "10",
                        "token_input": "100",
                        "token_output": "20",
                        "cache_read_input_tokens": "7",
                        "cache_creation_input_tokens": "3",
                        "total_tokens": "130",
                        "tool_calls": "5",
                    }
                ],
            )
            write_tool_invocation_log(
                report_dir,
                [
                    {
                        "task_id": "repo__one-1",
                        "attempt": 1,
                        "tool": "Bash",
                        "curated": {"command": "bitloops devql query '{ selectArtefacts { count } }'"},
                    },
                    {
                        "task_id": "repo__one-1",
                        "attempt": 1,
                        "tool": "Bash",
                        "curated": {"command": "cargo test"},
                    },
                    {
                        "task_id": "repo__one-1",
                        "attempt": 1,
                        "tool": "Read",
                        "curated": {},
                    },
                ],
            )

            completed = subprocess.run(
                [
                    "python3",
                    str(SCRIPT),
                    str(report_dir),
                    "--instance-id",
                    "repo__one-1",
                    "--attempt",
                    "1",
                    "--analysis",
                    "checked",
                    "--developer-comment",
                    "looks ok",
                    "--next-action",
                    "ship",
                    "--include-header",
                ],
                cwd=REPO_ROOT,
                check=True,
                text=True,
                capture_output=True,
            )

        header, row = completed.stdout.rstrip("\n").splitlines()
        columns = header.split("\t")
        cells = row.split("\t")
        self.assertEqual(columns[15], "devql_calls_num")
        self.assertEqual(columns[16], "internal_tool_calls")
        self.assertEqual(columns[17], "analysis (from AI and or query or script)")
        self.assertEqual(columns[18], "developer comment on analysis")
        self.assertEqual(columns[19], "next_action")
        self.assertEqual(columns[20], "run_id_report_folder")
        self.assertNotIn("ai_agent_and_model_used_for_analysis", columns)
        self.assertEqual(cells[15], "1")
        self.assertEqual(cells[16], "4")
        self.assertEqual(cells[17], "checked")
        self.assertEqual(cells[18], "looks ok")
        self.assertEqual(cells[19], "ship")
        self.assertEqual(cells[20], "")


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


class UploadReportFolderToDriveTests(unittest.TestCase):
    def test_default_report_folder_name_uses_run_id(self) -> None:
        module = load_report_upload_module()

        self.assertEqual(
            module.default_report_folder_name({"run_id": "20260428_141026_85fdaf"}, Path("report")),
            "20260428_141026_85fdaf",
        )

    def test_resolves_appendix_report_files_only(self) -> None:
        module = load_report_upload_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            report_dir = Path(tmpdir)
            for name in (
                "run_summary.csv",
                "run_summary.jsonl",
                "appendix_minimal_per_task_log.csv",
                "appendix_tool_invocation_log.jsonl",
                "notes.txt",
            ):
                (report_dir / name).write_text(name, encoding="utf-8")
            nested = report_dir / "nested"
            nested.mkdir()
            (nested / "appendix_nested.csv").write_text("nested", encoding="utf-8")

            uploads = module.resolve_report_file_uploads(report_dir)

        self.assertEqual(
            [upload.name for upload in uploads],
            [
                "appendix_minimal_per_task_log.csv",
                "appendix_tool_invocation_log.jsonl",
                "run_summary.csv",
                "run_summary.jsonl",
            ],
        )

    def test_create_drive_folder_falls_back_to_folder_url(self) -> None:
        module = load_report_upload_module()

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self):
                return b'{"id": "folder123", "name": "run-1"}'

        with mock.patch.object(module.urllib.request, "urlopen", return_value=FakeResponse()):
            folder = module.create_drive_folder(
                folder_name="run-1",
                parent_folder_id="parent123",
                access_token="token",
            )

        self.assertEqual(folder.id, "folder123")
        self.assertEqual(folder.link, "https://drive.google.com/drive/folders/folder123")


def write_summary_csv(report_dir: Path, overrides: dict[str, str] | None = None) -> None:
    row = {
        "run_id": "run-1",
        "run_datetime": "2026-04-28T07:01:09Z",
        "engineer": "markos",
        "condition": "baseline",
        "agent": "opencode",
        "model_canonical": "qwen3.6-plus",
        "model_resolved": "model/full",
        "log_jsonl_link": "runs/run-1/attempts/attempt-01/trace.jsonl",
        "run_id_report_folder": "",
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
    row.update(overrides or {})
    with (report_dir / "run_summary.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "run_id",
                "run_datetime",
                "engineer",
                "condition",
                "agent",
                "model_canonical",
                "model_resolved",
                "log_jsonl_link",
                "run_id_report_folder",
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
        writer.writerow(row)


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
                "condition",
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


def write_tool_invocation_log(report_dir: Path, rows: list[dict]) -> None:
    with (report_dir / "appendix_tool_invocation_log.jsonl").open(
        "w", encoding="utf-8"
    ) as handle:
        for row in rows:
            handle.write(json.dumps(row) + "\n")


if __name__ == "__main__":
    unittest.main()
