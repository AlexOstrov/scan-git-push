"""Thin wrapper around the ``git`` executable."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Sequence

from scan_git_push.git.errors import (
    GitAuthError,
    GitError,
    GitNotFoundError,
    GitUnavailableError,
)

# Applied to every invocation: UTF-8 paths on Windows, no CRLF rewriting, no
# credential prompts that would hang a non-interactive run.
BASE_CONFIG = [
    "-c",
    "core.quotepath=false",
    "-c",
    "core.longpaths=true",
    "-c",
    "core.autocrlf=false",
    "-c",
    "advice.detachedHead=false",
]

AUTH_MARKERS = (
    "authentication failed",
    "could not read username",
    "could not read password",
    "permission denied",
    "terminal prompts disabled",
    "access denied",
    "403 forbidden",
    "repository not found",
)

NOT_FOUND_MARKERS = (
    "does not appear to be a git repository",
    "not found",
    "no such file or directory",
    "repository does not exist",
)


def git_env() -> dict[str, str]:
    """Return an environment that never blocks on credential prompts."""
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    env.setdefault("GIT_CONFIG_NOSYSTEM", "0")
    env.setdefault("LC_ALL", "C.UTF-8")
    env.setdefault("LANG", "C.UTF-8")
    env.pop("GIT_DIR", None)
    env.pop("GIT_WORK_TREE", None)
    return env


def ensure_git_available() -> None:
    """Raise :class:`GitUnavailableError` when git is not installed."""
    if shutil.which("git") is None:
        raise GitUnavailableError("git executable not found in PATH")


def _classify(stderr: str) -> GitError:
    lowered = stderr.lower()
    if any(marker in lowered for marker in AUTH_MARKERS):
        return GitAuthError(stderr.strip())
    if any(marker in lowered for marker in NOT_FOUND_MARKERS):
        return GitNotFoundError(stderr.strip())
    return GitError(stderr.strip())


def run_git(
    args: Sequence[str],
    cwd: Path | str | None = None,
    check: bool = True,
    timeout: float = 300.0,
) -> str:
    """Run git and return stdout.

    ``check=True`` raises a classified :class:`GitError` on a non-zero exit.
    """
    ensure_git_available()
    command = ["git", *BASE_CONFIG, *args]
    try:
        completed = subprocess.run(
            command,
            cwd=str(cwd) if cwd is not None else None,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=git_env(),
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError as exc:  # pragma: no cover - guarded above
        raise GitUnavailableError("git executable not found") from exc
    except subprocess.TimeoutExpired as exc:
        raise GitError(f"git {' '.join(args)} timed out after {timeout:.0f}s") from exc

    if completed.returncode != 0:
        stderr = completed.stderr or completed.stdout
        error = _classify(stderr)
        if check:
            raise error
        return ""

    return completed.stdout


def popen_git(args: Sequence[str], cwd: Path | str | None = None) -> subprocess.Popen[str]:
    """Start git with piped stdout for streaming reads."""
    ensure_git_available()
    command = ["git", *BASE_CONFIG, *args]
    return subprocess.Popen(
        command,
        cwd=str(cwd) if cwd is not None else None,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=git_env(),
    )


def popen_git_binary(
    args: Sequence[str], cwd: Path | str | None = None
) -> subprocess.Popen[bytes]:
    """Start git with binary stdin/stdout (used by ``cat-file --batch``)."""
    ensure_git_available()
    command = ["git", *BASE_CONFIG, *args]
    return subprocess.Popen(
        command,
        cwd=str(cwd) if cwd is not None else None,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=git_env(),
    )

