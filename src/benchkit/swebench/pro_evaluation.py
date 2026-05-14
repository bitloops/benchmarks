from __future__ import annotations

from pathlib import Path
from typing import Any
import json

from benchkit.common.io import read_jsonl, write_json


def convert_predictions_jsonl_to_pro_patches(
    prediction_path: Path,
    output_path: Path,
    *,
    prefix: str | None = None,
) -> int:
    rows = read_jsonl(prediction_path.resolve())
    patches: list[dict[str, Any]] = []
    for row in rows:
        instance_id = str(row.get("instance_id", "")).strip()
        if not instance_id:
            continue
        patch = str(row.get("model_patch", row.get("patch", "")))
        payload: dict[str, Any] = {
            "instance_id": instance_id,
            "patch": patch,
        }
        if prefix:
            payload["prefix"] = prefix
        patches.append(payload)

    output_path = output_path.resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(patches, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return len(patches)


def normalize_swebench_pro_eval_results(
    raw_results_path: Path,
    output_path: Path,
) -> dict[str, Any]:
    raw_payload = json.loads(raw_results_path.resolve().read_text(encoding="utf-8"))
    if not isinstance(raw_payload, dict):
        raise ValueError("SWE-bench Pro evaluator results must be a JSON object")

    status_by_id: dict[str, bool] = {}
    for instance_id, solved in raw_payload.items():
        text_id = str(instance_id).strip()
        if not text_id:
            continue
        status_by_id[text_id] = bool(solved)

    submitted_ids = sorted(status_by_id.keys())
    resolved_ids = sorted(instance_id for instance_id, solved in status_by_id.items() if solved)
    unresolved_ids = sorted(
        instance_id for instance_id, solved in status_by_id.items() if not solved
    )
    normalized = {
        "submitted_ids": submitted_ids,
        "resolved_ids": resolved_ids,
        "unresolved_ids": unresolved_ids,
        "error_ids": [],
        "results": [
            {
                "instance_id": instance_id,
                "resolved": solved,
                "status": "solved" if solved else "unsolved",
            }
            for instance_id, solved in sorted(status_by_id.items())
        ],
        "raw_results_path": str(raw_results_path.resolve()),
    }
    write_json(output_path.resolve(), normalized)
    return normalized
