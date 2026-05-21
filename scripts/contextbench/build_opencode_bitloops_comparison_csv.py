import csv
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
OUTPUT_DIR = ROOT / "reports" / "appendix" / "contextbench_opencode_bitloops_vs_baseline"

BASELINE_PATH = (
    ROOT
    / "reports"
    / "appendix"
    / "opencode_baseline_20260521_clean8_runtimefixed"
    / "appendix_minimal_per_task_log.jsonl"
)


def bitloops_run(
    instance_id: str,
    appendix_dir: str,
    run_date: str,
    run_id: str,
) -> tuple[str, dict[str, Path]]:
    return (
        instance_id,
        {
            "appendix": ROOT
            / "reports"
            / "appendix"
            / appendix_dir
            / "appendix_minimal_per_task_log.jsonl",
            "trace": ROOT
            / "runs"
            / "contextbench_verified"
            / run_date
            / run_id
            / "attempts"
            / "attempt-01"
            / "trace.jsonl",
        },
    )


BITLOOPS_RUNS = dict(
    [
        bitloops_run(
            "SWE-Bench-Verified__python__maintenance__bugfix__e5236b5f",
            "opencode_bitloops_e5236b5f_readiness_fix",
            "20260521",
            "20260521_110734_df9e07",
        ),
        bitloops_run(
            "SWE-Bench-Verified__python__maintenance__bugfix__1409977d",
            "opencode_bitloops_1409977d_retry_readiness_fix",
            "20260521",
            "20260521_101227_5327ce",
        ),
        bitloops_run(
            "SWE-PolyBench__python__maintenance__bugfix__31f7341a",
            "opencode_bitloops_31f7341a_retry_after_login",
            "20260521",
            "20260521_112553_25475c",
        ),
        bitloops_run(
            "SWE-PolyBench__typescript__maintenance__bugfix__42165c4e",
            "opencode_bitloops_42165c4e_readiness_fix",
            "20260521",
            "20260521_114415_e06d3f",
        ),
        bitloops_run(
            "Multi-SWE-Bench__typescript__maintenance__bugfix__5eee261d",
            "opencode_bitloops_5eee261d_readiness_fix",
            "20260521",
            "20260521_114643_316fdb",
        ),
        bitloops_run(
            "Multi-SWE-Bench__go__maintenance__bugfix__d129d52f",
            "opencode_bitloops_d129d52f_readiness_fix",
            "20260521",
            "20260521_115738_deab3a",
        ),
        bitloops_run(
            "SWE-Bench-Verified__python__maintenance__bugfix__28ed0b77",
            "opencode_bitloops_28ed0b77_readiness_fix",
            "20260521",
            "20260521_120343_f14961",
        ),
        bitloops_run(
            "SWE-Bench-Verified__python__maintenance__bugfix__e989ba2d",
            "opencode_bitloops_e989ba2d_rerun_summarygate",
            "20260521",
            "20260521_082049_8ac2d9",
        ),
    ]
)


def load_jsonl_rows(path: Path) -> list[dict]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def load_single_row(path: Path) -> dict:
    rows = load_jsonl_rows(path)
    if len(rows) != 1:
        raise ValueError(f"expected one row in {path}, found {len(rows)}")
    return rows[0]


def load_trace_metadata(path: Path) -> dict:
    rows = load_jsonl_rows(path)
    if not rows:
        return {}
    metadata = rows[0].get("metadata")
    return metadata if isinstance(metadata, dict) else {}


def first_dict(*values):
    for value in values:
        if isinstance(value, dict):
            return value
    return {}


def as_number(value):
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def pct_reduction(baseline, bitloops):
    baseline = as_number(baseline)
    bitloops = as_number(bitloops)
    if baseline in (None, 0) or bitloops is None:
        return None
    return (baseline - bitloops) / baseline * 100


def pct_increase(baseline, bitloops):
    baseline = as_number(baseline)
    bitloops = as_number(bitloops)
    if baseline in (None, 0) or bitloops is None:
        return None
    return (bitloops - baseline) / baseline * 100


def delta(baseline, bitloops):
    baseline = as_number(baseline)
    bitloops = as_number(bitloops)
    if baseline is None or bitloops is None:
        return None
    return bitloops - baseline


def round_value(value, places=3):
    if isinstance(value, float):
        return round(value, places)
    return value


def negated(value):
    value = as_number(value)
    if value is None:
        return None
    return -value


def rows_by_task(path: Path) -> dict[str, dict]:
    return {row["task_id"]: row for row in load_jsonl_rows(path)}


def main() -> None:
    baseline_rows = rows_by_task(BASELINE_PATH)
    comparison_rows = []
    gain_rows = []

    for task_id, paths in BITLOOPS_RUNS.items():
        baseline = baseline_rows[task_id]
        bitloops = load_single_row(paths["appendix"])
        trace_metadata = load_trace_metadata(paths["trace"])
        ready_metadata = first_dict(
            trace_metadata.get("bitloops_init_runtime_ready_shortcut"),
            trace_metadata.get("bitloops_runtime_ready_metadata"),
        )
        summary_rows = ready_metadata.get("summary_rows")
        summary_embedding_rows = ready_metadata.get("summary_embedding_rows")
        missing_summary_embeddings = ready_metadata.get("summary_embedding_rows_missing")
        if (
            missing_summary_embeddings is None
            and isinstance(summary_rows, int)
            and isinstance(summary_embedding_rows, int)
        ):
            missing_summary_embeddings = max(summary_rows - summary_embedding_rows, 0)

        comparison_rows.append(
            {
                "instance_id": task_id,
                "repo": bitloops.get("repo"),
                "baseline_status": baseline.get("status"),
                "bitloops_status": bitloops.get("status"),
                "baseline_agent_runtime_s": baseline.get("runtime_sec"),
                "bitloops_agent_runtime_s": bitloops.get("runtime_sec"),
                "bitloops_setup_s_excluded": round_value(
                    trace_metadata.get("bitloops_setup_elapsed_ms", 0) / 1000,
                    3,
                ),
                "baseline_tool_calls": baseline.get("tool_calls"),
                "bitloops_tool_calls": bitloops.get("tool_calls"),
                "baseline_shell_commands": baseline.get("shell_commands"),
                "bitloops_shell_commands": bitloops.get("shell_commands"),
                "baseline_file_reads": baseline.get("file_reads"),
                "bitloops_file_reads": bitloops.get("file_reads"),
                "baseline_search_actions": baseline.get("search_actions"),
                "bitloops_search_actions": bitloops.get("search_actions"),
                "baseline_input_tokens": baseline.get("input_tokens"),
                "bitloops_input_tokens": bitloops.get("input_tokens"),
                "baseline_output_tokens": baseline.get("output_tokens"),
                "bitloops_output_tokens": bitloops.get("output_tokens"),
                "baseline_total_processed_tokens": baseline.get("total_processed_tokens"),
                "bitloops_total_processed_tokens": bitloops.get("total_processed_tokens"),
                "baseline_final_line_coverage": baseline.get("contextbench_final_line_coverage"),
                "bitloops_final_line_coverage": bitloops.get("contextbench_final_line_coverage"),
                "baseline_final_line_precision": baseline.get("contextbench_final_line_precision"),
                "bitloops_final_line_precision": bitloops.get("contextbench_final_line_precision"),
                "baseline_final_span_coverage": baseline.get("contextbench_final_span_coverage"),
                "bitloops_final_span_coverage": bitloops.get("contextbench_final_span_coverage"),
                "baseline_final_span_precision": baseline.get("contextbench_final_span_precision"),
                "bitloops_final_span_precision": bitloops.get("contextbench_final_span_precision"),
                "baseline_traj_auc_line": baseline.get("contextbench_traj_auc_line"),
                "bitloops_traj_auc_line": bitloops.get("contextbench_traj_auc_line"),
                "baseline_line_redundancy": baseline.get("contextbench_traj_redundancy_line"),
                "bitloops_line_redundancy": bitloops.get("contextbench_traj_redundancy_line"),
                "bitloops_summary_rows": summary_rows,
                "bitloops_summary_embedding_rows": summary_embedding_rows,
                "bitloops_missing_summary_embeddings": missing_summary_embeddings,
            }
        )

        gain_rows.append(
            {
                "instance_id": task_id,
                "repo": bitloops.get("repo"),
                "status_transition": f"{baseline.get('status')} -> {bitloops.get('status')}",
                "runtime_reduction_s": round_value(
                    negated(delta(baseline.get("runtime_sec"), bitloops.get("runtime_sec"))),
                    3,
                ),
                "runtime_reduction_pct": round_value(
                    pct_reduction(baseline.get("runtime_sec"), bitloops.get("runtime_sec")),
                    3,
                ),
                "tool_calls_reduction_pct": round_value(
                    pct_reduction(baseline.get("tool_calls"), bitloops.get("tool_calls")),
                    3,
                ),
                "shell_commands_reduction_pct": round_value(
                    pct_reduction(baseline.get("shell_commands"), bitloops.get("shell_commands")),
                    3,
                ),
                "file_reads_reduction_pct": round_value(
                    pct_reduction(baseline.get("file_reads"), bitloops.get("file_reads")),
                    3,
                ),
                "search_actions_reduction_pct": round_value(
                    pct_reduction(baseline.get("search_actions"), bitloops.get("search_actions")),
                    3,
                ),
                "input_tokens_reduction_pct": round_value(
                    pct_reduction(baseline.get("input_tokens"), bitloops.get("input_tokens")),
                    3,
                ),
                "total_processed_tokens_reduction_pct": round_value(
                    pct_reduction(
                        baseline.get("total_processed_tokens"),
                        bitloops.get("total_processed_tokens"),
                    ),
                    3,
                ),
                "final_line_coverage_gain_pct": round_value(
                    pct_increase(
                        baseline.get("contextbench_final_line_coverage"),
                        bitloops.get("contextbench_final_line_coverage"),
                    ),
                    3,
                ),
                "final_line_precision_gain_pct": round_value(
                    pct_increase(
                        baseline.get("contextbench_final_line_precision"),
                        bitloops.get("contextbench_final_line_precision"),
                    ),
                    3,
                ),
                "final_span_coverage_gain_pct": round_value(
                    pct_increase(
                        baseline.get("contextbench_final_span_coverage"),
                        bitloops.get("contextbench_final_span_coverage"),
                    ),
                    3,
                ),
                "final_span_precision_gain_pct": round_value(
                    pct_increase(
                        baseline.get("contextbench_final_span_precision"),
                        bitloops.get("contextbench_final_span_precision"),
                    ),
                    3,
                ),
                "traj_auc_line_gain_pct": round_value(
                    pct_increase(
                        baseline.get("contextbench_traj_auc_line"),
                        bitloops.get("contextbench_traj_auc_line"),
                    ),
                    3,
                ),
                "line_redundancy_reduction_pct": round_value(
                    pct_reduction(
                        baseline.get("contextbench_traj_redundancy_line"),
                        bitloops.get("contextbench_traj_redundancy_line"),
                    ),
                    3,
                ),
            }
        )

    numeric_gain_columns = [
        column
        for column in gain_rows[0]
        if column not in {"instance_id", "repo", "status_transition"}
    ]
    average_row = {
        "instance_id": "MEAN_OF_RUNS",
        "repo": f"n={len(BITLOOPS_RUNS)}",
        "status_transition": (
            f"{sum(row['status_transition'].endswith(' -> solved') for row in gain_rows)}/"
            f"{len(BITLOOPS_RUNS)} solved -> solved"
        ),
    }
    for column in numeric_gain_columns:
        values = [row[column] for row in gain_rows if isinstance(row.get(column), (int, float))]
        average_row[column] = round_value(sum(values) / len(values), 3) if values else None
    gain_rows.append(average_row)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    comparison_path = OUTPUT_DIR / "run_comparison.csv"
    gains_path = OUTPUT_DIR / "percent_gains.csv"

    with comparison_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(comparison_rows[0]))
        writer.writeheader()
        writer.writerows(comparison_rows)

    with gains_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(gain_rows[0]))
        writer.writeheader()
        writer.writerows(gain_rows)

    print(comparison_path)
    print(gains_path)


if __name__ == "__main__":
    main()
