"""Local mirror management: one bare mirror per git project."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Literal

from loguru import logger

from scan_git_push.git.errors import GitError, GitNotFoundError
from scan_git_push.git.runner import run_git
from scan_git_push.git.url import is_local_path, normalize_url, repo_name, url_key

FilterMode = Literal["auto", "none", "blob:none"]

_SAFE_NAME = re.compile(r"[^A-Za-z0-9_.-]+")


class MirrorManager:
    """Creates and updates bare mirrors used by both ``scan`` and ``analyze``.

    Mirror directory layout::

        db/repos/<repo-name>-<sha256(url_key)[:10]>.git
    """

    def __init__(
        self,
        mirror_dir: Path,
        filter_mode: FilterMode = "auto",
        clone_timeout: float = 1800.0,
        fetch_timeout: float = 900.0,
    ) -> None:
        self.mirror_dir = Path(mirror_dir)
        self.filter_mode = filter_mode
        self.clone_timeout = clone_timeout
        self.fetch_timeout = fetch_timeout

    # ------------------------------------------------------------------ paths
    def path_for(self, url: str, base_dir: Path | None = None) -> Path:
        """Return the mirror path for a repository (without creating it)."""
        key = url_key(url, base_dir)
        digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:10]
        name = _SAFE_NAME.sub("-", repo_name(url, base_dir)).strip("-") or "repository"
        return self.mirror_dir / f"{name}-{digest}.git"

    def is_cloned(self, url: str, base_dir: Path | None = None) -> bool:
        path = self.path_for(url, base_dir)
        return (path / "HEAD").exists() and (path / "objects").exists()

    # ------------------------------------------------------------------- sync
    def ensure(
        self,
        url: str,
        base_dir: Path | None = None,
        update: bool = True,
    ) -> Path:
        """Clone the repository if needed and refresh it when *update* is set."""
        display_url = normalize_url(url, base_dir)
        path = self.path_for(url, base_dir)
        if self.is_cloned(url, base_dir):
            if update:
                self.fetch(path)
            return path
        return self.clone(display_url, path)

    def clone(self, url: str, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        args = ["clone", "--mirror"]
        if self._use_filter(url):
            args += ["--filter=blob:none"]
        args += [url, str(path)]
        logger.info("Cloning mirror {} -> {}", url, path)
        try:
            run_git(args, timeout=self.clone_timeout)
        except GitError:
            # Leave no half-initialized directory behind so the next run retries.
            if path.exists() and not (path / "HEAD").exists():
                _remove_tree(path)
            raise
        return path

    def fetch(self, path: Path) -> None:
        logger.debug("Fetching mirror {}", path)
        try:
            run_git(
                ["remote", "update", "--prune"],
                cwd=path,
                timeout=self.fetch_timeout,
            )
        except GitNotFoundError:
            raise
        except GitError as exc:
            # Rewritten history is handled by the mirror refspec; anything else
            # is reported to the caller.
            logger.warning("Mirror update failed for {}: {}", path, exc)
            raise

    def _use_filter(self, url: str) -> bool:
        if self.filter_mode == "blob:none":
            return True
        if self.filter_mode == "none":
            return False
        # auto: partial clone for remote repositories, full copy for local ones
        # (a local clone is cheap and must work without a reachable server).
        return not is_local_path(url)

    # -------------------------------------------------------------- inspection
    def default_branch(self, path: Path) -> str | None:
        try:
            ref = run_git(["symbolic-ref", "--short", "HEAD"], cwd=path).strip()
        except GitError:
            return None
        return ref or None

    def branches(self, path: Path, raise_on_error: bool = False) -> list[str]:
        try:
            output = run_git(
                ["for-each-ref", "--format=%(refname:short)", "refs/heads"],
                cwd=path,
            )
        except GitError:
            if raise_on_error:
                raise
            return []
        return sorted(line.strip() for line in output.splitlines() if line.strip())

    def head_sha(self, path: Path) -> str | None:
        try:
            return run_git(["rev-parse", "HEAD"], cwd=path).strip()
        except GitError:
            return None


def _remove_tree(path: Path) -> None:
    import shutil

    shutil.rmtree(path, ignore_errors=True)
