"""Tests for the git layer: URL normalization, mirrors and revision reading."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from scan_git_push.git.mirror import MirrorManager
from scan_git_push.git.rev import (
    list_commits,
    numstat,
    read_patches,
    resolve_refs,
)
from scan_git_push.git.url import is_local_path, normalize_url, repo_name, url_key
from tests.conftest import git


class TestUrlNormalization:
    def test_https_variants_share_key(self) -> None:
        a = url_key("https://github.com/Owner/Repo.git")
        b = url_key("https://github.com/Owner/Repo/")
        assert a == b == "github.com/Owner/Repo"

    def test_ssh_and_scp_share_key(self) -> None:
        https = url_key("https://github.com/owner/repo")
        ssh = url_key("git@github.com:owner/repo.git")
        assert https == ssh == "github.com/owner/repo"

    def test_credentials_are_removed(self) -> None:
        normalized = normalize_url("https://user:secret@github.com/owner/repo.git")
        assert "secret" not in normalized
        assert "user" not in normalized
        assert normalized == "https://github.com/owner/repo"

    def test_ssh_user_is_kept(self) -> None:
        # ``git@`` is the SSH login, not a credential; it is required for cloning.
        assert (
            normalize_url("git@github.com:owner/repo.git")
            == "ssh://git@github.com/owner/repo"
        )
        assert url_key("git@github.com:owner/repo.git") == "github.com/owner/repo"

    def test_local_path_is_absolute(self, tmp_path: Path) -> None:
        key = url_key(str(tmp_path))
        assert Path(key).is_absolute()
        assert "\\" not in key

    def test_relative_local_path_uses_base_dir(self, tmp_path: Path) -> None:
        key = url_key("some/repo", base_dir=tmp_path)
        assert key == (tmp_path / "some" / "repo").resolve().as_posix()

    def test_repo_name(self) -> None:
        assert repo_name("https://github.com/owner/my-repo.git") == "my-repo"
        assert repo_name("git@github.com:owner/repo.git") == "repo"

    def test_is_local_path(self) -> None:
        assert is_local_path("d:/projects/repo")
        assert is_local_path("./repo")
        assert not is_local_path("https://github.com/o/r")
        assert not is_local_path("git@github.com:o/r.git")


class TestMirrorManager:
    def test_clone_and_reuse(self, tmp_path: Path, git_repo: Path) -> None:
        manager = MirrorManager(tmp_path / "mirrors", filter_mode="none")
        path = manager.ensure(str(git_repo), update=False)
        assert (path / "HEAD").exists()
        assert manager.is_cloned(str(git_repo))

        # A second ensure() must update the same mirror instead of cloning again.
        again = manager.ensure(str(git_repo), update=True)
        assert again == path

    def test_branches_and_default(self, tmp_path: Path, git_repo: Path) -> None:
        manager = MirrorManager(tmp_path / "mirrors", filter_mode="none")
        path = manager.ensure(str(git_repo), update=False)
        assert manager.branches(path) == ["feature", "main"]
        assert manager.default_branch(path) == "main"

    def test_resolve_refs(self, tmp_path: Path, git_repo: Path) -> None:
        manager = MirrorManager(tmp_path / "mirrors", filter_mode="none")
        path = manager.ensure(str(git_repo), update=False)
        assert resolve_refs(path, "all") == ["--branches"]
        assert resolve_refs(path, "default", "main") == ["refs/heads/main"]
        assert resolve_refs(path, ["main", "feature"]) == [
            "refs/heads/main",
            "refs/heads/feature",
        ]

    def test_missing_repository_raises(self, tmp_path: Path) -> None:
        manager = MirrorManager(tmp_path / "mirrors", filter_mode="none")
        with pytest.raises(Exception):
            manager.ensure(str(tmp_path / "missing"), update=False)


class TestRevisionReading:
    def test_list_commits_all_branches_dedupes(self, tmp_path: Path, git_repo: Path) -> None:
        manager = MirrorManager(tmp_path / "mirrors", filter_mode="none")
        mirror = manager.ensure(str(git_repo), update=False)

        commits = list_commits(mirror, days=30, refs=resolve_refs(mirror, "all"))
        messages = [commit.message.splitlines()[0] for commit in commits]
        assert len(commits) == len({commit.sha for commit in commits})
        assert "feature commit" in messages
        assert "initial" in messages

        default_only = list_commits(
            mirror, days=30, refs=resolve_refs(mirror, "default", "main")
        )
        assert "feature commit" not in [
            commit.message.splitlines()[0] for commit in default_only
        ]

    def test_since_uses_committer_date(self, tmp_path: Path, git_repo: Path) -> None:
        old_env = os.environ.copy()
        old_env["GIT_AUTHOR_DATE"] = "2020-01-01T00:00:00+0000"
        old_env["GIT_COMMITTER_DATE"] = "2020-01-01T00:00:00+0000"
        (git_repo / "old.txt").write_text("old\n", encoding="utf-8")
        git(["add", "old.txt"], git_repo)
        git(["commit", "-m", "old commit"], git_repo, env=old_env)

        manager = MirrorManager(tmp_path / "mirrors", filter_mode="none")
        mirror = manager.ensure(str(git_repo), update=False)

        recent = list_commits(mirror, days=30, refs=["--branches"])
        assert "old commit" not in [c.message.splitlines()[0] for c in recent]

        everything = list_commits(mirror, days=3650, refs=["--branches"])
        assert "old commit" in [c.message.splitlines()[0] for c in everything]

    def test_numstat_status_and_binary(self, tmp_path: Path, git_repo: Path) -> None:
        manager = MirrorManager(tmp_path / "mirrors", filter_mode="none")
        mirror = manager.ensure(str(git_repo), update=False)
        commits = list_commits(mirror, days=30, refs=["--branches"])
        second = next(c for c in commits if c.message.startswith("second commit"))

        files = {file.path: file for file in numstat(mirror, second.sha)}
        assert files["src.py"].status == "modified"
        assert files["src.py"].additions == 1
        assert files["src.py"].deletions == 1
        assert files["bin.dat"].is_binary is True

    def test_read_patches_and_truncation(self, tmp_path: Path, git_repo: Path) -> None:
        (git_repo / "big.txt").write_text(
            "\n".join(f"line {index}" for index in range(2000)), encoding="utf-8"
        )
        git(["add", "big.txt"], git_repo)
        git(["commit", "-m", "big file"], git_repo)

        manager = MirrorManager(tmp_path / "mirrors", filter_mode="none")
        mirror = manager.ensure(str(git_repo), update=False)
        commit = next(
            c for c in list_commits(mirror, days=30, refs=["--branches"])
            if c.message.startswith("big file")
        )

        patches = read_patches(mirror, commit.sha, max_chars_per_file=800)
        big = patches["big.txt"]
        assert big.truncated is True
        assert len(big.patch) < 2000

    def test_negative_days_rejected(self, tmp_path: Path, git_repo: Path) -> None:
        manager = MirrorManager(tmp_path / "mirrors", filter_mode="none")
        mirror = manager.ensure(str(git_repo), update=False)
        with pytest.raises(ValueError):
            list_commits(mirror, days=0)
