from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from benchkit.swebench.dataset import filter_instances, load_instances


class DatasetTests(unittest.TestCase):
    def test_load_and_filter_rust(self) -> None:
        payload = "\n".join(
            [
                '{"instance_id":"a","repo":"org/a","base_commit":"c1","problem_statement":"x","language":"rust"}',
                '{"instance_id":"b","repo":"org/b","base_commit":"c2","problem_statement":"y","language":"python"}',
            ]
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "dataset.jsonl"
            path.write_text(payload, encoding="utf-8")
            instances = load_instances(path)
            rust_only = filter_instances(instances, language="rust")
            self.assertEqual(len(instances), 2)
            self.assertEqual(len(rust_only), 1)
            self.assertEqual(rust_only[0].instance_id, "a")

    def test_filter_by_repo_and_instance_id(self) -> None:
        payload = "\n".join(
            [
                '{"instance_id":"a","repo":"tokio-rs/tokio","base_commit":"c1","problem_statement":"x","language":"rust"}',
                '{"instance_id":"b","repo":"tokio-rs/tokio","base_commit":"c2","problem_statement":"y","language":"rust"}',
                '{"instance_id":"c","repo":"other/repo","base_commit":"c3","problem_statement":"z","language":"rust"}',
            ]
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "dataset.jsonl"
            path.write_text(payload, encoding="utf-8")
            instances = load_instances(path)
            selected = filter_instances(
                instances,
                include_repos=["tokio-rs/tokio"],
                include_instance_ids=["b"],
            )
            self.assertEqual(len(selected), 1)
            self.assertEqual(selected[0].instance_id, "b")

    def test_filter_rust_matches_tokio_rs_alias(self) -> None:
        payload = '{"instance_id":"a","repo":"tokio-rs/tokio","base_commit":"c1","problem_statement":"x","language":"tokio-rs"}'
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "dataset.jsonl"
            path.write_text(payload, encoding="utf-8")
            instances = load_instances(path)
            rust_only = filter_instances(instances, language="rust")
            self.assertEqual(len(rust_only), 1)
            self.assertEqual(rust_only[0].language, "rust")

    def test_rust_track_repo_python_hf_tag_still_filters_as_rust(self) -> None:
        payload = '{"instance_id":"ruff-1","repo":"astral-sh/ruff","base_commit":"c1","problem_statement":"x","language":"python"}'
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "dataset.jsonl"
            path.write_text(payload, encoding="utf-8")
            instances = load_instances(path)
            self.assertEqual(instances[0].language, "rust")
            rust_only = filter_instances(instances, language="rust")
            self.assertEqual(len(rust_only), 1)

    def test_swebench_pro_does_not_apply_multilingual_rust_track_override(self) -> None:
        payload = '{"instance_id":"ruff-1","repo":"astral-sh/ruff","base_commit":"c1","problem_statement":"x","repo_language":"python"}'
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "dataset.jsonl"
            path.write_text(payload, encoding="utf-8")
            instances = load_instances(path, benchmark="swebench_pro")
            self.assertEqual(instances[0].language, "python")

    def test_swebench_pro_normalizes_js_ts_aliases(self) -> None:
        payload = "\n".join(
            [
                '{"instance_id":"a","repo":"org/a","base_commit":"c1","problem_statement":"x","repo_language":"js"}',
                '{"instance_id":"b","repo":"org/b","base_commit":"c2","problem_statement":"y","repo_language":"ts"}',
            ]
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "dataset.jsonl"
            path.write_text(payload, encoding="utf-8")
            instances = load_instances(path, benchmark="swebench_pro")
            self.assertEqual(instances[0].language, "javascript")
            self.assertEqual(instances[1].language, "typescript")


if __name__ == "__main__":
    unittest.main()
