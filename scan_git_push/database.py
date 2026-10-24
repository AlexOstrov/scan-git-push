"""SQLite database for storing analyzed commits.

Provides persistent storage of commit analysis results so that repeated
runs of the utility do not re-analyse commits that were already processed.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from loguru import logger


class DatabaseManager:
    """Manages the SQLite database for analyzed commits."""

    def __init__(self, db_dir: Path, db_name: str = "analysis.db") -> None:
        self.db_dir = db_dir
        self.db_path = db_dir / db_name
        self._ensure_dir()

    def _ensure_dir(self) -> None:
        """Create the database directory if it does not exist."""
        self.db_dir.mkdir(parents=True, exist_ok=True)

    def _get_connection(self) -> sqlite3.Connection:
        """Open a new database connection."""
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def initialize(self) -> None:
        """Create tables and indexes if they do not exist."""
        with self._get_connection() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS analyzed_commits (
                    sha TEXT NOT NULL,
                    repo_url TEXT NOT NULL,
                    analyzed_at TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'success',
                    summary TEXT,
                    author TEXT,
                    message TEXT,
                    files_count INTEGER DEFAULT 0,
                    chunks_count INTEGER DEFAULT 0,
                    PRIMARY KEY (sha, repo_url)
                );

                CREATE TABLE IF NOT EXISTS commit_files (
                    commit_sha TEXT NOT NULL,
                    repo_url TEXT NOT NULL,
                    filename TEXT NOT NULL,
                    file_status TEXT,
                    additions INTEGER DEFAULT 0,
                    deletions INTEGER DEFAULT 0,
                    PRIMARY KEY (commit_sha, repo_url, filename),
                    FOREIGN KEY (commit_sha, repo_url)
                        REFERENCES analyzed_commits(sha, repo_url)
                );

                CREATE INDEX IF NOT EXISTS idx_analyzed_commits_repo_date
                    ON analyzed_commits(repo_url, analyzed_at);

                CREATE INDEX IF NOT EXISTS idx_commit_files_repo
                    ON commit_files(repo_url);
                """
            )
            # Migration: add author/message columns to existing databases
            for column_sql in [
                "ALTER TABLE analyzed_commits ADD COLUMN author TEXT",
                "ALTER TABLE analyzed_commits ADD COLUMN message TEXT",
            ]:
                try:
                    conn.execute(column_sql)
                except sqlite3.OperationalError:
                    # Column already exists — ignore
                    pass
            conn.commit()
        logger.info("Database initialized at %s", self.db_path)

    def is_commit_analyzed(self, sha: str, repo_url: str) -> bool:
        """Return True if the commit was already analyzed."""
        with self._get_connection() as conn:
            cursor = conn.execute(
                "SELECT 1 FROM analyzed_commits WHERE sha = ? AND repo_url = ?",
                (sha, repo_url),
            )
            return cursor.fetchone() is not None

    def filter_unanalyzed(
        self, commits: list, repo_url: str
    ) -> list:
        """Return only commits that have not been analyzed yet."""
        if not commits:
            return commits

        shas = [c.sha for c in commits]
        placeholders = ",".join("?" for _ in shas)
        with self._get_connection() as conn:
            cursor = conn.execute(
                f"SELECT sha FROM analyzed_commits "
                f"WHERE repo_url = ? AND sha IN ({placeholders})",
                [repo_url, *shas],
            )
            analyzed_shas = {row[0] for row in cursor.fetchall()}

        return [c for c in commits if c.sha not in analyzed_shas]

    def save_commit_analysis(
        self,
        commit_sha: str,
        repo_url: str,
        analyzed_at: datetime,
        summary: str,
        files: list,
        chunks_count: int,
        status: str = "success",
        author: str = "",
        message: str = "",
    ) -> None:
        """Save a single commit analysis to the database."""
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO analyzed_commits
                (sha, repo_url, analyzed_at, status, summary, author, message, files_count, chunks_count)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    commit_sha,
                    repo_url,
                    analyzed_at.isoformat(),
                    status,
                    summary,
                    author,
                    message,
                    len(files),
                    chunks_count,
                ),
            )
            # Replace file list for this commit (idempotent re-save)
            conn.execute(
                "DELETE FROM commit_files WHERE commit_sha = ? AND repo_url = ?",
                (commit_sha, repo_url),
            )
            for file in files:
                conn.execute(
                    """
                    INSERT INTO commit_files
                    (commit_sha, repo_url, filename, file_status, additions, deletions)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        commit_sha,
                        repo_url,
                        file.filename,
                        file.status,
                        file.additions,
                        file.deletions,
                    ),
                )
            conn.commit()
        logger.debug("Saved analysis for commit %s (%s)", commit_sha[:8], repo_url)

    def get_recent_analyses(
        self, repo_url: str, days: int
    ) -> list[dict[str, Any]]:
        """Return analyses from the last N days (for the report-from-DB backlog)."""
        since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                SELECT sha, analyzed_at, status, summary, author, message, files_count, chunks_count
                FROM analyzed_commits
                WHERE repo_url = ? AND analyzed_at >= ?
                ORDER BY analyzed_at DESC
                """,
                (repo_url, since),
            )
            return [dict(row) for row in cursor.fetchall()]

    def get_commit_files(self, commit_sha: str, repo_url: str) -> list[dict[str, Any]]:
        """Return the list of changed files for a commit."""
        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                SELECT filename, file_status, additions, deletions
                FROM commit_files
                WHERE commit_sha = ? AND repo_url = ?
                ORDER BY filename
                """,
                (commit_sha, repo_url),
            )
            return [dict(row) for row in cursor.fetchall()]

    def cleanup_old_records(self, retention_days: int = 60) -> int:
        """Delete records older than N days. Returns number of deleted commits."""
        cutoff = (datetime.now(timezone.utc) - timedelta(days=retention_days)).isoformat()
        with self._get_connection() as conn:
            conn.execute(
                """
                DELETE FROM commit_files
                WHERE (commit_sha, repo_url) IN (
                    SELECT sha, repo_url FROM analyzed_commits WHERE analyzed_at < ?
                )
                """,
                (cutoff,),
            )
            cursor = conn.execute(
                "DELETE FROM analyzed_commits WHERE analyzed_at < ?",
                (cutoff,),
            )
            conn.commit()
            deleted = cursor.rowcount
        if deleted:
            logger.info("Cleaned up %d old commit records (older than %d days)", deleted, retention_days)
        return deleted
