"""Diff chunking for large commits.

A file patch is limited by ``MAX_PATCH_CHARS_PER_FILE`` before chunking, and a
single oversized file is split at hunk boundaries instead of being sent whole.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

from scan_git_push.git.models import FileChange


@dataclass
class DiffChunk:
    """One LLM-sized piece of a commit diff."""

    index: int
    text: str
    paths: list[str] = field(default_factory=list)


def truncate_patch(patch: str, limit: int) -> tuple[str, bool]:
    """Cut a patch at a hunk boundary close to *limit*."""
    if limit <= 0 or len(patch) <= limit:
        return patch, False

    cut = patch.rfind("\n@@", 0, limit)
    if cut <= 0:
        cut = limit
    return patch[:cut].rstrip() + "\n... [diff truncated]", True


def split_hunks(patch: str, limit: int) -> list[str]:
    """Split one file patch into pieces no larger than *limit*."""
    header_end = patch.find("\n@@")
    header = patch if header_end < 0 else patch[: header_end + 1]
    hunks = _hunks(patch)

    if not hunks:
        text, _ = truncate_patch(patch, limit)
        return [text]

    pieces: list[str] = []
    current: list[str] = []
    size = len(header)

    for hunk in hunks:
        if current and size + len(hunk) > limit:
            pieces.append(header + "".join(current))
            current = []
            size = len(header)
        if len(hunk) > limit:
            # A single hunk larger than the budget: hard-truncate it.
            hunk, _ = truncate_patch(hunk, limit - len(header))
        current.append(hunk)
        size += len(hunk)

    if current:
        pieces.append(header + "".join(current))

    total = len(pieces)
    if total > 1:
        pieces = [f"{piece}\n# part {index}/{total}\n" for index, piece in enumerate(pieces, 1)]
    return pieces


def _hunks(patch: str) -> list[str]:
    hunks: list[str] = []
    current: list[str] = []
    for line in patch.splitlines(keepends=True):
        if line.startswith("@@"):
            if current:
                hunks.append("".join(current))
            current = [line]
        elif current:
            current.append(line)
    if current:
        hunks.append("".join(current))
    return hunks


def build_chunks(
    files: Iterable[FileChange],
    max_chunk_chars: int = 12_000,
    max_patch_chars_per_file: int = 4_000,
) -> list[DiffChunk]:
    """Group file patches into LLM-sized chunks."""
    chunks: list[DiffChunk] = []
    current: list[str] = []
    current_paths: list[str] = []
    current_size = 0

    def flush() -> None:
        nonlocal current, current_paths, current_size
        if current:
            chunks.append(
                DiffChunk(
                    index=len(chunks),
                    text="\n\n".join(current),
                    paths=list(current_paths),
                )
            )
        current = []
        current_paths = []
        current_size = 0

    for file in files:
        if not file.patch or file.is_binary:
            continue

        patch, _ = truncate_patch(file.patch, max_patch_chars_per_file)

        if len(patch) > max_chunk_chars:
            flush()
            for piece in split_hunks(patch, max_chunk_chars):
                chunks.append(
                    DiffChunk(index=len(chunks), text=piece, paths=[file.path])
                )
            continue

        if current_size + len(patch) > max_chunk_chars:
            flush()

        current.append(patch)
        current_paths.append(file.path)
        current_size += len(patch)

    flush()
    for index, chunk in enumerate(chunks):
        chunk.index = index
    return chunks


def describe_files(files: Iterable[FileChange]) -> str:
    """Human-readable file list for the LLM prompt."""
    lines: list[str] = []
    for file in files:
        flags = []
        if file.is_binary:
            flags.append("binary")
        if file.truncated:
            flags.append("diff truncated")
        suffix = f" [{', '.join(flags)}]" if flags else ""
        lines.append(
            f"  - {file.path} ({file.status}, +{file.additions} -{file.deletions}){suffix}"
        )
    return "\n".join(lines) if lines else "  (нет изменённых файлов)"
