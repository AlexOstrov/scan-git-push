"""SQLite storage for analyzed commits (``analysis.db``, schema version 1).

The database is separate from ``scan.db`` (see ``dop_tz01.md``). Both share the
``git_project.url_key`` value, which allows ``ATTACH``-based queries that link
commits to requirements.
"""

from __future__ import annotations

import shutil
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator, Sequence

from loguru import logger

from scan_git_push.git.models import CommitData
from scan_git_push.git.url import normalize_url, url_key

SCHEMA_VERSION = 1

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS schema_version (
    version     INTEGER NOT NULL,
    applied_at  TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE TABLE IF NOT EXISTS git_project (
    id          INTEGER PRIMARY KEY,
    url         TEXT    NOT NULL,
    url_key     TEXT    NOT NULL UNIQUE,
    author      TEXT,
    status      INTEGER NOT NULL DEFAULT 1 CHECK (status IN (0, 1)),
    created_at  TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at  TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE TABLE IF NOT EXISTS analyzed_commits (
    id              INTEGER PRIMARY KEY,
    git_project_id  INTEGER NOT NULL REFERENCES git_project(id) ON DELETE CASCADE,
    sha             TEXT    NOT NULL,
    commit_date     TEXT    NOT NULL,
    author_date     TEXT,
    analyzed_at     TEXT    NOT NULL,
    status          TEXT    NOT NULL DEFAULT 'success'
                            CHECK (status IN ('success', 'failed')),
    summary         TEXT,
    author          TEXT,
    committer       TEXT,
    email           TEXT,
    message         TEXT,
    files_count     INTEGER NOT NULL DEFAULT 0,
    additions       INTEGER NOT NULL DEFAULT 0,
    deletions       INTEGER NOT NULL DEFAULT 0,
    chunks_count    INTEGER NOT NULL DEFAULT 0,
    provider        TEXT,
    model           TEXT,
    prompt_version  INTEGER NOT NULL DEFAULT 1,
    UNIQUE (git_project_id, sha)
);

CREATE TABLE IF NOT EXISTS commit_files (
    commit_id   INTEGER NOT NULL REFERENCES analyzed_commits(id) ON DELETE CASCADE,
    path        TEXT    NOT NULL,
    file_status TEXT    NOT NULL DEFAULT 'modified',
    additions   INTEGER NOT NULL DEFAULT 0,
    deletions   INTEGER NOT NULL DEFAULT 0,
    is_binary   INTEGER NOT NULL DEFAULT 0 CHECK (is_binary IN (0, 1)),
    truncated   INTEGER NOT NULL DEFAULT 0 CHECK (truncated IN (0, 1)),
    PRIMARY KEY (commit_id, path)
);

CREATE INDEX IF NOT EXISTS idx_commits_project_date
    ON analyzed_commits(git_project_id, commit_date);

CREATE INDEX IF NOT EXISTS idx_commits_analyzed_at
    ON analyzed_commits(analyzed_at);
"""


class DatabaseError(RuntimeError):
    """Base class for database failures."""


class SchemaVersionError(DatabaseError):
    """The database schema is incompatible with this release."""


class DatabaseManager:
    """Manages ``analysis.db`` (schema version 1)."""

    def __init__(self, db_path: Path | str, db_name: str = "analysis.db") -> None:
        path = Path(db_path)
        if path.suffix == ".db":
            self.db_path = path
        else:
            self.db_path = path / db_name
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------- connection
    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(str(self.db_path), timeout=5.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA busy_timeout = 5000")
        # Must be set before a transaction starts.
        conn.execute("PRAGMA synchronous = NORMAL")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    # ------------------------------------------------------------ schema init
    def initialize(self) -> None:
        """Create or validate the schema, migrating a legacy database once."""
        exists = self.db_path.exists() and self.db_path.stat().st_size > 0
        if not exists:
            with self.connect() as conn:
                conn.executescript(SCHEMA_SQL)
                conn.execute("INSERT INTO schema_version(version) VALUES (?)", (SCHEMA_VERSION,))
            logger.info("Database created at {}", self.db_path)
            return

        with self.connect() as conn:
            tables = _table_names(conn)
            if "schema_version" in tables:
                version = _read_version(conn)
                if version != SCHEMA_VERSION:
                    raise SchemaVersionError(
                        f"{self.db_path}: schema version {version} is not supported "
                        f"(expected {SCHEMA_VERSION})"
                    )
                conn.executescript(SCHEMA_SQL)
                return

            if "analyzed_commits" not in tables:
                raise SchemaVersionError(
                    f"{self.db_path}: no recognizable schema and no schema_version table"
                )

        self._migrate_legacy()

    def _migrate_legacy(self) -> None:
        """One-time migration from the 0.1 schema (``repo_url`` key)."""
        backup = self.db_path.with_suffix(self.db_path.suffix + ".bak")
        logger.warning("Legacy analysis database detected; migrating {}", self.db_path)

        with self.connect() as conn:
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        shutil.copy2(self.db_path, backup)
        logger.info("Backup written to {}", backup)

        with self.connect() as conn:
            tables = _table_names(conn)
            if "analyzed_commits_legacy" not in tables:
                conn.execute("ALTER TABLE analyzed_commits RENAME TO analyzed_commits_legacy")
            if "commit_files" in tables and "commit_files_legacy" not in tables:
                conn.execute("ALTER TABLE commit_files RENAME TO commit_files_legacy")
            conn.executescript(SCHEMA_SQL)
            tables = _table_names(conn)

            project_cache: dict[str, int] = {}

            rows = conn.execute(
                "SELECT sha, repo_url, analyzed_at, status, summary, author, message, "
                "files_count, chunks_count FROM analyzed_commits_legacy"
            ).fetchall()

            for row in rows:
                repo_url = row["repo_url"] or "unknown"
                project_id = project_cache.get(repo_url)
                if project_id is None:
                    project_id = self._ensure_project_conn(conn, repo_url)
                    project_cache[repo_url] = project_id

                cursor = conn.execute(
                    """
                    INSERT INTO analyzed_commits (
                        git_project_id, sha, commit_date, analyzed_at, status, summary,
                        author, message, files_count, chunks_count, prompt_version
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
                    """,
                    (
                        project_id,
                        row["sha"],
                        row["analyzed_at"],
                        row["analyzed_at"],
                        row["status"] if row["status"] in {"success", "failed"} else "success",
                        row["summary"],
                        row["author"],
                        row["message"],
                        row["files_count"] or 0,
                        row["chunks_count"] or 0,
                    ),
                )
                commit_id = int(cursor.lastrowid)

                if "commit_files_legacy" in tables:
                    file_rows = conn.execute(
                        "SELECT filename, file_status, additions, deletions "
                        "FROM commit_files_legacy WHERE commit_sha = ? AND repo_url = ?",
                        (row["sha"], row["repo_url"]),
                    ).fetchall()
                    for file_row in file_rows:
                        conn.execute(
                            "INSERT OR IGNORE INTO commit_files "
                            "(commit_id, path, file_status, additions, deletions) "
                            "VALUES (?, ?, ?, ?, ?)",
                            (
                                commit_id,
                                file_row["filename"],
                                file_row["file_status"] or "modified",
                                file_row["additions"] or 0,
                                file_row["deletions"] or 0,
                            ),
                        )

            conn.execute("DELETE FROM schema_version")
            conn.execute("INSERT INTO schema_version(version) VALUES (?)", (SCHEMA_VERSION,))
            logger.info("Migrated {} commit(s) to schema version {}", len(rows), SCHEMA_VERSION)

    # ---------------------------------------------------------------- projects
    def ensure_project(self, url: str, base_dir: Path | None = None) -> int:
        with self.connect() as conn:
            return self._ensure_project_conn(conn, url, base_dir)

    def _ensure_project_conn(
        self, conn: sqlite3.Connection, url: str, base_dir: Path | None = None
    ) -> int:
        display = normalize_url(url, base_dir)
        key = url_key(url, base_dir)
        conn.execute(
            """
            INSERT INTO git_project (url, url_key, updated_at)
            VALUES (?, ?, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
            ON CONFLICT(url_key) DO UPDATE SET
                url = excluded.url,
                updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
            """,
            (display, key),
        )
        row = conn.execute("SELECT id FROM git_project WHERE url_key = ?", (key,)).fetchone()
        if row is None:  # pragma: no cover - defensive
            raise DatabaseError(f"failed to register project {display}")
        return int(row["id"])

    def get_project_id(self, url: str, base_dir: Path | None = None) -> int | None:
        key = url_key(url, base_dir)
        with self.connect() as conn:
            row = conn.execute("SELECT id FROM git_project WHERE url_key = ?", (key,)).fetchone()
        return int(row["id"]) if row else None

    def list_projects(self) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT id, url, url_key, status, created_at, updated_at "
                "FROM git_project WHERE status = 1 ORDER BY url"
            ).fetchall()
        return [dict(row) for row in rows]

    # ---------------------------------------------------------------- commits
    def filter_unanalyzed(
        self, commits: Sequence[CommitData], project_id: int
    ) -> list[CommitData]:
        if not commits:
            return list(commits)

        shas = [commit.sha for commit in commits]
        placeholders = ",".join("?" for _ in shas)
        with self.connect() as conn:
            rows = conn.execute(
                f"SELECT sha FROM analyzed_commits WHERE git_project_id = ? "
                f"AND status = 'success' AND sha IN ({placeholders})",
                [project_id, *shas],
            ).fetchall()
        analyzed = {row["sha"] for row in rows}
        return [commit for commit in commits if commit.sha not in analyzed]

    def is_commit_analyzed(self, sha: str, repo_url: str) -> bool:
        """Compatibility helper: check by project URL instead of project id."""
        key = url_key(repo_url)
        with self.connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM analyzed_commits c "
                "JOIN git_project p ON p.id = c.git_project_id "
                "WHERE c.sha = ? AND p.url_key = ?",
                (sha, key),
            ).fetchone()
        return row is not None

    def save_commit_analysis(
        self,
        *,
        project_id: int,
        commit: CommitData,
        summary: str,
        chunks_count: int,
        provider: str | None = None,
        model: str | None = None,
        prompt_version: int = 1,
        status: str = "success",
        analyzed_at: datetime | None = None,
    ) -> int:
        """Insert or replace a commit analysis together with its file list."""
        analyzed_at = analyzed_at or datetime.now(timezone.utc)
        with self.connect() as conn:
            conn.execute(
                "DELETE FROM analyzed_commits WHERE git_project_id = ? AND sha = ?",
                (project_id, commit.sha),
            )
            cursor = conn.execute(
                """
                INSERT INTO analyzed_commits (
                    git_project_id, sha, commit_date, author_date, analyzed_at,
                    status, summary, author, committer, email, message,
                    files_count, additions, deletions, chunks_count,
                    provider, model, prompt_version
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    project_id,
                    commit.sha,
                    commit.date.isoformat(),
                    commit.author_date.isoformat() if commit.author_date else None,
                    analyzed_at.isoformat(),
                    status,
                    summary,
                    commit.author,
                    commit.committer,
                    commit.email,
                    commit.message,
                    commit.files_count,
                    commit.additions,
                    commit.deletions,
                    chunks_count,
                    provider,
                    model,
                    prompt_version,
                ),
            )
            commit_id = int(cursor.lastrowid)

            for file in commit.files:
                conn.execute(
                    "INSERT OR REPLACE INTO commit_files "
                    "(commit_id, path, file_status, additions, deletions, is_binary, truncated) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        commit_id,
                        file.path,
                        file.status,
                        file.additions,
                        file.deletions,
                        int(file.is_binary),
                        int(file.truncated),
                    ),
                )
        logger.debug("Saved commit {} for project {}", commit.sha[:8], project_id)
        return commit_id

    def get_analyses(
        self,
        project_ids: Sequence[int] | None = None,
        days: int | None = None,
    ) -> list[dict[str, Any]]:
        """Return analyses ordered newest-first, optionally limited by period."""
        clauses: list[str] = ["c.status = 'success'"]
        params: list[Any] = []

        if project_ids:
            placeholders = ",".join("?" for _ in project_ids)
            clauses.append(f"c.git_project_id IN ({placeholders})")
            params.extend(project_ids)
        if days is not None:
            since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
            clauses.append("c.commit_date >= ?")
            params.append(since)

        query = f"""
            SELECT c.id, c.git_project_id, c.sha, c.commit_date, c.author_date,
                   c.analyzed_at, c.summary, c.author, c.committer, c.email,
                   c.message, c.files_count, c.additions, c.deletions,
                   c.chunks_count, c.provider, c.model, c.prompt_version,
                   p.url AS project_url, p.url_key AS project_key
            FROM analyzed_commits c
            JOIN git_project p ON p.id = c.git_project_id
            WHERE {' AND '.join(clauses)}
            ORDER BY c.commit_date DESC
        """
        with self.connect() as conn:
            rows = conn.execute(query, params).fetchall()
            analyses = [dict(row) for row in rows]
            for analysis in analyses:
                files = conn.execute(
                    "SELECT path, file_status, additions, deletions, is_binary, truncated "
                    "FROM commit_files WHERE commit_id = ? ORDER BY path",
                    (analysis["id"],),
                ).fetchall()
                analysis["files"] = [dict(file) for file in files]

        return analyses

    def cleanup_old_records(self, retention_days: int = 60) -> int:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=retention_days)).isoformat()
        with self.connect() as conn:
            cursor = conn.execute(
                "DELETE FROM analyzed_commits WHERE commit_date < ?",
                (cutoff,),
            )
            deleted = cursor.rowcount
        if deleted:
            logger.info(
                "Cleaned up {} commit record(s) older than {} days", deleted, retention_days
            )
        return deleted

    def count_commits(self, project_id: int | None = None) -> int:
        with self.connect() as conn:
            if project_id is None:
                row = conn.execute("SELECT COUNT(*) FROM analyzed_commits").fetchone()
            else:
                row = conn.execute(
                    "SELECT COUNT(*) FROM analyzed_commits WHERE git_project_id = ?",
                    (project_id,),
                ).fetchone()
        return int(row[0]) if row else 0

    def schema_version(self) -> int:
        with self.connect() as conn:
            return _read_version(conn)


# --------------------------------------------------------------------------
def _table_names(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
    return {row[0] for row in rows}


def _read_version(conn: sqlite3.Connection) -> int:
    try:
        row = conn.execute("SELECT version FROM schema_version ORDER BY rowid DESC LIMIT 1").fetchone()
    except sqlite3.Error as exc:
        raise SchemaVersionError(f"cannot read schema_version: {exc}") from exc
    if row is None:
        raise SchemaVersionError("schema_version is empty")
    return int(row[0])
