from __future__ import annotations

from pathlib import Path
import json
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

from benchkit.swebench.hf_export import (
    _load_hf_dataset,
    export_hf_dataset,
    export_hf_swebench_multilingual,
    normalize_hf_row,
)


class HFExportTests(unittest.TestCase):
    def test_normalize_hf_row_accepts_alternative_keys(self) -> None:
        raw = {
            "id": "rust__crate_x-1",
            "repository": "org/crate_x",
            "base_sha": "abc123",
            "prompt": "Fix panic in parser",
            "lang": "Rust",
            "difficulty": "easy",
        }
        row = normalize_hf_row(raw)
        self.assertEqual(row["instance_id"], "rust__crate_x-1")
        self.assertEqual(row["repo"], "org/crate_x")
        self.assertEqual(row["base_commit"], "abc123")
        self.assertEqual(row["problem_statement"], "Fix panic in parser")
        self.assertEqual(row["language"], "rust")
        self.assertEqual(row["difficulty"], "easy")

    def test_normalize_hf_row_infers_language_from_instance_id(self) -> None:
        raw = {
            "instance_id": "rust__crate_x-2",
            "repo": "org/crate_x",
            "base_commit": "def456",
            "problem_statement": "Fix error handling",
        }
        row = normalize_hf_row(raw)
        self.assertEqual(row["language"], "rust")
        self.assertEqual(row["repo_label"], "org")

    def test_normalize_hf_row_maps_tokio_rs_to_rust_and_adds_repo_label(self) -> None:
        raw = {
            "instance_id": "tokio-rs__tokio-4384",
            "repo": "tokio-rs/tokio",
            "base_commit": "abc123",
            "problem_statement": "Fix unwind safe marker",
        }
        row = normalize_hf_row(raw)
        self.assertEqual(row["language"], "rust")
        self.assertEqual(row["repo_label"], "tokio-rs")

    def test_export_hf_filters_language_and_applies_max_instances(self) -> None:
        fake_rows = [
            {
                "instance_id": "python__lib_a-1",
                "repo": "org/lib_a",
                "base_commit": "111aaa",
                "problem_statement": "Fix serialization",
                "language": "python",
            },
            {
                "instance_id": "tokio-rs__crate_b-1",
                "repo": "org/crate_b",
                "base_commit": "222bbb",
                "problem_statement": "Fix unwrap panic",
                "language": "tokio-rs",
            },
            {
                "instance_id": "rust__crate_c-1",
                "repo": "org/crate_c",
                "base_commit": "333ccc",
                "problem_statement": "Fix parser edge case",
                "language": "rust",
            },
        ]
        with tempfile.TemporaryDirectory() as temp_dir:
            out = Path(temp_dir) / "export.jsonl"
            with patch(
                "benchkit.swebench.hf_export._load_hf_dataset",
                return_value=fake_rows,
            ):
                stats = export_hf_swebench_multilingual(
                    output_path=out,
                    split="dev",
                    language="rust",
                    max_instances=1,
                    overwrite=True,
                )

            lines = out.read_text(encoding="utf-8").strip().splitlines()
            self.assertEqual(len(lines), 1)
            row = json.loads(lines[0])
            self.assertEqual(row["language"], "rust")
            self.assertEqual(row["instance_id"], "tokio-rs__crate_b-1")
            self.assertEqual(stats.rows_written, 1)
            self.assertEqual(stats.total_rows_seen, 2)

    def test_export_hf_filters_repo_and_instance_id(self) -> None:
        fake_rows = [
            {
                "instance_id": "rust__crate_a-1",
                "repo": "tokio-rs/tokio",
                "base_commit": "111aaa",
                "problem_statement": "Fix A",
                "language": "rust",
            },
            {
                "instance_id": "rust__crate_b-1",
                "repo": "tokio-rs/tokio",
                "base_commit": "222bbb",
                "problem_statement": "Fix B",
                "language": "rust",
            },
            {
                "instance_id": "rust__crate_c-1",
                "repo": "other/repo",
                "base_commit": "333ccc",
                "problem_statement": "Fix C",
                "language": "rust",
            },
        ]
        with tempfile.TemporaryDirectory() as temp_dir:
            out = Path(temp_dir) / "export.jsonl"
            with patch(
                "benchkit.swebench.hf_export._load_hf_dataset",
                return_value=fake_rows,
            ):
                stats = export_hf_swebench_multilingual(
                    output_path=out,
                    split="dev",
                    include_repos=["tokio-rs/tokio"],
                    include_instance_ids=["rust__crate_b-1"],
                    overwrite=True,
                )

            lines = out.read_text(encoding="utf-8").strip().splitlines()
            self.assertEqual(len(lines), 1)
            row = json.loads(lines[0])
            self.assertEqual(row["repo"], "tokio-rs/tokio")
            self.assertEqual(row["instance_id"], "rust__crate_b-1")
            self.assertEqual(stats.rows_written, 1)

    def test_export_hf_requires_overwrite_for_existing_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            out = Path(temp_dir) / "export.jsonl"
            out.write_text("{}", encoding="utf-8")
            with self.assertRaises(FileExistsError):
                export_hf_swebench_multilingual(
                    output_path=out,
                    split="dev",
                )

    def test_export_hf_pro_uses_pro_language_without_multilingual_rust_override(self) -> None:
        fake_rows = [
            {
                "instance_id": "instance_a",
                "repo": "astral-sh/ruff",
                "base_commit": "111aaa",
                "problem_statement": "Fix A",
                "repo_language": "python",
            },
            {
                "instance_id": "instance_b",
                "repo": "org/js-task",
                "base_commit": "222bbb",
                "problem_statement": "Fix B",
                "repo_language": "js",
            },
        ]
        with tempfile.TemporaryDirectory() as temp_dir:
            out = Path(temp_dir) / "export.jsonl"
            with patch(
                "benchkit.swebench.hf_export._load_hf_dataset",
                return_value=fake_rows,
            ):
                stats = export_hf_dataset(
                    output_path=out,
                    split="test",
                    dataset="ScaleAI/SWE-bench_Pro",
                    benchmark="swebench_pro",
                    overwrite=True,
                )

            rows = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(stats.rows_written, 2)
            self.assertEqual(rows[0]["language"], "python")
            self.assertEqual(rows[1]["language"], "javascript")

    def test_load_hf_dataset_errors_when_shadowed_module_has_no_loader(self) -> None:
        with patch(
            "benchkit.swebench.hf_export.importlib.import_module",
            return_value=SimpleNamespace(),
        ):
            with self.assertRaises(RuntimeError):
                _load_hf_dataset(
                    dataset="SWE-bench/SWE-bench_Multilingual",
                    dataset_config=None,
                    split="dev",
                    revision=None,
                    cache_dir=None,
                    streaming=False,
                    token=None,
                )

    def test_load_hf_dataset_uses_fallback_module_when_needed(self) -> None:
        def fake_loader(**kwargs):  # noqa: ANN003
            return {"ok": True, "kwargs": kwargs}

        with patch(
            "benchkit.swebench.hf_export._import_datasets_module",
            return_value=SimpleNamespace(),
        ), patch(
            "benchkit.swebench.hf_export._import_datasets_without_workspace_shadowing",
            return_value=SimpleNamespace(load_dataset=fake_loader),
        ):
            out = _load_hf_dataset(
                dataset="SWE-bench/SWE-bench_Multilingual",
                dataset_config=None,
                split="dev",
                revision=None,
                cache_dir=None,
                streaming=False,
                token=None,
            )
        self.assertTrue(out["ok"])
        self.assertEqual(out["kwargs"]["path"], "SWE-bench/SWE-bench_Multilingual")


if __name__ == "__main__":
    unittest.main()
