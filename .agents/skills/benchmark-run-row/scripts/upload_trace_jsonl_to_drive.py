#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import mimetypes
import os
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from generate_benchmark_run_row import load_summary_rows, resolve_summary_source, select_row


@dataclass(frozen=True, slots=True)
class TraceUpload:
    path: Path
    name: str


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Upload raw agent transcript JSONL files from a benchmark report to a "
            "Google Drive folder."
        )
    )
    parser.add_argument(
        "report_path",
        type=Path,
        help="Appendix report directory, run_summary.csv, or run_summary.jsonl.",
    )
    parser.add_argument(
        "--drive-folder-url",
        required=True,
        help="Google Drive folder URL or raw folder ID.",
    )
    parser.add_argument(
        "--run-id",
        help="Select a run_id when the report summary contains multiple rows.",
    )
    parser.add_argument(
        "--attempt",
        dest="attempt_filters",
        action="append",
        help="Upload only this attempt, e.g. 1, 01, or attempt-01. Repeat for multiple attempts.",
    )
    parser.add_argument(
        "--instance-id",
        dest="instance_id_filters",
        action="append",
        help="Upload only this benchmark instance_id. Repeat for multiple instances.",
    )
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=Path.cwd(),
        help="Repository root for resolving relative trace paths. Defaults to the current directory.",
    )
    parser.add_argument(
        "--name",
        help="Uploaded file name. Only valid when exactly one transcript JSONL is resolved.",
    )
    parser.add_argument(
        "--access-token",
        help="Google OAuth access token with Drive file creation scope.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print resolved uploads without calling Google Drive.",
    )
    args = parser.parse_args()

    try:
        source = resolve_summary_source(args.report_path)
        row = select_row(load_summary_rows(source), args.run_id)
        folder_id = extract_folder_id(args.drive_folder_url)
        uploads = resolve_trace_uploads(
            row,
            args.repo_root.expanduser(),
            name=args.name,
            attempt_filters=args.attempt_filters,
            instance_id_filters=args.instance_id_filters,
        )
        if args.dry_run:
            for upload in uploads:
                print(f"{upload.path}\t{folder_id}\t{upload.name}")
            return 0
        token = get_access_token(args.access_token)
        links = [
            upload_to_drive(
                file_path=upload.path,
                folder_id=folder_id,
                upload_name=upload.name,
                access_token=token,
            )
            for upload in uploads
        ]
    except (RuntimeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    print(";".join(links))
    return 0


def extract_folder_id(value: str) -> str:
    cleaned = value.strip()
    if not cleaned:
        raise ValueError("drive folder URL is empty")
    parsed = urllib.parse.urlparse(cleaned)
    if not parsed.scheme:
        return cleaned

    query = urllib.parse.parse_qs(parsed.query)
    if query.get("id"):
        return query["id"][0]

    match = re.search(r"/folders/([^/?#]+)", parsed.path)
    if match:
        return urllib.parse.unquote(match.group(1))
    raise ValueError(f"could not extract Drive folder ID from: {value}")


def resolve_trace_uploads(
    row: dict[str, Any],
    repo_root: Path,
    *,
    name: str | None = None,
    attempt_filters: list[str] | None = None,
    instance_id_filters: list[str] | None = None,
) -> list[TraceUpload]:
    normalized_attempt_filters = normalize_attempt_filters(attempt_filters)
    normalized_instance_filters = normalize_instance_id_filters(instance_id_filters)
    paths = resolve_raw_stdout_paths(
        row,
        repo_root,
        attempt_filters=normalized_attempt_filters,
        instance_id_filters=normalized_instance_filters,
    )
    if not paths:
        if normalized_instance_filters:
            raise ValueError(
                "no raw agent transcript matched the requested instance_id filter(s)"
            )
        paths = filter_paths_by_attempt(
            parse_path_list(row.get("trace_jsonl_paths")) or parse_path_list(row.get("log_jsonl_link")),
            normalized_attempt_filters,
        )
    if not paths:
        raise ValueError("no agent transcript or trace JSONL path found in selected summary row")
    if name and len(paths) != 1:
        raise ValueError("--name can only be used when one trace JSONL is resolved")

    uploads: list[TraceUpload] = []
    for raw_path in paths:
        if urllib.parse.urlparse(raw_path).scheme in {"http", "https"}:
            raise ValueError(f"trace JSONL is already a URL, not a local file path: {raw_path}")
        path = Path(raw_path).expanduser()
        if not path.is_absolute():
            path = repo_root / path
        if not path.is_file():
            raise ValueError(f"trace JSONL does not exist: {path}")
        uploads.append(
            TraceUpload(
                path=path,
                name=name or default_upload_name(row=row, path=path),
            )
        )
    return uploads


def resolve_raw_stdout_paths(
    row: dict[str, Any],
    repo_root: Path,
    *,
    attempt_filters: set[str] | None = None,
    instance_id_filters: set[str] | None = None,
) -> list[str]:
    paths: list[str] = []
    for raw_trace_path in parse_path_list(row.get("trace_jsonl_paths")) or parse_path_list(
        row.get("log_jsonl_link")
    ):
        if urllib.parse.urlparse(raw_trace_path).scheme in {"http", "https"}:
            continue
        trace_path = Path(raw_trace_path).expanduser()
        if not trace_path.is_absolute():
            trace_path = repo_root / trace_path
        if not trace_path.is_file():
            continue
        if not path_matches_attempt(trace_path, attempt_filters):
            continue
        with trace_path.open("r", encoding="utf-8") as handle:
            for line in handle:
                cleaned = line.strip()
                if not cleaned:
                    continue
                try:
                    trace_row = json.loads(cleaned)
                except json.JSONDecodeError:
                    continue
                metadata = trace_row.get("metadata")
                if not isinstance(metadata, dict):
                    continue
                instance_id = str(trace_row.get("instance_id") or "").strip()
                if instance_id_filters and instance_id not in instance_id_filters:
                    continue
                raw_stdout_path = str(metadata.get("raw_stdout_path") or "").strip()
                if raw_stdout_path:
                    paths.append(raw_stdout_path)
    return list(dict.fromkeys(paths))


def parse_path_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]

    text = str(value).strip()
    if not text:
        return []
    if text.startswith("["):
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, list):
            return [str(item).strip() for item in parsed if str(item).strip()]
    return [item.strip() for item in text.split(";") if item.strip()]


def normalize_attempt_filters(values: list[str] | None) -> set[str] | None:
    if not values:
        return None
    output: set[str] = set()
    for value in values:
        text = str(value or "").strip()
        if not text:
            continue
        if text.startswith("attempt-"):
            output.add(text)
        elif text.isdigit():
            output.add(f"attempt-{int(text):02d}")
        else:
            output.add(text)
    return output or None


def normalize_instance_id_filters(values: list[str] | None) -> set[str] | None:
    if not values:
        return None
    output = {str(value or "").strip() for value in values if str(value or "").strip()}
    return output or None


def filter_paths_by_attempt(paths: list[str], attempt_filters: set[str] | None) -> list[str]:
    if not attempt_filters:
        return paths
    return [path for path in paths if path_matches_attempt(Path(path), attempt_filters)]


def path_matches_attempt(path: Path, attempt_filters: set[str] | None) -> bool:
    if not attempt_filters:
        return True
    attempt = path_attempt(path)
    return bool(attempt and attempt in attempt_filters)


def path_attempt(path: Path) -> str:
    return next((part for part in path.parts if part.startswith("attempt-")), "")


def default_upload_name(*, row: dict[str, Any], path: Path) -> str:
    run_id = str(row.get("run_id") or path.parents[2].name).strip()
    attempt = path_attempt(path)
    if path.name.endswith(".stdout.jsonl"):
        return "_".join(part for part in (run_id, attempt, path.name) if part)
    parts = [part for part in (run_id, attempt, path.name) if part]
    return "_".join(parts)


def get_access_token(explicit_token: str | None) -> str:
    token = (explicit_token or os.environ.get("GOOGLE_OAUTH_ACCESS_TOKEN") or "").strip()
    if token:
        return token
    try:
        completed = subprocess.run(
            ["gcloud", "auth", "print-access-token"],
            check=True,
            text=True,
            capture_output=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError) as exc:
        raise RuntimeError(
            "could not get a Google access token. Run "
            "`gcloud auth login --enable-gdrive-access --force`, or pass "
            "--access-token / GOOGLE_OAUTH_ACCESS_TOKEN."
        ) from exc
    token = completed.stdout.strip()
    if not token:
        raise RuntimeError("gcloud returned an empty access token")
    return token


def upload_to_drive(
    *,
    file_path: Path,
    folder_id: str,
    upload_name: str,
    access_token: str,
) -> str:
    metadata = {"name": upload_name, "parents": [folder_id]}
    mime_type = mimetypes.guess_type(upload_name)[0] or "application/jsonl"
    boundary = "benchkit-upload-boundary"
    body = build_multipart_body(
        metadata=metadata,
        file_bytes=file_path.read_bytes(),
        mime_type=mime_type,
        boundary=boundary,
    )
    request = urllib.request.Request(
        "https://www.googleapis.com/upload/drive/v3/files"
        "?uploadType=multipart&fields=id,name,webViewLink",
        data=body,
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": f"multipart/related; boundary={boundary}",
            "Content-Length": str(len(body)),
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        details = exc.read().decode("utf-8", errors="replace")
        if exc.code == 403 and "ACCESS_TOKEN_SCOPE_INSUFFICIENT" in details:
            details = (
                "Google rejected the token because it lacks Drive upload scope. "
                "Run `gcloud auth login --enable-gdrive-access --force` and try again."
            )
        raise RuntimeError(f"Drive upload failed ({exc.code}): {details}") from exc

    link = str(payload.get("webViewLink") or "").strip()
    if link:
        return link
    file_id = str(payload.get("id") or "").strip()
    if file_id:
        return f"https://drive.google.com/file/d/{file_id}/view"
    raise RuntimeError(f"Drive upload response did not include a file link: {payload}")


def build_multipart_body(
    *,
    metadata: dict[str, Any],
    file_bytes: bytes,
    mime_type: str,
    boundary: str,
) -> bytes:
    metadata_bytes = json.dumps(metadata).encode("utf-8")
    return b"".join(
        [
            f"--{boundary}\r\n".encode("utf-8"),
            b"Content-Type: application/json; charset=UTF-8\r\n\r\n",
            metadata_bytes,
            b"\r\n",
            f"--{boundary}\r\n".encode("utf-8"),
            f"Content-Type: {mime_type}\r\n\r\n".encode("utf-8"),
            file_bytes,
            b"\r\n",
            f"--{boundary}--\r\n".encode("utf-8"),
        ]
    )


if __name__ == "__main__":
    raise SystemExit(main())
