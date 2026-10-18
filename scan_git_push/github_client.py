"""GitHub API client with retry logic for fetching commit data."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from github import Github
from github.Commit import Commit
from loguru import logger


@dataclass
class FileChange:
    """Represents a single changed file in a commit."""

    filename: str
    status: str
    additions: int
    deletions: int
    patch: str = ""


@dataclass
class CommitData:
    """Full data for a single commit."""

    sha: str
    author: str
    date: datetime
    message: str
    files: list[FileChange] = field(default_factory=list)
    is_merge: bool = False


class GitHubClient:
    """Client for fetching commit history from GitHub with retry support."""

    def __init__(
        self,
        token: str,
        repo_url: str,
        max_retries: int = 3,
        backoff_factor: float = 2.0,
        timeout: float = 30.0,
    ) -> None:
        self.token = token
        self.repo_url = repo_url
        self.max_retries = max_retries
        self.backoff_factor = backoff_factor
        self.timeout = timeout
        self._repo: Any = None

    @property
    def repo(self):
        """Lazy-initialize the GitHub repo object."""
        if self._repo is None:
            # Extract owner/repo from GitHub URL
            # Supports: https://github.com/owner/repo or git@github.com:owner/repo
            url = self.repo_url.rstrip("/")
            if url.startswith("https://github.com/"):
                path = url[len("https://github.com/"):]
            elif url.startswith("git@github.com:"):
                path = url[len("git@github.com:"):]
            else:
                raise ValueError(f"Unsupported GitHub URL format: {self.repo_url}")

            path = path.removesuffix(".git")
            owner, repo_name = path.split("/", 1)

            gh = Github(self.token, timeout=self.timeout)
            self._repo = gh.get_repo(f"{owner}/{repo_name}")

        return self._repo

    def get_commits(self, days: int) -> list[CommitData]:
        """Fetch commits from the last N days, excluding merge commits."""
        since = datetime.now(timezone.utc) - timedelta(days=days)

        logger.info(f"Fetching commits from {self.repo_url} since {since.isoformat()}")

        commits: list[CommitData] = []
        raw_commits = self._retry_with_backoff(
            lambda: list(self.repo.get_commits(since=since))
        )

        for commit in raw_commits:
            # Skip merge commits (more than one parent)
            is_merge = len(commit.parents) > 1
            if is_merge:
                logger.debug(f"Skipping merge commit {commit.sha[:8]}")
                continue

            commit_data = self._parse_commit(commit)
            commits.append(commit_data)

        logger.info(f"Fetched {len(commits)} commits (excluding merges)")
        return commits

    def _parse_commit(self, commit: Commit) -> CommitData:
        """Parse a PyGithub Commit into a CommitData dataclass."""
        files: list[FileChange] = []

        for file in commit.files:
            files.append(FileChange(
                filename=file.filename,
                status=file.status,
                additions=file.additions,
                deletions=file.deletions,
                patch=file.patch or "",
            ))

        return CommitData(
            sha=commit.sha,
            author=commit.author.login if commit.author else "unknown",
            date=commit.commit.author.date,
            message=commit.commit.message.strip(),
            files=files,
            is_merge=len(commit.parents) > 1,
        )

    def _retry_with_backoff(self, func):
        """Execute a function with exponential backoff retry logic."""
        last_exception: Exception | None = None

        for attempt in range(1, self.max_retries + 1):
            try:
                return func()
            except Exception as exc:
                last_exception = exc
                if attempt < self.max_retries:
                    wait_time = self.backoff_factor ** (attempt - 1)
                    logger.warning(
                        f"GitHub API attempt {attempt}/{self.max_retries} failed: {exc}. "
                        f"Retrying in {wait_time:.1f}s"
                    )
                    time.sleep(wait_time)
                else:
                    logger.error(f"All {self.max_retries} GitHub API attempts failed: {exc}")

        raise last_exception  # type: ignore[misc]
