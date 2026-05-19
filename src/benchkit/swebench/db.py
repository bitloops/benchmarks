from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
import csv
import sqlite3

from benchkit.common.io import read_json


TASK_ATTEMPT_FIELDS = [
    "task_id",
    "attempt",
    "benchmark",
    "benchmark_version",
    "repo",
    "repo_label",
    "language",
    "agent",
    "model_version",
    "condition",
    "status",
    "runtime_sec",
    "input_tokens",
    "output_tokens",
    "cache_read_input_tokens",
    "cache_creation_input_tokens",
    "total_input_processed_tokens",
    "total_processed_tokens",
    "estimated_cost",
    "tool_calls",
    "shell_commands",
    "file_reads",
    "search_actions",
    "files_edited",
    "patch_size",
    "first_file_opened",
    "first_file_edited",
    "first_test_command",
    "bitloops_context_tokens",
    "contextbench_final_file_coverage",
    "contextbench_final_file_precision",
    "contextbench_final_symbol_coverage",
    "contextbench_final_symbol_precision",
    "contextbench_final_span_coverage",
    "contextbench_final_span_precision",
    "contextbench_final_line_coverage",
    "contextbench_final_line_precision",
    "contextbench_traj_auc_file",
    "contextbench_traj_auc_symbol",
    "contextbench_traj_auc_span",
    "contextbench_traj_auc_line",
    "contextbench_traj_redundancy_file",
    "contextbench_traj_redundancy_symbol",
    "contextbench_traj_redundancy_span",
    "contextbench_traj_redundancy_line",
    "contextbench_editloc_recall",
    "contextbench_editloc_precision",
    "evaluator_result",
]

INTEGER_FIELDS = {
    "attempt",
    "input_tokens",
    "output_tokens",
    "cache_read_input_tokens",
    "cache_creation_input_tokens",
    "total_input_processed_tokens",
    "total_processed_tokens",
    "tool_calls",
    "shell_commands",
    "file_reads",
    "search_actions",
    "files_edited",
    "patch_size",
    "bitloops_context_tokens",
}

REAL_FIELDS = {
    "runtime_sec",
    "estimated_cost",
    "contextbench_final_file_coverage",
    "contextbench_final_file_precision",
    "contextbench_final_symbol_coverage",
    "contextbench_final_symbol_precision",
    "contextbench_final_span_coverage",
    "contextbench_final_span_precision",
    "contextbench_final_line_coverage",
    "contextbench_final_line_precision",
    "contextbench_traj_auc_file",
    "contextbench_traj_auc_symbol",
    "contextbench_traj_auc_span",
    "contextbench_traj_auc_line",
    "contextbench_traj_redundancy_file",
    "contextbench_traj_redundancy_symbol",
    "contextbench_traj_redundancy_span",
    "contextbench_traj_redundancy_line",
    "contextbench_editloc_recall",
    "contextbench_editloc_precision",
}

RUN_COLUMNS = [
    "run_id",
    "benchmark",
    "condition",
    "dataset_path",
    "split",
    "agent",
    "model_canonical",
    "model_resolved",
    "max_workers",
    "attempts",
    "evaluation_enabled",
    "started_at_utc",
    "run_root",
]

TASK_ATTEMPT_COLUMNS = ["run_id", *TASK_ATTEMPT_FIELDS]

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    benchmark TEXT,
    condition TEXT,
    dataset_path TEXT,
    split TEXT,
    agent TEXT,
    model_canonical TEXT,
    model_resolved TEXT,
    max_workers INTEGER,
    attempts INTEGER,
    evaluation_enabled INTEGER,
    started_at_utc TEXT,
    run_root TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS task_attempts (
    run_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    attempt INTEGER NOT NULL,
    benchmark TEXT,
    benchmark_version TEXT,
    repo TEXT,
    repo_label TEXT,
    language TEXT,
    agent TEXT,
    model_version TEXT,
    condition TEXT,
    status TEXT,
    runtime_sec REAL,
    input_tokens INTEGER,
    output_tokens INTEGER,
    cache_read_input_tokens INTEGER,
    cache_creation_input_tokens INTEGER,
    total_input_processed_tokens INTEGER,
    total_processed_tokens INTEGER,
    estimated_cost REAL,
    tool_calls INTEGER,
    shell_commands INTEGER,
    file_reads INTEGER,
    search_actions INTEGER,
    files_edited INTEGER,
    patch_size INTEGER,
    first_file_opened TEXT,
    first_file_edited TEXT,
    first_test_command TEXT,
    bitloops_context_tokens INTEGER,
    contextbench_final_file_coverage REAL,
    contextbench_final_file_precision REAL,
    contextbench_final_symbol_coverage REAL,
    contextbench_final_symbol_precision REAL,
    contextbench_final_span_coverage REAL,
    contextbench_final_span_precision REAL,
    contextbench_final_line_coverage REAL,
    contextbench_final_line_precision REAL,
    contextbench_traj_auc_file REAL,
    contextbench_traj_auc_symbol REAL,
    contextbench_traj_auc_span REAL,
    contextbench_traj_auc_line REAL,
    contextbench_traj_redundancy_file REAL,
    contextbench_traj_redundancy_symbol REAL,
    contextbench_traj_redundancy_span REAL,
    contextbench_traj_redundancy_line REAL,
    contextbench_editloc_recall REAL,
    contextbench_editloc_precision REAL,
    evaluator_result TEXT,
    PRIMARY KEY (run_id, task_id, attempt),
    FOREIGN KEY (run_id) REFERENCES runs(run_id)
);

CREATE INDEX IF NOT EXISTS idx_task_attempts_task_id ON task_attempts(task_id);
CREATE INDEX IF NOT EXISTS idx_task_attempts_status ON task_attempts(status);
CREATE INDEX IF NOT EXISTS idx_task_attempts_model_version ON task_attempts(model_version);
CREATE INDEX IF NOT EXISTS idx_task_attempts_condition ON task_attempts(condition);

CREATE VIEW IF NOT EXISTS run_summary AS
SELECT
    r.run_id,
    r.benchmark,
    r.condition,
    r.agent,
    r.model_resolved,
    r.max_workers,
    r.attempts AS configured_attempts,
    COUNT(t.task_id) AS task_attempt_rows,
    COUNT(DISTINCT t.task_id) AS unique_tasks,
    SUM(CASE WHEN t.status = 'solved' THEN 1 ELSE 0 END) AS solved_rows,
    AVG(t.runtime_sec) AS avg_runtime_sec,
    AVG(t.estimated_cost) AS avg_estimated_cost
FROM runs AS r
LEFT JOIN task_attempts AS t ON t.run_id = r.run_id
GROUP BY
    r.run_id,
    r.benchmark,
    r.condition,
    r.agent,
    r.model_resolved,
    r.max_workers,
    r.attempts;

CREATE VIEW IF NOT EXISTS model_condition_summary AS
SELECT
    r.benchmark,
    t.agent,
    t.model_version,
    t.condition,
    COUNT(*) AS task_attempt_rows,
    SUM(CASE WHEN t.status = 'solved' THEN 1 ELSE 0 END) AS solved_rows,
    AVG(t.runtime_sec) AS avg_runtime_sec,
    AVG(t.estimated_cost) AS avg_estimated_cost
FROM task_attempts AS t
JOIN runs AS r ON r.run_id = t.run_id
GROUP BY
    r.benchmark,
    t.agent,
    t.model_version,
    t.condition;
"""


@dataclass(slots=True)
class DatabaseImportResult:
    db_path: Path
    run_id: str
    inserted_runs: int
    inserted_task_attempts: int


def import_appendix_csv_to_sqlite(
    db_path: Path,
    appendix_csv: Path,
    run_root: Path,
) -> DatabaseImportResult:
    db_path = db_path.resolve()
    appendix_csv = appendix_csv.resolve()
    run_root = run_root.resolve()

    manifest_path = run_root / "run_manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError(f"Missing run manifest: {manifest_path}")
    if not appendix_csv.exists():
        raise FileNotFoundError(f"Missing appendix CSV: {appendix_csv}")

    manifest = read_json(manifest_path)
    run_row = _build_run_row(manifest, run_root)
    task_attempt_rows = _load_task_attempt_rows(appendix_csv, run_row["run_id"])

    db_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(db_path)
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.executescript(SCHEMA_SQL)
        task_attempt_columns_sql = ", ".join(TASK_ATTEMPT_COLUMNS)
        task_attempt_placeholders_sql = ", ".join(["?"] * len(TASK_ATTEMPT_COLUMNS))
        existing_run = connection.execute(
            "SELECT 1 FROM runs WHERE run_id = ?",
            (run_row["run_id"],),
        ).fetchone()
        existing_task_keys = {
            (str(task_id), int(attempt))
            for task_id, attempt in connection.execute(
                "SELECT task_id, attempt FROM task_attempts WHERE run_id = ?",
                (run_row["run_id"],),
            )
        }

        with connection:
            connection.execute(
                """
                INSERT INTO runs (
                    run_id, benchmark, condition, dataset_path, split, agent,
                    model_canonical, model_resolved, max_workers, attempts,
                    evaluation_enabled, started_at_utc, run_root
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id) DO UPDATE SET
                    benchmark = excluded.benchmark,
                    condition = excluded.condition,
                    dataset_path = excluded.dataset_path,
                    split = excluded.split,
                    agent = excluded.agent,
                    model_canonical = excluded.model_canonical,
                    model_resolved = excluded.model_resolved,
                    max_workers = excluded.max_workers,
                    attempts = excluded.attempts,
                    evaluation_enabled = excluded.evaluation_enabled,
                    started_at_utc = excluded.started_at_utc,
                    run_root = excluded.run_root
                """,
                tuple(run_row[column] for column in RUN_COLUMNS),
            )
            connection.executemany(
                f"""
                INSERT INTO task_attempts (
                    {task_attempt_columns_sql}
                ) VALUES ({task_attempt_placeholders_sql})
                ON CONFLICT(run_id, task_id, attempt) DO UPDATE SET
                    benchmark = excluded.benchmark,
                    benchmark_version = excluded.benchmark_version,
                    repo = excluded.repo,
                    repo_label = excluded.repo_label,
                    language = excluded.language,
                    agent = excluded.agent,
                    model_version = excluded.model_version,
                    condition = excluded.condition,
                    status = excluded.status,
                    runtime_sec = excluded.runtime_sec,
                    input_tokens = excluded.input_tokens,
                    output_tokens = excluded.output_tokens,
                    cache_read_input_tokens = excluded.cache_read_input_tokens,
                    cache_creation_input_tokens = excluded.cache_creation_input_tokens,
                    total_input_processed_tokens = excluded.total_input_processed_tokens,
                    total_processed_tokens = excluded.total_processed_tokens,
                    estimated_cost = excluded.estimated_cost,
                    tool_calls = excluded.tool_calls,
                    shell_commands = excluded.shell_commands,
                    file_reads = excluded.file_reads,
                    search_actions = excluded.search_actions,
                    files_edited = excluded.files_edited,
                    patch_size = excluded.patch_size,
                    first_file_opened = excluded.first_file_opened,
                    first_file_edited = excluded.first_file_edited,
                    first_test_command = excluded.first_test_command,
                    bitloops_context_tokens = excluded.bitloops_context_tokens,
                    contextbench_final_file_coverage = excluded.contextbench_final_file_coverage,
                    contextbench_final_file_precision = excluded.contextbench_final_file_precision,
                    contextbench_final_symbol_coverage = excluded.contextbench_final_symbol_coverage,
                    contextbench_final_symbol_precision = excluded.contextbench_final_symbol_precision,
                    contextbench_final_span_coverage = excluded.contextbench_final_span_coverage,
                    contextbench_final_span_precision = excluded.contextbench_final_span_precision,
                    contextbench_final_line_coverage = excluded.contextbench_final_line_coverage,
                    contextbench_final_line_precision = excluded.contextbench_final_line_precision,
                    contextbench_traj_auc_file = excluded.contextbench_traj_auc_file,
                    contextbench_traj_auc_symbol = excluded.contextbench_traj_auc_symbol,
                    contextbench_traj_auc_span = excluded.contextbench_traj_auc_span,
                    contextbench_traj_auc_line = excluded.contextbench_traj_auc_line,
                    contextbench_traj_redundancy_file = excluded.contextbench_traj_redundancy_file,
                    contextbench_traj_redundancy_symbol = excluded.contextbench_traj_redundancy_symbol,
                    contextbench_traj_redundancy_span = excluded.contextbench_traj_redundancy_span,
                    contextbench_traj_redundancy_line = excluded.contextbench_traj_redundancy_line,
                    contextbench_editloc_recall = excluded.contextbench_editloc_recall,
                    contextbench_editloc_precision = excluded.contextbench_editloc_precision,
                    evaluator_result = excluded.evaluator_result
                """,
                [tuple(row[column] for column in TASK_ATTEMPT_COLUMNS) for row in task_attempt_rows],
            )
    finally:
        connection.close()

    inserted_runs = 0 if existing_run else 1
    inserted_task_attempts = sum(
        1
        for row in task_attempt_rows
        if (str(row["task_id"]), int(row["attempt"])) not in existing_task_keys
    )
    return DatabaseImportResult(
        db_path=db_path,
        run_id=str(run_row["run_id"]),
        inserted_runs=inserted_runs,
        inserted_task_attempts=inserted_task_attempts,
    )


def _build_run_row(manifest: dict[str, Any], run_root: Path) -> dict[str, Any]:
    model = manifest.get("model", {}) if isinstance(manifest.get("model"), dict) else {}
    agent = manifest.get("agent", {}) if isinstance(manifest.get("agent"), dict) else {}
    evaluation = (
        manifest.get("evaluation", {}) if isinstance(manifest.get("evaluation"), dict) else {}
    )
    run_id = str(manifest.get("run_id") or run_root.name)
    return {
        "run_id": run_id,
        "benchmark": _clean_text(manifest.get("benchmark")),
        "condition": _clean_text(manifest.get("condition")),
        "dataset_path": _clean_text(manifest.get("dataset_path")),
        "split": _clean_text(manifest.get("split")),
        "agent": _clean_text(agent.get("id")),
        "model_canonical": _clean_text(model.get("canonical_name")),
        "model_resolved": _clean_text(model.get("resolved_name")),
        "max_workers": _coerce_int(manifest.get("max_workers")),
        "attempts": _coerce_int(manifest.get("attempts")),
        "evaluation_enabled": 1 if bool(evaluation.get("enabled")) else 0,
        "started_at_utc": _clean_text(manifest.get("started_at_utc")),
        "run_root": str(run_root),
    }


def _load_task_attempt_rows(appendix_csv: Path, run_id: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with appendix_csv.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, skipinitialspace=True)
        for raw_row in reader:
            normalized = {_normalize_key(key): value for key, value in raw_row.items() if key is not None}
            task_id = _clean_text(normalized.get("task_id"))
            attempt = _coerce_int(normalized.get("attempt"))
            if not task_id or attempt is None:
                continue
            row: dict[str, Any] = {
                "run_id": run_id,
                "task_id": task_id,
                "attempt": attempt,
            }
            for field in TASK_ATTEMPT_FIELDS:
                if field in {"task_id", "attempt"}:
                    continue
                value = normalized.get(field)
                if field in INTEGER_FIELDS:
                    row[field] = _coerce_int(value)
                elif field in REAL_FIELDS:
                    row[field] = _coerce_float(value)
                else:
                    row[field] = _clean_text(value)
            rows.append(row)
    return rows


def _normalize_key(key: str) -> str:
    return key.strip()


def _clean_text(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        return str(value)
    text = value.strip()
    return text or None


def _coerce_int(value: Any) -> int | None:
    text = _clean_text(value)
    if text is None:
        return None
    return int(text)


def _coerce_float(value: Any) -> float | None:
    text = _clean_text(value)
    if text is None:
        return None
    return float(text)
