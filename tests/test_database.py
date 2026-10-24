"""Tests for the database manager module."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from scan_git_push.database import DatabaseManager
from scan_git_push.github_client import CommitData, FileChange


@pytest.fixture
def temp_db(tmp_path: Path) -> DatabaseManager:
    """Create a temporary database manager."""
    db_dir = tmp_path / "db"
    db = DatabaseManager(db_dir)
    db.initialize()
    return db


@pytest.fixture
def sample_commit() -> CommitData:
    """Create a sample CommitData."""
    return CommitData(
        sha="abc123def456",
        author="testuser",
        date=datetime(2026, 10, 20, 12, 0, tzinfo=timezone.utc),
        message="Test commit message",
        files=[
            FileChange(
                filename="test_file.py",
                status="modified",
                additions=10,
                deletions=5,
                patch="@@ -1,3 +1,5 @@\n+new line\n-old line",
            ),
        ],
        is_merge=False,
    )


class TestDatabaseManager:
    """Test DatabaseManager class."""

    def test_initialize_creates_tables(self, temp_db: DatabaseManager) -> None:
        """Test that tables are created on initialize."""
        # Tables exist — no exception raised
        assert temp_db.db_path.exists()

    def test_is_commit_analyzed_false_for_new(self, temp_db: DatabaseManager) -> None:
        """Test that a new commit is not analyzed."""
        assert temp_db.is_commit_analyzed("abc123", "https://github.com/o/r") is False

    def test_save_and_check_commit(self, temp_db: DatabaseManager, sample_commit: CommitData) -> None:
        """Test saving a commit and then checking it is analyzed."""
        temp_db.save_commit_analysis(
            commit_sha=sample_commit.sha,
            repo_url="https://github.com/o/r",
            analyzed_at=sample_commit.date,
            summary="Test summary",
            files=sample_commit.files,
            chunks_count=1,
        )

        assert temp_db.is_commit_analyzed(sample_commit.sha, "https://github.com/o/r") is True

    def test_filter_unanalyzed(self, temp_db: DatabaseManager, sample_commit: CommitData) -> None:
        """Test filtering out already analyzed commits."""
        repo_url = "https://github.com/o/r"

        # Save one commit as analyzed
        temp_db.save_commit_analysis(
            commit_sha=sample_commit.sha,
            repo_url=repo_url,
            analyzed_at=sample_commit.date,
            summary="Test summary",
            files=sample_commit.files,
            chunks_count=1,
        )

        # Create two commits: one analyzed, one new
        new_commit = CommitData(
            sha="new456",
            author="testuser",
            date=sample_commit.date,
            message="New commit",
            files=[],
            is_merge=False,
        )

        commits = [sample_commit, new_commit]
        unanalyzed = temp_db.filter_unanalyzed(commits, repo_url)

        assert len(unanalyzed) == 1
        assert unanalyzed[0].sha == "new456"

    def test_filter_unanalyzed_empty_list(self, temp_db: DatabaseManager) -> None:
        """Test filtering with empty commit list."""
        result = temp_db.filter_unanalyzed([], "https://github.com/o/r")
        assert result == []

    def test_save_and_get_files(self, temp_db: DatabaseManager, sample_commit: CommitData) -> None:
        """Test saving and retrieving commit files."""
        temp_db.save_commit_analysis(
            commit_sha=sample_commit.sha,
            repo_url="https://github.com/o/r",
            analyzed_at=sample_commit.date,
            summary="Test summary",
            files=sample_commit.files,
            chunks_count=1,
            author=sample_commit.author,
            message=sample_commit.message,
        )

        files = temp_db.get_commit_files(sample_commit.sha, "https://github.com/o/r")
        assert len(files) == 1
        assert files[0]["filename"] == "test_file.py"
        assert files[0]["file_status"] == "modified"
        assert files[0]["additions"] == 10
        assert files[0]["deletions"] == 5

    def test_save_with_author_and_message(self, temp_db: DatabaseManager, sample_commit: CommitData) -> None:
        """Test saving commit with author and message fields."""
        temp_db.save_commit_analysis(
            commit_sha=sample_commit.sha,
            repo_url="https://github.com/o/r",
            analyzed_at=sample_commit.date,
            summary="Test summary",
            files=sample_commit.files,
            chunks_count=1,
            author=sample_commit.author,
            message=sample_commit.message,
        )

        analyses = temp_db.get_recent_analyses("https://github.com/o/r", days=7)
        assert len(analyses) == 1
        assert analyses[0]["author"] == "testuser"
        assert analyses[0]["message"] == "Test commit message"

    def test_get_recent_analyses_includes_author_message(self, temp_db: DatabaseManager, sample_commit: CommitData) -> None:
        """Test that get_recent_analyses returns author and message."""
        temp_db.save_commit_analysis(
            commit_sha=sample_commit.sha,
            repo_url="https://github.com/o/r",
            analyzed_at=sample_commit.date,
            summary="Test summary",
            files=sample_commit.files,
            chunks_count=1,
            author=sample_commit.author,
            message=sample_commit.message,
        )

        analyses = temp_db.get_recent_analyses("https://github.com/o/r", days=7)
        assert analyses[0]["author"] == "testuser"
        assert analyses[0]["message"] == "Test commit message"

    def test_cleanup_old_records(self, temp_db: DatabaseManager, sample_commit: CommitData) -> None:
        """Test cleanup of old records."""
        # Save an old commit (61 days ago)
        old_date = datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc)
        temp_db.save_commit_analysis(
            commit_sha="old123",
            repo_url="https://github.com/o/r",
            analyzed_at=old_date,
            summary="Old commit",
            files=[],
            chunks_count=1,
        )

        # Save a recent commit
        temp_db.save_commit_analysis(
            commit_sha="new123",
            repo_url="https://github.com/o/r",
            analyzed_at=datetime.now(timezone.utc),
            summary="New commit",
            files=[],
            chunks_count=1,
        )

        deleted = temp_db.cleanup_old_records(retention_days=60)
        assert deleted == 1

        # Old commit should be gone
        assert temp_db.is_commit_analyzed("old123", "https://github.com/o/r") is False
        # Recent commit should remain
        assert temp_db.is_commit_analyzed("new123", "https://github.com/o/r") is True

    def test_cleanup_no_old_records(self, temp_db: DatabaseManager, sample_commit: CommitData) -> None:
        """Test cleanup when no records are old."""
        temp_db.save_commit_analysis(
            commit_sha="new123",
            repo_url="https://github.com/o/r",
            analyzed_at=datetime.now(timezone.utc),
            summary="New commit",
            files=[],
            chunks_count=1,
        )

        deleted = temp_db.cleanup_old_records(retention_days=60)
        assert deleted == 0

    def test_get_recent_analyses(self, temp_db: DatabaseManager, sample_commit: CommitData) -> None:
        """Test retrieving recent analyses."""
        temp_db.save_commit_analysis(
            commit_sha=sample_commit.sha,
            repo_url="https://github.com/o/r",
            analyzed_at=sample_commit.date,
            summary="Test summary",
            files=sample_commit.files,
            chunks_count=1,
        )

        analyses = temp_db.get_recent_analyses("https://github.com/o/r", days=7)
        assert len(analyses) == 1
        assert analyses[0]["sha"] == sample_commit.sha
        assert analyses[0]["summary"] == "Test summary"
