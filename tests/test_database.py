"""Tests for ``analysis.db`` (schema version 1)."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from scan_git_push.database import DatabaseManager, SchemaVersionError
from scan_git_push.git.models import CommitData, FileChange


@pytest.fixture
def temp_db(tmp_path: Path) -> DatabaseManager:
    db = DatabaseManager(tmp_path / "db")
    db.initialize()
    return db


@pytest.fixture
def sample_commit() -> CommitData:
    return CommitData(
        sha="abc123def456",
        author="testuser",
        date=datetime(2026, 10, 20, 12, 0, tzinfo=timezone.utc),
        message="Test commit message",
        committer="testuser",
        email="test@example.com",
        files=[
            FileChange(path="test_file.py", status="modified", additions=10, deletions=5),
            FileChange(path="logo.png", status="added", is_binary=True),
        ],
    )


def _save(db: DatabaseManager, project_id: int, commit: CommitData, summary: str = "sum") -> int:
    return db.save_commit_analysis(
        project_id=project_id,
        commit=commit,
        summary=summary,
        chunks_count=1,
        provider="fake",
        model="fake-model",
    )


class TestSchema:
    def test_creates_version_1(self, temp_db: DatabaseManager) -> None:
        assert temp_db.schema_version() == 1
        with temp_db.connect() as conn:
            names = {
                row[0]
                for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
        assert {"schema_version", "git_project", "analyzed_commits", "commit_files"} <= names

    def test_pragmas_are_applied(self, temp_db: DatabaseManager) -> None:
        with temp_db.connect() as conn:
            assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
            assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
            assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 5000

    def test_incompatible_version_raises(self, temp_db: DatabaseManager) -> None:
        with temp_db.connect() as conn:
            conn.execute("UPDATE schema_version SET version = 2")
        with pytest.raises(SchemaVersionError):
            temp_db.initialize()

    def test_unrecognizable_database_raises(self, tmp_path: Path) -> None:
        path = tmp_path / "db" / "analysis.db"
        path.parent.mkdir(parents=True)
        conn = sqlite3.connect(str(path))
        conn.execute("CREATE TABLE something_else (id INTEGER)")
        conn.commit()
        conn.close()

        db = DatabaseManager(path)
        with pytest.raises(SchemaVersionError):
            db.initialize()


class TestProjects:
    def test_ensure_project_is_idempotent(self, temp_db: DatabaseManager) -> None:
        first = temp_db.ensure_project("https://github.com/Owner/Repo.git")
        second = temp_db.ensure_project("https://github.com/Owner/Repo")
        assert first == second
        projects = temp_db.list_projects()
        assert len(projects) == 1
        assert projects[0]["url_key"] == "github.com/Owner/Repo"
        assert projects[0]["url"].endswith("Owner/Repo")

    def test_get_project_id_missing(self, temp_db: DatabaseManager) -> None:
        assert temp_db.get_project_id("https://github.com/o/missing") is None


class TestCommitStorage:
    def test_save_and_read(self, temp_db: DatabaseManager, sample_commit: CommitData) -> None:
        project_id = temp_db.ensure_project("https://github.com/o/r")
        _save(temp_db, project_id, sample_commit)

        rows = temp_db.get_analyses([project_id], days=3650)
        assert len(rows) == 1
        row = rows[0]
        assert row["sha"] == sample_commit.sha
        assert row["summary"] == "sum"
        assert row["author"] == "testuser"
        assert row["message"] == "Test commit message"
        assert row["additions"] == 10
        assert row["deletions"] == 5
        assert row["provider"] == "fake"
        files = {file["path"]: file for file in row["files"]}
        assert files["test_file.py"]["file_status"] == "modified"
        assert files["logo.png"]["is_binary"] == 1

    def test_save_replaces_existing(self, temp_db: DatabaseManager, sample_commit: CommitData) -> None:
        project_id = temp_db.ensure_project("https://github.com/o/r")
        _save(temp_db, project_id, sample_commit, summary="first")
        _save(temp_db, project_id, sample_commit, summary="second")

        rows = temp_db.get_analyses([project_id])
        assert len(rows) == 1
        assert rows[0]["summary"] == "second"

    def test_filter_unanalyzed(self, temp_db: DatabaseManager, sample_commit: CommitData) -> None:
        project_id = temp_db.ensure_project("https://github.com/o/r")
        other = CommitData(
            sha="fff000",
            author="a",
            date=sample_commit.date,
            message="other",
        )
        assert len(temp_db.filter_unanalyzed([sample_commit, other], project_id)) == 2

        _save(temp_db, project_id, sample_commit)
        pending = temp_db.filter_unanalyzed([sample_commit, other], project_id)
        assert [commit.sha for commit in pending] == [other.sha]

    def test_same_sha_in_different_projects(
        self, temp_db: DatabaseManager, sample_commit: CommitData
    ) -> None:
        first = temp_db.ensure_project("https://github.com/o/one")
        second = temp_db.ensure_project("https://github.com/o/two")
        _save(temp_db, first, sample_commit)
        _save(temp_db, second, sample_commit)
        assert temp_db.count_commits(first) == 1
        assert temp_db.count_commits(second) == 1
        assert temp_db.count_commits() == 2

    def test_period_filter(self, temp_db: DatabaseManager) -> None:
        project_id = temp_db.ensure_project("https://github.com/o/r")
        now = datetime.now(timezone.utc)
        recent = CommitData(sha="recent", author="a", date=now, message="recent")
        old = CommitData(
            sha="old", author="a", date=now - timedelta(days=100), message="old"
        )
        _save(temp_db, project_id, recent)
        _save(temp_db, project_id, old)

        rows = temp_db.get_analyses([project_id], days=7)
        assert [row["sha"] for row in rows] == ["recent"]

    def test_cleanup_removes_old_records(self, temp_db: DatabaseManager) -> None:
        project_id = temp_db.ensure_project("https://github.com/o/r")
        now = datetime.now(timezone.utc)
        _save(temp_db, project_id, CommitData(sha="new", author="a", date=now, message="n"))
        _save(
            temp_db,
            project_id,
            CommitData(sha="old", author="a", date=now - timedelta(days=90), message="o"),
        )
        assert temp_db.cleanup_old_records(60) == 1
        assert temp_db.count_commits(project_id) == 1

    def test_delete_project_cascades(self, temp_db: DatabaseManager, sample_commit: CommitData) -> None:
        project_id = temp_db.ensure_project("https://github.com/o/r")
        _save(temp_db, project_id, sample_commit)
        with temp_db.connect() as conn:
            conn.execute("DELETE FROM git_project WHERE id = ?", (project_id,))
            assert conn.execute("SELECT COUNT(*) FROM analyzed_commits").fetchone()[0] == 0
            assert conn.execute("SELECT COUNT(*) FROM commit_files").fetchone()[0] == 0


class TestDateNormalization:
    """Commit dates are stored in UTC so string comparison and ORDER BY work."""

    def test_save_stores_utc(self, temp_db: DatabaseManager) -> None:
        project_id = temp_db.ensure_project("https://github.com/o/r")
        commit = CommitData(
            sha="offset",
            author="a",
            date=datetime(
                2026, 11, 1, 14, 0, tzinfo=timezone(timedelta(hours=3))
            ),
            message="m",
        )
        _save(temp_db, project_id, commit)
        with temp_db.connect() as conn:
            stored = conn.execute(
                "SELECT commit_date FROM analyzed_commits WHERE sha = 'offset'"
            ).fetchone()[0]
        assert stored == "2026-11-01T11:00:00+00:00"

    def test_naive_date_is_treated_as_utc(self, temp_db: DatabaseManager) -> None:
        project_id = temp_db.ensure_project("https://github.com/o/r")
        _save(
            temp_db,
            project_id,
            CommitData(
                sha="naive",
                author="a",
                date=datetime(2026, 11, 1, 11, 0),
                message="m",
            ),
        )
        with temp_db.connect() as conn:
            stored = conn.execute(
                "SELECT commit_date FROM analyzed_commits WHERE sha = 'naive'"
            ).fetchone()[0]
        assert stored == "2026-11-01T11:00:00+00:00"

    def test_order_across_offsets(self, temp_db: DatabaseManager) -> None:
        project_id = temp_db.ensure_project("https://github.com/o/r")
        # 18:00-05:00 == 23:00 UTC (newer); 23:00+03:00 == 20:00 UTC (older).
        newer = CommitData(
            sha="newer",
            author="a",
            date=datetime(2026, 11, 1, 18, 0, tzinfo=timezone(timedelta(hours=-5))),
            message="n",
        )
        older = CommitData(
            sha="older",
            author="a",
            date=datetime(2026, 11, 1, 23, 0, tzinfo=timezone(timedelta(hours=3))),
            message="o",
        )
        _save(temp_db, project_id, older)
        _save(temp_db, project_id, newer)
        rows = temp_db.get_analyses([project_id], days=3650)
        assert [row["sha"] for row in rows] == ["newer", "older"]

    def test_initialize_normalizes_existing_offsets(
        self, temp_db: DatabaseManager
    ) -> None:
        project_id = temp_db.ensure_project("https://github.com/o/r")
        _save(
            temp_db,
            project_id,
            CommitData(
                sha="legacy",
                author="a",
                date=datetime(2026, 11, 1, 12, 0, tzinfo=timezone.utc),
                message="m",
            ),
        )
        with temp_db.connect() as conn:
            conn.execute(
                "UPDATE analyzed_commits SET commit_date = ?, author_date = ? "
                "WHERE sha = 'legacy'",
                ("2026-11-01T15:00:00+03:00", "2026-11-01T15:00:00+03:00"),
            )

        DatabaseManager(temp_db.db_path).initialize()

        with temp_db.connect() as conn:
            row = conn.execute(
                "SELECT commit_date, author_date FROM analyzed_commits "
                "WHERE sha = 'legacy'"
            ).fetchone()
        assert row["commit_date"] == "2026-11-01T12:00:00+00:00"
        assert row["author_date"] == "2026-11-01T12:00:00+00:00"


class TestLegacyMigration:
    def _create_legacy(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(path))
        conn.executescript(
            """
            CREATE TABLE analyzed_commits (
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
            CREATE TABLE commit_files (
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
            """
        )
        conn.execute(
            "INSERT INTO analyzed_commits VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "abc123",
                "https://github.com/o/r",
                "2026-10-20T12:00:00+00:00",
                "success",
                "legacy summary",
                "legacy author",
                "legacy message",
                1,
                1,
            ),
        )
        conn.execute(
            "INSERT INTO commit_files VALUES (?, ?, ?, ?, ?, ?)",
            ("abc123", "https://github.com/o/r", "old.py", "modified", 3, 1),
        )
        conn.commit()
        conn.close()

    def test_migration_preserves_data_and_backs_up(self, tmp_path: Path) -> None:
        path = tmp_path / "db" / "analysis.db"
        self._create_legacy(path)

        db = DatabaseManager(path)
        db.initialize()

        assert db.schema_version() == 1
        assert (tmp_path / "db" / "analysis.db.bak").exists()

        project_id = db.get_project_id("https://github.com/o/r")
        assert project_id is not None
        rows = db.get_analyses([project_id], days=3650)
        assert len(rows) == 1
        assert rows[0]["summary"] == "legacy summary"
        assert rows[0]["author"] == "legacy author"
        assert rows[0]["files"][0]["path"] == "old.py"

    def test_migration_is_one_time(self, tmp_path: Path) -> None:
        path = tmp_path / "db" / "analysis.db"
        self._create_legacy(path)

        DatabaseManager(path).initialize()
        db = DatabaseManager(path)
        db.initialize()
        assert db.schema_version() == 1
        assert db.count_commits() == 1
