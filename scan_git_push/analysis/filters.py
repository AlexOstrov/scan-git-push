"""Glob filters with ``**`` support, shared with the ``scan`` mode."""

from __future__ import annotations

import re
from functools import lru_cache
from typing import Iterable, Sequence

from scan_git_push.git.models import FileChange


@lru_cache(maxsize=512)
def glob_to_regex(pattern: str) -> re.Pattern[str]:
    """Translate a git-style glob into a case-insensitive regular expression."""
    pattern = pattern.strip().replace("\\", "/")
    if not pattern:
        return re.compile("^$")
    if "/" not in pattern:
        pattern = f"**/{pattern}"

    out: list[str] = []
    index = 0
    while index < len(pattern):
        if pattern.startswith("**/", index):
            out.append("(?:.*/)?")
            index += 3
        elif pattern.startswith("**", index):
            out.append(".*")
            index += 2
        elif pattern[index] == "*":
            out.append("[^/]*")
            index += 1
        elif pattern[index] == "?":
            out.append("[^/]")
            index += 1
        else:
            out.append(re.escape(pattern[index]))
            index += 1
    return re.compile("^" + "".join(out) + "$", re.IGNORECASE)


def path_matches(path: str, pattern: str) -> bool:
    return bool(glob_to_regex(pattern).match(path.replace("\\", "/")))


def matches_any(path: str, patterns: Sequence[str]) -> bool:
    return any(path_matches(path, pattern) for pattern in patterns)


def is_excluded(path: str, patterns: Iterable[str]) -> bool:
    return matches_any(path, list(patterns))


def filter_files(files: Iterable[FileChange], exclude: Sequence[str]) -> list[FileChange]:
    """Drop files matching the analysis exclude list (lock files, bundles, ...)."""
    if not exclude:
        return list(files)
    return [file for file in files if not is_excluded(file.path, exclude)]
