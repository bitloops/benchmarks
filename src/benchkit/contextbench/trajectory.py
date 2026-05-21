from __future__ import annotations

from pathlib import Path
import re
import shlex
from typing import Any
import json


_SED_SPAN_RE = re.compile(
    r"""sed\s+-n\s+['"]?(?P<start>\d+)\s*,\s*(?P<end>\d+)p['"]?\s+(?P<path>[^\s|;]+)"""
)
_HEAD_RE = re.compile(r"""head\s+-n\s+(?P<count>\d+)\s+(?P<path>[^\s|;]+)""")
_TAIL_RE = re.compile(r"""tail\s+-n\s+(?P<count>\d+)\s+(?P<path>[^\s|;]+)""")
_CAT_RE = re.compile(r"""cat\s+(?P<path>[^\s|;]+)""")
_RG_GREP_RE = re.compile(
    r"""(?:rg|grep)\b(?:[^|;]*?)\s+(?P<path>(?:\./|/)?[A-Za-z0-9_.\-/]+(?:\.[A-Za-z0-9_.\-]+)?)$"""
)


def build_contextbench_traj_data(
    *,
    tool_invocations_curated: Any,
    tool_invocations_raw: Any,
) -> dict[str, Any]:
    steps: list[dict[str, Any]] = []
    final_files: set[str] = set()
    final_spans: dict[str, list[dict[str, int]]] = {}
    primary_step_count = 0
    fallback_steps: list[dict[str, Any]] = []
    fallback_final_files: set[str] = set()
    fallback_final_spans: dict[str, list[dict[str, int]]] = {}

    curated_list = (
        tool_invocations_curated if isinstance(tool_invocations_curated, list) else []
    )
    raw_list = tool_invocations_raw if isinstance(tool_invocations_raw, list) else []
    invocations = curated_list if curated_list else raw_list
    raw_by_tool_use_id: dict[str, dict[str, Any]] = {}
    raw_by_call_index: dict[int, dict[str, Any]] = {}
    for raw_invocation in raw_list:
        if not isinstance(raw_invocation, dict):
            continue
        tool_use_id = str(raw_invocation.get("tool_use_id") or "").strip()
        if tool_use_id:
            raw_by_tool_use_id[tool_use_id] = raw_invocation
        call_index = _as_int(raw_invocation.get("call_index"))
        if call_index is not None:
            raw_by_call_index[call_index] = raw_invocation

    for invocation in invocations:
        invocation = _hydrate_invocation_from_raw(
            invocation,
            raw_by_tool_use_id=raw_by_tool_use_id,
            raw_by_call_index=raw_by_call_index,
        )
        edit_files, edit_spans = _extract_edit_fallback(invocation)
        if edit_files or edit_spans:
            fallback_steps.append({"files": sorted(edit_files), "spans": edit_spans})
            for path in edit_files:
                fallback_final_files.add(path)
            for path, path_spans in edit_spans.items():
                existing = fallback_final_spans.setdefault(path, [])
                for span in path_spans:
                    if span not in existing:
                        existing.append(span)

        files, spans = _extract_files_and_spans(invocation)
        if not files and not spans:
            continue
        if _is_primary_retrieval_invocation(invocation):
            primary_step_count += 1
        for path in files:
            final_files.add(path)
        for path, path_spans in spans.items():
            existing = final_spans.setdefault(path, [])
            for span in path_spans:
                if span not in existing:
                    existing.append(span)
        steps.append({"files": sorted(files), "spans": spans})

    if primary_step_count == 0 and fallback_steps:
        steps = fallback_steps
        final_files = fallback_final_files
        final_spans = fallback_final_spans

    return {
        "pred_steps": steps,
        "pred_files": sorted(final_files),
        "pred_spans": final_spans,
    }


def _extract_files_and_spans(invocation: Any) -> tuple[set[str], dict[str, list[dict[str, int]]]]:
    files: set[str] = set()
    spans: dict[str, list[dict[str, int]]] = {}
    if not isinstance(invocation, dict):
        return files, spans

    tool = str(invocation.get("tool") or "").strip().lower()
    if tool == "read":
        path = _invocation_repo_path(invocation)
        if path:
            files.add(path)
            offset = _as_int(invocation.get("offset"))
            limit = _as_int(invocation.get("limit"))
            if offset is not None and limit is not None and limit > 0:
                _add_span(spans, path, offset + 1, offset + limit)
        return files, spans

    if tool == "grep":
        path = _invocation_repo_path(invocation)
        if path:
            files.add(path)
        return files, spans

    if tool == "bash":
        command = str(invocation.get("command") or "").strip()
        parsed_files, parsed_spans = _parse_bash_command(command)
        files.update(parsed_files)
        for path, path_spans in parsed_spans.items():
            for span in path_spans:
                _add_span(spans, path, span["start"], span["end"])
        return files, spans

    return files, spans


def _hydrate_invocation_from_raw(
    invocation: Any,
    *,
    raw_by_tool_use_id: dict[str, dict[str, Any]],
    raw_by_call_index: dict[int, dict[str, Any]],
) -> Any:
    if not isinstance(invocation, dict):
        return invocation
    hydrated = dict(invocation)
    raw_match: dict[str, Any] | None = None

    tool_use_id = str(hydrated.get("tool_use_id") or "").strip()
    if tool_use_id:
        raw_match = raw_by_tool_use_id.get(tool_use_id)
    if raw_match is None:
        call_index = _as_int(hydrated.get("call_index"))
        if call_index is not None:
            raw_match = raw_by_call_index.get(call_index)
    if raw_match is None:
        return hydrated

    if "raw_event" not in hydrated and isinstance(raw_match.get("raw_event"), dict):
        hydrated["raw_event"] = raw_match.get("raw_event")
    if hydrated.get("raw_input_json") in {None, ""} and isinstance(
        raw_match.get("raw_input_json"), str
    ):
        hydrated["raw_input_json"] = raw_match.get("raw_input_json")

    raw_input = raw_match.get("input")
    if isinstance(raw_input, dict):
        for key in ("path", "filePath", "command", "offset", "limit"):
            existing = hydrated.get(key)
            incoming = raw_input.get(key)
            if (existing is None or existing == "") and not (incoming is None or incoming == ""):
                hydrated[key] = incoming
        if hydrated.get("raw_input_json") in {None, ""}:
            try:
                hydrated["raw_input_json"] = json.dumps(raw_input)
            except TypeError:
                pass
    return hydrated


def _is_primary_retrieval_invocation(invocation: Any) -> bool:
    if not isinstance(invocation, dict):
        return False
    tool = str(invocation.get("tool") or "").strip().lower()
    return tool in {"read", "grep", "bash"}


def _extract_edit_fallback(
    invocation: Any,
) -> tuple[set[str], dict[str, list[dict[str, int]]]]:
    files: set[str] = set()
    spans: dict[str, list[dict[str, int]]] = {}
    if not isinstance(invocation, dict):
        return files, spans

    tool = str(invocation.get("tool") or "").strip().lower()
    if tool != "edit":
        return files, spans

    file_path = _normalize_repo_path(
        invocation.get("path")
        or invocation.get("filePath")
    )
    if file_path:
        files.add(file_path)

    patch_text = _extract_patch_text(invocation)
    for span_path, start, end in _parse_unified_diff_spans(patch_text):
        normalized = _normalize_repo_path(span_path)
        if not normalized:
            continue
        files.add(normalized)
        _add_span(spans, normalized, start, end)

    return files, spans


def _extract_patch_text(invocation: dict[str, Any]) -> str:
    raw_event = invocation.get("raw_event")
    if isinstance(raw_event, dict):
        state = raw_event.get("state")
        if isinstance(state, dict):
            metadata = state.get("metadata")
            if isinstance(metadata, dict):
                filediff = metadata.get("filediff")
                if isinstance(filediff, dict):
                    patch = filediff.get("patch")
                    if isinstance(patch, str):
                        return patch
                diff = metadata.get("diff")
                if isinstance(diff, str):
                    return diff
    raw_input_json = invocation.get("raw_input_json")
    if isinstance(raw_input_json, str) and "@@" in raw_input_json:
        return raw_input_json
    return ""


_HUNK_RE = re.compile(
    r"@@\s+-(?P<old_start>\d+)(?:,(?P<old_count>\d+))?\s+\+(?P<new_start>\d+)(?:,(?P<new_count>\d+))?\s+@@"
)


def _parse_unified_diff_spans(patch_text: str) -> list[tuple[str, int, int]]:
    if not patch_text:
        return []

    rows: list[tuple[str, int, int]] = []
    current_path: str | None = None
    for line in patch_text.splitlines():
        if line.startswith("+++ "):
            raw = line[4:].strip()
            if raw.startswith("b/"):
                raw = raw[2:]
            current_path = raw
            continue
        if current_path is None:
            continue
        match = _HUNK_RE.search(line)
        if not match:
            continue
        start = int(match.group("new_start"))
        count_text = match.group("new_count")
        count = int(count_text) if count_text else 1
        if count < 1:
            count = 1
        end = start + count - 1
        rows.append((current_path, start, end))
    return rows


def _parse_bash_command(command: str) -> tuple[set[str], dict[str, list[dict[str, int]]]]:
    files: set[str] = set()
    spans: dict[str, list[dict[str, int]]] = {}
    if not command:
        return files, spans

    normalized = " ".join(command.split())
    for match in _SED_SPAN_RE.finditer(normalized):
        path = _normalize_repo_path(match.group("path"))
        if not path:
            continue
        files.add(path)
        _add_span(
            spans,
            path,
            int(match.group("start")),
            int(match.group("end")),
        )

    for match in _HEAD_RE.finditer(normalized):
        path = _normalize_repo_path(match.group("path"))
        count = int(match.group("count"))
        if not path or count < 1:
            continue
        files.add(path)
        _add_span(spans, path, 1, count)

    for match in _TAIL_RE.finditer(normalized):
        path = _normalize_repo_path(match.group("path"))
        count = int(match.group("count"))
        if not path or count < 1:
            continue
        files.add(path)
        _add_span(spans, path, 1, count)

    for match in _CAT_RE.finditer(normalized):
        path = _normalize_repo_path(match.group("path"))
        if path:
            files.add(path)

    grep_path = _extract_grep_like_path(normalized)
    if grep_path:
        files.add(grep_path)

    return files, spans


def _extract_grep_like_path(command: str) -> str | None:
    match = _RG_GREP_RE.search(command.strip())
    if not match:
        return None
    return _normalize_repo_path(match.group("path"))


def _invocation_repo_path(invocation: dict[str, Any]) -> str | None:
    path = _normalize_repo_path(invocation.get("path") or invocation.get("filePath"))
    if path:
        return path

    raw_input = invocation.get("raw_input_json")
    if not isinstance(raw_input, str) or not raw_input.strip().startswith("{"):
        return None
    try:
        parsed = json.loads(raw_input)
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, dict):
        return None
    return _normalize_repo_path(parsed.get("path") or parsed.get("filePath"))


def _add_span(
    spans: dict[str, list[dict[str, int]]],
    path: str,
    start: int,
    end: int,
) -> None:
    if start < 1:
        start = 1
    if end < start:
        end = start
    path_spans = spans.setdefault(path, [])
    span = {"start": start, "end": end}
    if span not in path_spans:
        path_spans.append(span)


def _as_int(value: Any) -> int | None:
    try:
        if value is None:
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _normalize_repo_path(raw: Any) -> str | None:
    if not isinstance(raw, str):
        return None
    text = raw.strip()
    if not text:
        return None
    if text.startswith("{") or text.startswith("["):
        return None

    token = text
    if " " in token:
        try:
            token = shlex.split(token)[0]
        except ValueError:
            token = token.split(" ", 1)[0]
    token = token.strip().strip("'\"").replace("\\", "/")
    if not token or token in {"-", "."}:
        return None

    token = _strip_benchmark_workspace_prefix(token)

    for prefix in ("/testbed/", "/workspace/", "/app/"):
        if token.startswith(prefix):
            token = token[len(prefix) :]
            if prefix == "/workspace/" and "/" in token:
                token = token.split("/", 1)[1]
            break
    if token.startswith("./"):
        token = token[2:]
    token = token.lstrip("/")
    if not token or token.startswith(".."):
        return None
    if token.endswith(":"):
        token = token[:-1]
    if not token:
        return None

    # Avoid obvious non-file shell tokens.
    if token in {"|", "&&", "||"}:
        return None
    if Path(token).name in {"bash", "sh", "zsh"}:
        return None
    return token


def _strip_benchmark_workspace_prefix(token: str) -> str:
    marker = "/workspaces/"
    if marker not in token:
        return token
    if not (token.startswith("/") or token.startswith("runs/") or "/runs/" in token):
        return token

    suffix = token.split(marker, 1)[1]
    parts = suffix.split("/")
    if len(parts) < 3:
        return token
    return "/".join(parts[2:])
