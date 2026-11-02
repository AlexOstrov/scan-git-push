"""Commit collection from a local mirror."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from loguru import logger

from scan_git_push.analysis.filters import filter_files
from scan_git_push.config import AnalysisSettings
from scan_git_push.git.mirror import MirrorManager
from scan_git_push.git.models import CommitData
from scan_git_push.git.rev import list_commits, numstat, read_patches, resolve_refs
from scan_git_push.git.url import normalize_url, repo_name, url_key


@dataclass
class ProjectCommits:
    """Commits collected for a single project."""

    url: str
    display_url: str
    key: str
    name: str
    mirror: Path
    branches: list[str] = field(default_factory=list)
    commits: list[CommitData] = field(default_factory=list)


class CommitCollector:
    """Reads commits and statistics from mirrors without touching the network."""

    def __init__(self, mirror: MirrorManager, settings: AnalysisSettings) -> None:
        self.mirror = mirror
        self.settings = settings

    def collect(
        self,
        url: str,
        base_dir: Path | None = None,
        days: int | None = None,
    ) -> ProjectCommits:
        """Ensure the mirror exists, refresh it and read the commit window."""
        mirror_path = self.mirror.ensure(url, base_dir=base_dir, update=True)
        branches = self.mirror.branches(mirror_path)
        default_branch = self.mirror.default_branch(mirror_path)
        refs = resolve_refs(mirror_path, self.settings.branches, default_branch)

        commits = list_commits(
            mirror_path,
            days=days if days is not None else self.settings.git_day,
            refs=refs,
            first_parent=self.settings.first_parent,
        )

        for commit in commits:
            files = numstat(mirror_path, commit.sha)
            commit.files = filter_files(files, self.settings.exclude)

        logger.info(
            "Collected {} commit(s) from {} ({} branch(es))",
            len(commits),
            url,
            len(branches),
        )
        return ProjectCommits(
            url=url,
            display_url=normalize_url(url, base_dir),
            key=url_key(url, base_dir),
            name=repo_name(url, base_dir),
            mirror=mirror_path,
            branches=branches,
            commits=commits,
        )

    def attach_patches(self, project: ProjectCommits, commit: CommitData) -> None:
        """Load patches for the files that survived the exclude filter."""
        ordered = [file.path for file in commit.files]
        patches = read_patches(
            project.mirror,
            commit.sha,
            max_chars_per_file=self.settings.max_patch_chars_per_file,
            ordered_paths=ordered,
        )
        for file in commit.files:
            patch = patches.get(file.path)
            if patch is not None:
                file.patch = patch.patch
                file.truncated = patch.truncated
                file.is_binary = file.is_binary or patch.is_binary
