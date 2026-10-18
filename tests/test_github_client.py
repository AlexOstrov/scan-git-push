"""Tests for GitHub client module."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from scan_git_push.github_client import GitHubClient, CommitData, FileChange


class TestGitHubClient:
    """Test GitHubClient class."""

    def test_extract_repo_name_https(self) -> None:
        """Test extracting repo name from HTTPS URL."""
        client = GitHubClient(token="test", repo_url="https://github.com/owner/repo")
        assert client.repo  # Should not raise

    def test_extract_repo_name_git_suffix(self) -> None:
        """Test extracting repo name from URL with .git suffix."""
        client = GitHubClient(token="test", repo_url="https://github.com/owner/repo.git")
        assert client.repo  # Should not raise

    def test_extract_repo_name_ssh(self) -> None:
        """Test extracting repo name from SSH URL."""
        client = GitHubClient(token="test", repo_url="git@github.com:owner/repo.git")
        assert client.repo  # Should not raise

    def test_invalid_url_format(self) -> None:
        """Test that invalid URL format raises ValueError."""
        client = GitHubClient(token="test", repo_url="https://gitlab.com/owner/repo")
        with pytest.raises(ValueError):
            _ = client.repo

    @patch("scan_git_push.github_client.Github")
    def test_get_commits(self, mock_github_cls) -> None:
        """Test fetching commits with merge commit exclusion."""
        # Setup mocks
        mock_repo = MagicMock()
        mock_github = MagicMock()
        mock_github.get_repo.return_value = mock_repo
        mock_github_cls.return_value = mock_github

        # Create mock commits
        since = datetime.now(timezone.utc) - timedelta(days=7)

        mock_commit1 = MagicMock()
        mock_commit1.sha = "abc123"
        mock_commit1.author.login = "user1"
        mock_commit1.commit.message = "Regular commit"
        mock_commit1.commit.author.date = datetime.now(timezone.utc)
        mock_commit1.parents = [MagicMock()]  # Single parent = not merge
        mock_commit1.files = [
            MagicMock(filename="file1.py", status="modified", additions=5, deletions=2, patch="@@ -1 +1 @@")
        ]

        mock_commit2 = MagicMock()
        mock_commit2.sha = "def456"
        mock_commit2.author.login = "user2"
        mock_commit2.commit.message = "Merge commit"
        mock_commit2.commit.author.date = datetime.now(timezone.utc)
        mock_commit2.parents = [MagicMock(), MagicMock()]  # Two parents = merge
        mock_commit2.files = []

        mock_repo.get_commits.return_value = [mock_commit1, mock_commit2]

        client = GitHubClient(token="test", repo_url="https://github.com/owner/repo")
        commits = client.get_commits(days=7)

        # Should only return the non-merge commit
        assert len(commits) == 1
        assert commits[0].sha == "abc123"
        assert commits[0].author == "user1"
        assert commits[0].is_merge is False
        assert len(commits[0].files) == 1
        assert commits[0].files[0].filename == "file1.py"

    @patch("scan_git_push.github_client.Github")
    def test_retry_on_failure(self, mock_github_cls) -> None:
        """Test that retry logic works on transient failures."""
        mock_repo = MagicMock()
        mock_github = MagicMock()
        mock_github.get_repo.return_value = mock_repo
        mock_github_cls.return_value = mock_github

        # First two calls fail, third succeeds
        mock_repo.get_commits.side_effect = [
            Exception("Network error"),
            Exception("Timeout"),
            [],  # Empty list on success
        ]

        client = GitHubClient(
            token="test",
            repo_url="https://github.com/owner/repo",
            max_retries=3,
            backoff_factor=0.01,  # Fast retry for tests
        )

        commits = client.get_commits(days=7)
        assert commits == []
        assert mock_repo.get_commits.call_count == 3

    @patch("scan_git_push.github_client.Github")
    def test_retry_exhausted(self, mock_github_cls) -> None:
        """Test that exception is raised after all retries exhausted."""
        mock_repo = MagicMock()
        mock_github = MagicMock()
        mock_github.get_repo.return_value = mock_repo
        mock_github_cls.return_value = mock_github

        # All calls fail
        mock_repo.get_commits.side_effect = Exception("Persistent error")

        client = GitHubClient(
            token="test",
            repo_url="https://github.com/owner/repo",
            max_retries=2,
            backoff_factor=0.01,
        )

        with pytest.raises(Exception, match="Persistent error"):
            client.get_commits(days=7)
