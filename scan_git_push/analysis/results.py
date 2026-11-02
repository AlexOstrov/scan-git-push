"""Conversion between database rows and report objects."""

from __future__ import annotations

from typing import Any, Iterable

from scan_git_push.analyzer import AnalysisResult
from scan_git_push.git.models import FileChange


def _file_from_row(row: dict[str, Any]) -> FileChange:
    return FileChange(
        path=row["path"],
        status=row.get("file_status") or "modified",
        additions=int(row.get("additions") or 0),
        deletions=int(row.get("deletions") or 0),
        is_binary=bool(row.get("is_binary")),
        truncated=bool(row.get("truncated")),
    )


def result_from_row(row: dict[str, Any]) -> AnalysisResult:
    """Build an :class:`AnalysisResult` from a ``get_analyses`` row."""
    files = [_file_from_row(file) for file in row.get("files", [])]
    return AnalysisResult(
        sha=row["sha"],
        author=row.get("author") or "unknown",
        date=row.get("commit_date") or row.get("analyzed_at") or "",
        message=row.get("message") or "",
        files=[file.path for file in files],
        summary=row.get("summary") or "",
        chunks_analyzed=int(row.get("chunks_count") or 0),
        file_changes=files,
        additions=int(row.get("additions") or 0),
        deletions=int(row.get("deletions") or 0),
        provider=row.get("provider") or "",
        model=row.get("model") or "",
        project=row.get("project_url") or "",
        project_name="",
    )


def results_from_rows(rows: Iterable[dict[str, Any]]) -> list[AnalysisResult]:
    return [result_from_row(row) for row in rows]
