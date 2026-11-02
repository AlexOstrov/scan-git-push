"""Link analyzed commits to requirement markers stored in ``scan.db``.

The link uses ``git_project.url_key``, which is identical in both databases.
Only the current tip state of ``scan.db`` is used (see ``dop_tz01.md``), so the
result answers "which requirements live in the files touched by this commit".
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Iterable, Sequence

from loguru import logger

from scan_git_push.analyzer import RequirementRef


class RequirementLinker:
    """Read-only access to requirement markers from ``scan.db``."""

    def __init__(self, scan_db_path: Path | None) -> None:
        self.scan_db_path = Path(scan_db_path) if scan_db_path else None
        self._warned = False

    @property
    def available(self) -> bool:
        return bool(self.scan_db_path and self.scan_db_path.exists())

    def _connect(self) -> sqlite3.Connection | None:
        if not self.available:
            if not self._warned:
                logger.debug("scan.db not found — requirement linking is skipped")
                self._warned = True
            return None
        assert self.scan_db_path is not None
        try:
            conn = sqlite3.connect(
                f"file:{self.scan_db_path.as_posix()}?mode=ro", uri=True, timeout=5.0
            )
            conn.row_factory = sqlite3.Row
            return conn
        except sqlite3.Error as exc:
            logger.warning("Cannot open scan.db for requirement linking: {}", exc)
            return None

    def requirements_for(
        self, project_key: str, paths: Sequence[str]
    ) -> list[RequirementRef]:
        """Return requirements whose markers are located in *paths*."""
        if not paths:
            return []
        conn = self._connect()
        if conn is None:
            return []
        try:
            placeholders = ",".join("?" for _ in paths)
            rows = conn.execute(
                f"""
                SELECT DISTINCT r.code AS code, p.point AS point,
                       r.name AS name, fs.path AS path
                FROM point_content pc
                JOIN point p  ON p.id = pc.point_id
                JOIN req r    ON r.id = p.req_id
                JOIN file_snapshot fs ON fs.id = pc.file_id
                JOIN git_project gp   ON gp.id = r.git_project_id
                WHERE gp.url_key = ?
                  AND r.status = 1 AND p.status = 1
                  AND fs.path IN ({placeholders})
                ORDER BY r.code, p.point, fs.path
                """,
                [project_key, *paths],
            ).fetchall()
        except sqlite3.Error as exc:
            logger.warning("Requirement query failed (scan.db schema?): {}", exc)
            return []
        finally:
            conn.close()

        return [
            RequirementRef(
                code=row["code"],
                point=row["point"],
                name=row["name"] or "",
                path=row["path"],
            )
            for row in rows
        ]

    def all_requirements(self, project_key: str) -> list[dict[str, str]]:
        """Return every active requirement of a project."""
        conn = self._connect()
        if conn is None:
            return []
        try:
            rows = conn.execute(
                """
                SELECT r.code AS code, p.point AS point, r.name AS name
                FROM req r
                JOIN point p ON p.req_id = r.id
                JOIN git_project gp ON gp.id = r.git_project_id
                WHERE gp.url_key = ? AND r.status = 1 AND p.status = 1
                ORDER BY r.code, p.point
                """,
                (project_key,),
            ).fetchall()
        except sqlite3.Error as exc:
            logger.warning("Requirement overview query failed: {}", exc)
            return []
        finally:
            conn.close()
        return [dict(row) for row in rows]

    def attach(self, project_key: str, analyses: Iterable) -> None:
        """Populate ``requirements`` on analysis results (in place)."""
        if not self.available:
            return
        for analysis in analyses:
            paths = [file.path for file in analysis.file_changes]
            analysis.requirements = self.requirements_for(project_key, paths)
