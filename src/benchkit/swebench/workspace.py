from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
import subprocess
import time
import uuid

from benchkit.swebench.types import BenchmarkInstance


@dataclass(slots=True)
class WorkspacePrepResult:
    status: str
    workspace_path: Path | None
    repo_url: str | None
    elapsed_ms: int
    isolation_mode: str = "shared_repo_commit"
    error: str | None = None

    def to_metadata(self) -> dict[str, str | int | None]:
        return {
            "status": self.status,
            "workspace_path": str(self.workspace_path) if self.workspace_path else None,
            "repo_url": self.repo_url,
            "elapsed_ms": self.elapsed_ms,
            "isolation_mode": self.isolation_mode,
            "error": self.error,
        }


def prepare_instance_workspace(
    instance: BenchmarkInstance,
    run_root: Path,
    repo_url_template: str,
    git_bin: str,
    timeout_seconds: int,
    workspace_root: Path | None = None,
    isolation_mode: str = "shared_repo_commit",
    run_id: str | None = None,
    attempt: int | None = None,
) -> WorkspacePrepResult:
    start = time.time()
    base = workspace_root.resolve() if workspace_root else (run_root / "workspaces").resolve()
    base.mkdir(parents=True, exist_ok=True)

    repo_slug = _sanitize_repo_slug(instance.repo)
    commit_slug = _sanitize_commit(instance.base_commit)
    target = _resolve_workspace_target(
        base=base,
        repo_slug=repo_slug,
        commit_slug=commit_slug,
        instance_id=instance.instance_id,
        isolation_mode=isolation_mode,
        run_id=run_id,
        attempt=attempt,
    )
    repo_url = _resolve_repo_url(instance.repo, repo_url_template)

    try:
        if _is_checkout_ready(target, instance.base_commit, git_bin, timeout_seconds):
            return WorkspacePrepResult(
                status="reused",
                workspace_path=target,
                repo_url=repo_url,
                elapsed_ms=int((time.time() - start) * 1000),
                isolation_mode=isolation_mode,
            )

        if target.exists():
            target = target.with_name(f"{target.name}_{uuid.uuid4().hex[:6]}")

        target.parent.mkdir(parents=True, exist_ok=True)
        _run([git_bin, "clone", repo_url, str(target)], timeout_seconds)
        _checkout_commit(target, instance.base_commit, git_bin, timeout_seconds)

        return WorkspacePrepResult(
            status="prepared",
            workspace_path=target,
            repo_url=repo_url,
            elapsed_ms=int((time.time() - start) * 1000),
            isolation_mode=isolation_mode,
        )
    except Exception as exc:  # noqa: BLE001
        return WorkspacePrepResult(
            status="error",
            workspace_path=None,
            repo_url=repo_url,
            elapsed_ms=int((time.time() - start) * 1000),
            isolation_mode=isolation_mode,
            error=str(exc),
        )


def _resolve_workspace_target(
    *,
    base: Path,
    repo_slug: str,
    commit_slug: str,
    instance_id: str,
    isolation_mode: str,
    run_id: str | None,
    attempt: int | None,
) -> Path:
    if isolation_mode == "attempt_scoped":
        run_slug = _sanitize_path_segment(run_id, fallback="unknown_run")
        instance_slug = _sanitize_path_segment(instance_id, fallback="unknown_instance")
        attempt_slug = _sanitize_attempt(attempt)
        return base / "_isolated" / run_slug / repo_slug / commit_slug / instance_slug / attempt_slug
    if isolation_mode == "task_scoped":
        run_slug = _sanitize_path_segment(run_id, fallback="unknown_run")
        instance_slug = _sanitize_path_segment(instance_id, fallback="unknown_instance")
        return base / "_isolated" / run_slug / repo_slug / commit_slug / instance_slug
    return base / repo_slug / commit_slug


def _checkout_commit(path: Path, commit: str, git_bin: str, timeout_seconds: int) -> None:
    direct = _run(
        [git_bin, "-C", str(path), "checkout", "--detach", commit],
        timeout_seconds,
        check=False,
    )
    if direct.returncode == 0:
        return
    _run([git_bin, "-C", str(path), "fetch", "--all", "--tags", "--prune"], timeout_seconds)
    _run([git_bin, "-C", str(path), "checkout", "--detach", commit], timeout_seconds)


def _is_checkout_ready(path: Path, commit: str, git_bin: str, timeout_seconds: int) -> bool:
    if not path.exists():
        return False
    git_dir = path / ".git"
    if not git_dir.exists():
        return False
    completed = _run(
        [git_bin, "-C", str(path), "rev-parse", "HEAD"],
        timeout_seconds,
        check=False,
    )
    if completed.returncode != 0:
        return False
    head = completed.stdout.strip()
    return head.startswith(commit) or commit.startswith(head)


def _resolve_repo_url(repo: str, template: str) -> str:
    repo = repo.strip()
    if re.match(r"^(https?|git)://", repo) or repo.startswith("git@"):
        return repo
    return template.format(repo=repo)


def _sanitize_repo_slug(repo: str) -> str:
    cleaned = repo.strip().replace("/", "__")
    return re.sub(r"[^A-Za-z0-9_.-]", "_", cleaned)[:180]


def _sanitize_commit(commit: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9]", "", commit.strip())
    return cleaned[:40] if cleaned else "unknown_commit"


def _sanitize_path_segment(value: str | None, fallback: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]", "_", str(value or "").strip())
    return cleaned[:120] if cleaned else fallback


def _sanitize_attempt(attempt: int | None) -> str:
    if isinstance(attempt, int) and attempt > 0:
        return f"attempt-{attempt:02d}"
    return "attempt-unknown"


def _run(
    command: list[str],
    timeout_seconds: int,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        command,
        text=True,
        capture_output=True,
        timeout=timeout_seconds,
        check=False,
    )
    if check and completed.returncode != 0:
        stderr = completed.stderr.strip()
        raise RuntimeError(
            f"Command failed (exit={completed.returncode}): {' '.join(command)} :: {stderr}"
        )
    return completed
