#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from generate_benchmark_run_row import clean, load_summary_rows, resolve_summary_source, select_row
from upload_trace_jsonl_to_drive import extract_folder_id, get_access_token, upload_to_drive


@dataclass(frozen=True, slots=True)
class ReportFileUpload:
    path: Path
    name: str


@dataclass(frozen=True, slots=True)
class DriveFolder:
    id: str
    link: str
    name: str


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Upload appendix report files to a new Google Drive child folder."
    )
    parser.add_argument(
        "report_path",
        type=Path,
        help="Appendix report directory, run_summary.csv, or run_summary.jsonl.",
    )
    parser.add_argument(
        "--drive-folder-url",
        required=True,
        help="Parent Google Drive folder URL or raw folder ID.",
    )
    parser.add_argument(
        "--run-id",
        help="Select a run_id when the report summary contains multiple rows.",
    )
    parser.add_argument(
        "--folder-name",
        help="Child Drive folder name. Defaults to the selected run_id.",
    )
    parser.add_argument(
        "--access-token",
        help="Google OAuth access token with Drive file creation scope.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print resolved folder/files without calling Google Drive.",
    )
    args = parser.parse_args()

    try:
        source = resolve_summary_source(args.report_path)
        row = select_row(load_summary_rows(source), args.run_id)
        parent_folder_id = extract_folder_id(args.drive_folder_url)
        report_dir = source.parent
        folder_name = clean(args.folder_name) or default_report_folder_name(row, report_dir)
        uploads = resolve_report_file_uploads(report_dir)
        if args.dry_run:
            print(f"folder\t{parent_folder_id}\t{folder_name}")
            for upload in uploads:
                print(f"file\t{upload.path}\t{folder_name}\t{upload.name}")
            return 0

        token = get_access_token(args.access_token)
        folder = create_drive_folder(
            folder_name=folder_name,
            parent_folder_id=parent_folder_id,
            access_token=token,
        )
        for upload in uploads:
            upload_to_drive(
                file_path=upload.path,
                folder_id=folder.id,
                upload_name=upload.name,
                access_token=token,
            )
    except (RuntimeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    print(folder.link)
    return 0


def default_report_folder_name(row: dict[str, Any], report_dir: Path) -> str:
    return clean(row.get("run_id")) or report_dir.name


def resolve_report_file_uploads(report_dir: Path) -> list[ReportFileUpload]:
    report_dir = report_dir.expanduser()
    if not report_dir.is_dir():
        raise ValueError(f"report directory does not exist: {report_dir}")

    uploads = [
        ReportFileUpload(path=path, name=path.name)
        for path in sorted(report_dir.iterdir(), key=lambda item: item.name)
        if path.is_file() and is_appendix_report_file(path)
    ]
    if not uploads:
        raise ValueError(f"no run_summary.* or appendix_* files found in {report_dir}")
    return uploads


def is_appendix_report_file(path: Path) -> bool:
    return path.name in {"run_summary.csv", "run_summary.jsonl"} or path.name.startswith(
        "appendix_"
    )


def create_drive_folder(
    *,
    folder_name: str,
    parent_folder_id: str,
    access_token: str,
) -> DriveFolder:
    metadata = {
        "name": folder_name,
        "mimeType": "application/vnd.google-apps.folder",
        "parents": [parent_folder_id],
    }
    body = json.dumps(metadata).encode("utf-8")
    request = urllib.request.Request(
        "https://www.googleapis.com/drive/v3/files?fields=id,name,webViewLink",
        data=body,
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json; charset=UTF-8",
            "Content-Length": str(len(body)),
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"Drive folder creation failed ({exc.code}): {drive_error_details(exc)}") from exc

    folder_id = clean(payload.get("id"))
    if not folder_id:
        raise RuntimeError(f"Drive folder creation response did not include an id: {payload}")
    return DriveFolder(
        id=folder_id,
        link=clean(payload.get("webViewLink"))
        or f"https://drive.google.com/drive/folders/{folder_id}",
        name=clean(payload.get("name")) or folder_name,
    )


def drive_error_details(exc: urllib.error.HTTPError) -> str:
    details = exc.read().decode("utf-8", errors="replace")
    if exc.code == 403 and "ACCESS_TOKEN_SCOPE_INSUFFICIENT" in details:
        return (
            "Google rejected the token because it lacks Drive upload scope. "
            "Run `gcloud auth login --enable-gdrive-access --force` and try again."
        )
    return details


if __name__ == "__main__":
    raise SystemExit(main())
