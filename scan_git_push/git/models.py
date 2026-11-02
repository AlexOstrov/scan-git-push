"""Data structures shared by the git layer and the analysis pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

# Statuses are normalized to the vocabulary used by earlier releases so that
# existing reports and templates keep working.
STATUS_BY_GIT_CODE = {
    "A": "added",
    "M": "modified",
    "D": "deleted",
    "T": "typechange",
    "R": "renamed",
    "C": "copied",
}


@dataclass
class FileChange:
    """A single file touched by a commit."""

    path: str
    status: str = "modified"
    additions: int = 0
    deletions: int = 0
    patch: str = ""
    is_binary: bool = False
    truncated: bool = False

    # Kept as a property so older code using ``filename`` keeps working.
    @property
    def filename(self) -> str:
        return self.path

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "status": self.status,
            "additions": self.additions,
            "deletions": self.deletions,
            "is_binary": self.is_binary,
            "truncated": self.truncated,
        }


@dataclass
class CommitData:
    """A commit read from a local mirror."""

    sha: str
    author: str
    date: datetime
    message: str
    files: list[FileChange] = field(default_factory=list)
    author_date: datetime | None = None
    committer: str = ""
    email: str = ""
    is_merge: bool = False
    branches: list[str] = field(default_factory=list)

    @property
    def subject(self) -> str:
        return self.message.splitlines()[0] if self.message else ""

    @property
    def additions(self) -> int:
        return sum(f.additions for f in self.files)

    @property
    def deletions(self) -> int:
        return sum(f.deletions for f in self.files)

    @property
    def files_count(self) -> int:
        return len(self.files)
