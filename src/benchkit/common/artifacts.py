from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import shutil
import uuid


@dataclass(slots=True)
class RunLayout:
    run_id: str
    run_root: Path
    attempts_root: Path
    manifest_path: Path
    instances_path: Path
    config_snapshot_path: Path
    summary_path: Path

    def attempt_dir(self, attempt: int) -> Path:
        return self.attempts_root / f"attempt-{attempt:02d}"


def create_run_layout(output_root: Path, benchmark: str) -> RunLayout:
    now = datetime.now(timezone.utc)
    day_bucket = now.strftime("%Y%m%d")
    run_id = f"{now.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"

    run_root = output_root / benchmark / day_bucket / run_id
    attempts_root = run_root / "attempts"
    attempts_root.mkdir(parents=True, exist_ok=True)

    return RunLayout(
        run_id=run_id,
        run_root=run_root,
        attempts_root=attempts_root,
        manifest_path=run_root / "run_manifest.json",
        instances_path=run_root / "instances.jsonl",
        config_snapshot_path=run_root / "config.toml",
        summary_path=run_root / "summary.json",
    )


def snapshot_config(source_path: Path, destination_path: Path) -> None:
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source_path, destination_path)
