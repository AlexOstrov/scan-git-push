"""Reading commits, file statistics and patches from a local mirror."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Literal, Sequence

from loguru import logger

from scan_git_push.git.models import STATUS_BY_GIT_CODE, CommitData, FileChange
from scan_git_push.git.runner import popen_git, run_git

FIELD = "\x1f"
RECORD = "\x1e"

LOG_FORMAT = FIELD.join(
    [
        "%H",  # sha
        "%an",  # author name
        "%ae",  # author email
        "%aI",  # author date (ISO 8601)
        "%cI",  # committer date (ISO 8601)
        "%cn",  # committer name
        "%s",  # subject
        "%b",  # body
    ]
) + RECORD

MAX_COUNT_SAFETY = 100_000


# --------------------------------------------------------------------- refs
def resolve_refs(
    mirror: Path,
    mode: str | Sequence[str] = "all",
    default_branch: str | None = None,
) -> list[str]:
    """Translate a branch selection into git arguments.

    ``all`` -> ``--branches``; ``default`` -> the mirror HEAD; a list of names ->
    explicit ``refs/heads/<name>`` arguments.
    """
    if isinstance(mode, str):
        if mode == "all":
            return ["--branches"]
        if mode == "default":
            branch = default_branch or _default_branch(mirror)
            if not branch:
                raise ValueError("cannot determine the default branch of the mirror")
            return [f"refs/heads/{branch}"]
        return [f"refs/heads/{mode}"]

    resolved: list[str] = []
    for name in mode:
        name = name.strip()
        if not name:
            continue
        resolved.append(name if name.startswith("refs/") else f"refs/heads/{name}")
    return resolved


def _default_branch(mirror: Path) -> str | None:
    try:
        ref = run_git(["symbolic-ref", "--short", "HEAD"], cwd=mirror).strip()
    except Exception:
        return None
    return ref or None


def branches(mirror: Path) -> list[str]:
    try:
        output = run_git(
            ["for-each-ref", "--format=%(refname:short)", "refs/heads"], cwd=mirror
        )
    except Exception:
        return []
    return sorted(line.strip() for line in output.splitlines() if line.strip())


# ------------------------------------------------------------------ commits
def list_commits(
    mirror: Path,
    days: int | None = None,
    refs: Sequence[str] | None = None,
    first_parent: bool = False,
    max_count: int | None = None,
) -> list[CommitData]:
    """Return non-merge commits from ``refs`` newer than ``days``.

    Filtering uses the committer date, which is also what ``git log --since``
    applies. Duplicates across branches are removed by git itself.
    """
    if days is not None and days <= 0:
        raise ValueError("days must be positive")

    args = ["log", "--no-merges", f"--format={LOG_FORMAT}", "--date=iso-strict"]
    if days is not None:
        args.append(f"--since={days} days ago")
    if first_parent:
        args.append("--first-parent")
    if max_count:
        args.append(f"--max-count={min(max_count, MAX_COUNT_SAFETY)}")
    args.extend(refs or ["--branches"])

    output = run_git(args, cwd=mirror)
    return _parse_log(output)


def _parse_log(output: str) -> list[CommitData]:
    commits: list[CommitData] = []
    for record in output.split(RECORD):
        record = record.strip("\r\n")
        if not record:
            continue
        parts = record.split(FIELD)
        if len(parts) < 8:
            logger.debug("Skipping malformed git log record: {!r}", record[:80])
            continue
        sha, author, email, author_date, commit_date, committer, subject, body = parts[:8]
        message = subject.strip()
        body = body.strip()
        if body:
            message = f"{message}\n\n{body}" if message else body
        commits.append(
            CommitData(
                sha=sha,
                author=author or "unknown",
                date=_parse_date(commit_date),
                author_date=_parse_date(author_date),
                message=message,
                committer=committer,
                email=email,
                is_merge=False,
            )
        )
    return commits


def _parse_date(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return datetime.now(timezone.utc)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    # Git emits ISO 8601 with the committer's local offset; normalize to UTC so
    # stored values and period comparisons are unambiguous and sortable.
    return parsed.astimezone(timezone.utc)


# ------------------------------------------------------------------- numstat
def numstat(mirror: Path, sha: str) -> list[FileChange]:
    """Return the file list of a commit with add/delete counters."""
    names = _name_status(mirror, sha)
    output = run_git(
        [
            "diff-tree",
            "-r",
            "-z",
            "--no-commit-id",
            "--no-renames",
            "--numstat",
            "--root",
            sha,
        ],
        cwd=mirror,
    )

    files: list[FileChange] = []
    for record in output.split("\0"):
        if not record.strip():
            continue
        parts = record.split("\t", 2)
        if len(parts) < 3:
            continue
        added, deleted, path = parts[0], parts[1], parts[2]
        is_binary = added == "-" or deleted == "-"
        files.append(
            FileChange(
                path=path,
                status=names.get(path, "modified"),
                additions=0 if is_binary else _to_int(added),
                deletions=0 if is_binary else _to_int(deleted),
                is_binary=is_binary,
            )
        )
    return files


def _name_status(mirror: Path, sha: str) -> dict[str, str]:
    output = run_git(
        [
            "diff-tree",
            "-r",
            "-z",
            "--no-commit-id",
            "--no-renames",
            "--name-status",
            "--root",
            sha,
        ],
        cwd=mirror,
    )
    tokens = [token for token in output.split("\0")]
    mapping: dict[str, str] = {}
    index = 0
    while index + 1 < len(tokens):
        code = tokens[index].strip()
        path = tokens[index + 1]
        index += 2
        if not code or not path:
            continue
        letter = code[0].upper()
        mapping[path] = STATUS_BY_GIT_CODE.get(letter, "modified")
    return mapping


def _to_int(value: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


# ------------------------------------------------------------------- patches
_DIFF_GIT = "diff --git "
_BINARY = "Binary files "


def read_patches(
    mirror: Path,
    sha: str,
    max_chars_per_file: int = 4_000,
    ordered_paths: Iterable[str] | None = None,
) -> dict[str, FileChange]:
    """Return per-file patches for a commit, truncated to a character budget.

    The stream is read line by line; each file buffer is capped, so a huge diff
    cannot exhaust memory.
    """
    fallback_paths = list(ordered_paths or [])
    fallback_index = 0
    patches: dict[str, FileChange] = {}

    process = popen_git(
        [
            "diff-tree",
            "-r",
            "-p",
            "--no-commit-id",
            "--no-renames",
            "--root",
            "--unified=3",
            sha,
        ],
        cwd=mirror,
    )
    assert process.stdout is not None

    path: str | None = None
    buffer: list[str] = []
    size = 0
    truncated = False
    binary = False
    omitted = 0

    def flush(current: str | None) -> None:
        nonlocal buffer, size, truncated, binary, omitted, fallback_index
        if current is None:
            return
        text = _truncate_patch("".join(buffer), max_chars_per_file) if buffer else ""
        was_truncated = truncated or len("".join(buffer)) > max_chars_per_file
        if omitted:
            text = text.rstrip() + f"\n... [diff truncated: {omitted} line(s) omitted]"
        patches[current] = FileChange(
            path=current,
            patch=text,
            is_binary=binary,
            truncated=was_truncated,
        )
        buffer = []
        size = 0
        truncated = False
        binary = False
        omitted = 0
        fallback_index += 1

    try:
        for raw_line in process.stdout:
            if raw_line.startswith(_DIFF_GIT):
                flush(path)
                path = _parse_diff_path(raw_line) or (
                    fallback_paths[fallback_index]
                    if fallback_index < len(fallback_paths)
                    else f"file-{fallback_index}"
                )
                buffer.append(raw_line)
                size = len(raw_line)
                continue

            if path is None:
                continue

            if raw_line.startswith(_BINARY):
                binary = True

            if truncated:
                omitted += 1
                continue

            buffer.append(raw_line)
            size += len(raw_line)
            if size > max_chars_per_file:
                truncated = True
        flush(path)
    finally:
        stderr = process.stderr.read() if process.stderr is not None else ""
        process.stdout.close()
        if process.stderr is not None:
            process.stderr.close()
        returncode = process.wait()
        if returncode != 0:
            logger.warning("diff-tree for {} failed: {}", sha[:8], stderr.strip())

    return patches


def _parse_diff_path(line: str) -> str | None:
    rest = line[len(_DIFF_GIT) :].rstrip("\r\n")
    tokens = _split_tokens(rest)
    if len(tokens) < 2:
        return None
    candidate = tokens[1]
    if candidate.startswith("b/"):
        candidate = candidate[2:]
    elif candidate.startswith("a/"):
        candidate = candidate[2:]
    return candidate


def _split_tokens(value: str) -> list[str]:
    tokens: list[str] = []
    token: list[str] = []
    index = 0
    in_quotes = False
    while index < len(value):
        char = value[index]
        if in_quotes:
            if char == "\\" and index + 1 < len(value):
                token.append(_unescape_char(value, index))
                index += 2
                continue
            if char == '"':
                in_quotes = False
                tokens.append("".join(token))
                token = []
                index += 1
                continue
            token.append(char)
            index += 1
            continue
        if char == '"':
            in_quotes = True
            index += 1
            continue
        if char == " ":
            if token:
                tokens.append("".join(token))
                token = []
            index += 1
            continue
        token.append(char)
        index += 1
    if token:
        tokens.append("".join(token))
    return tokens


def _unescape_char(value: str, index: int) -> str:
    escaped = value[index + 1]
    simple = {
        "n": "\n",
        "t": "\t",
        "r": "\r",
        '"': '"',
        "\\": "\\",
        "a": "\a",
        "b": "\b",
        "f": "\f",
        "v": "\v",
    }
    if escaped in simple:
        return simple[escaped]
    octal = value[index + 1 : index + 4]
    if re.fullmatch(r"[0-7]{3}", octal):
        return chr(int(octal, 8))
    return escaped


def _truncate_patch(patch: str, limit: int) -> str:
    """Cut a patch at the last hunk boundary that fits the limit."""
    if limit <= 0 or len(patch) <= limit:
        return patch

    head_end = patch.find("\n@@")
    cut = patch.rfind("\n@@", 0, limit)
    if cut > 0 and (head_end < 0 or cut > head_end):
        return patch[:cut].rstrip() + "\n... [diff truncated]"
    return patch[:limit].rstrip() + "\n... [diff truncated]"


# -------------------------------------------------------------------- counts
def commit_stats(mirror: Path, sha: str) -> tuple[int, int]:
    """Return total additions and deletions for a commit."""
    files = numstat(mirror, sha)
    return sum(f.additions for f in files), sum(f.deletions for f in files)


BranchMode = Literal["all", "default"]
