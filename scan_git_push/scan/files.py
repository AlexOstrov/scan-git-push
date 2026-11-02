"""File discovery and reading for the ``scan`` mode.

All data is read from the local bare mirror. File contents live only in memory;
only the SHA-256 of the raw blob bytes is persisted.
"""

from __future__ import annotations

import hashlib
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

from loguru import logger

from scan_git_push.analysis.filters import matches_any
from scan_git_push.git.runner import popen_git_binary, run_git

LFS_PREFIX = b"version https://git-lfs.github.com/spec/v1"
BINARY_SNIFF_BYTES = 8192
REGULAR_MODES = {"100644", "100755"}
SYMLINK_MODE = "120000"
SUBMODULE_MODE = "160000"

_WINDOWS_ABS = re.compile(r"^[A-Za-z]:")


class BlobReadError(RuntimeError):
    """A blob could not be read from the mirror."""


@dataclass(frozen=True)
class TreeEntry:
    """One entry of ``git ls-tree -r -l``."""

    mode: str
    obj_type: str
    sha: str
    size: int
    path: str

    @property
    def is_symlink(self) -> bool:
        return self.mode == SYMLINK_MODE

    @property
    def is_submodule(self) -> bool:
        return self.mode == SUBMODULE_MODE or self.obj_type == "commit"

    @property
    def is_regular(self) -> bool:
        return self.mode in REGULAR_MODES and self.obj_type == "blob"


@dataclass(frozen=True)
class CommitInfo:
    """Metadata of the last commit that touched a path."""

    sha: str
    author: str | None
    committer: str | None


@dataclass(frozen=True)
class DecodedBlob:
    text: str
    encoding: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def is_safe_repo_path(path: str) -> bool:
    """Return True for repo-relative POSIX paths without traversal."""
    if not path or "\x00" in path:
        return False
    if path.startswith(("/", "\\")) or _WINDOWS_ABS.match(path):
        return False
    parts = path.replace("\\", "/").split("/")
    return ".." not in parts


def sha256_blob(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def is_binary(data: bytes) -> bool:
    return b"\x00" in data[:BINARY_SNIFF_BYTES]


def is_lfs_pointer(data: bytes) -> bool:
    return data.startswith(LFS_PREFIX)


def decode_blob(
    data: bytes,
    primary: str = "utf-8",
    fallback: str | None = "cp1251",
) -> DecodedBlob | None:
    """Decode raw blob bytes, honouring a UTF-8 BOM and the fallback encoding."""
    if data.startswith(b"\xef\xbb\xbf"):
        try:
            return DecodedBlob(data.decode("utf-8-sig"), "utf-8-sig")
        except UnicodeDecodeError:
            pass

    for encoding in (primary, fallback):
        if not encoding:
            continue
        try:
            return DecodedBlob(data.decode(encoding), encoding)
        except (UnicodeDecodeError, LookupError):
            continue

    # A BOM with a non-UTF-8 primary encoding still has to be reported with the
    # actual encoding name.
    if data.startswith(b"\xef\xbb\xbf"):
        try:
            return DecodedBlob(data.decode("utf-8-sig"), "utf-8-sig")
        except UnicodeDecodeError:
            pass
    return None


def skip_reason(
    entry: TreeEntry,
    include: Sequence[str],
    exclude: Sequence[str],
    exclude_secrets: Sequence[str],
    max_file_size: int,
) -> str | None:
    """Return a human-readable skip reason, or ``None`` when the file is scanned.

    Exclude and secret patterns take precedence over include patterns.
    """
    if entry.is_symlink:
        return "symlink"
    if entry.is_submodule:
        return "submodule"
    if not entry.is_regular:
        return "not a regular file"
    if exclude_secrets and matches_any(entry.path, exclude_secrets):
        return "secret pattern"
    if exclude and matches_any(entry.path, exclude):
        return "excluded pattern"
    if include and not matches_any(entry.path, include):
        return "not included"
    if entry.size > max_file_size:
        return "too large"
    return None


# ---------------------------------------------------------------------------
# Trees and commits
# ---------------------------------------------------------------------------
def list_tree(mirror: Path, branch: str) -> list[TreeEntry]:
    """Return all entries of the branch tip, recursively."""
    output = run_git(["ls-tree", "-r", "-l", "-z", branch], cwd=mirror)
    entries: list[TreeEntry] = []

    for record in output.split("\0"):
        if not record:
            continue
        meta, separator, path = record.partition("\t")
        if not separator:
            continue
        fields = meta.split()
        if len(fields) < 4:
            continue
        mode, obj_type, sha, size_raw = fields[:4]
        try:
            size = int(size_raw)
        except ValueError:
            size = -1
        if not is_safe_repo_path(path):
            logger.warning("Skipping unsafe path from ls-tree: {!r}", path)
            continue
        entries.append(TreeEntry(mode, obj_type, sha, size, path))

    entries.sort(key=lambda entry: entry.path)
    return entries


def last_commit_map(mirror: Path, branch: str) -> dict[str, CommitInfo]:
    """Map every path in the branch to the last commit that touched it.

    A single ``git log --name-only -z`` pass keeps the scan fast even for large
    histories (``dop_tz01.md``, section 13.1).
    """
    output = run_git(
        [
            "log",
            "--format=%x1e%H%x1f%an%x1f%cn",
            "--name-only",
            "-z",
            branch,
        ],
        cwd=mirror,
        timeout=600.0,
    )

    mapping: dict[str, CommitInfo] = {}
    for chunk in output.split("\x1e"):
        if not chunk:
            continue
        header, separator, rest = chunk.partition("\0")
        if not separator:
            continue
        fields = header.split("\x1f")
        if len(fields) < 3:
            continue
        sha, author, committer = fields[:3]

        paths = rest.split("\0")
        # The commit header itself is terminated by ``\0\n``.
        if paths and paths[0].startswith("\n"):
            paths[0] = paths[0][1:]
        elif paths and paths[0].startswith("\r\n"):
            paths[0] = paths[0][2:]

        info = CommitInfo(sha, author or None, committer or None)
        for path in paths:
            if path and path not in mapping:
                mapping[path] = info
    return mapping


# ---------------------------------------------------------------------------
# Blob reading
# ---------------------------------------------------------------------------
class BatchReader:
    """Reads blobs through a single ``git cat-file --batch`` process."""

    def __init__(self, mirror: Path) -> None:
        self._process = popen_git_binary(["cat-file", "--batch"], cwd=mirror)
        if self._process.stdin is None or self._process.stdout is None:
            raise BlobReadError("cannot start git cat-file --batch")

    def __enter__(self) -> BatchReader:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def read(self, sha: str) -> bytes:
        assert self._process.stdin is not None
        assert self._process.stdout is not None
        try:
            self._process.stdin.write(sha.encode("ascii") + b"\n")
            self._process.stdin.flush()
            header = self._process.stdout.readline()
            if not header:
                raise BlobReadError(f"git cat-file closed unexpectedly for {sha}")
            parts = header.split()
            if len(parts) < 2 or parts[1] == b"missing":
                raise BlobReadError(f"object {sha} is missing")
            if len(parts) < 3:
                raise BlobReadError(f"unexpected cat-file header: {header!r}")
            size = int(parts[2])
            data = self._process.stdout.read(size)
            if len(data) != size:
                raise BlobReadError(f"short read for {sha}: {len(data)}/{size}")
            self._process.stdout.read(1)  # trailing newline
            return data
        except (OSError, ValueError) as exc:
            raise BlobReadError(f"cannot read object {sha}: {exc}") from exc

    def close(self) -> None:
        process = self._process
        try:
            if process.stdin is not None and not process.stdin.closed:
                process.stdin.close()
            if process.stdout is not None and not process.stdout.closed:
                process.stdout.close()
            if process.stderr is not None:
                error = process.stderr.read()
                process.stderr.close()
                if process.wait(timeout=10) != 0 and error:
                    logger.debug("cat-file stderr: {}", error.decode("utf-8", "replace"))
        except (OSError, subprocess.TimeoutExpired):
            process.kill()
        except ValueError:  # pragma: no cover - already closed
            pass


def read_blobs(
    mirror: Path, entries: Iterable[TreeEntry]
) -> dict[str, bytes]:
    """Read all requested blobs in one batch process."""
    result: dict[str, bytes] = {}
    with BatchReader(mirror) as reader:
        for entry in entries:
            result[entry.sha] = reader.read(entry.sha)
    return result
