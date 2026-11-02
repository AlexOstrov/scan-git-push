"""Errors raised by the git access layer."""

from __future__ import annotations


class GitError(RuntimeError):
    """Base class for git-related failures."""


class GitUnavailableError(GitError):
    """The git executable is missing or cannot be executed."""


class GitNotFoundError(GitError):
    """The repository does not exist or is not reachable."""


class GitAuthError(GitError):
    """Authentication or authorization failed for a remote repository."""
