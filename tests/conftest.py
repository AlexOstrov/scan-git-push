"""Shared fixtures for scan-git-push tests."""

from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest

from scan_git_push.config import Settings
from scan_git_push.git.models import CommitData, FileChange


def git(args: list[str], cwd: Path, env: dict[str, str] | None = None) -> str:
    """Run git and return stdout."""
    completed = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        check=True,
    )
    return completed.stdout


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    """Create a repository with three commits, two branches and a binary file."""
    repo = tmp_path / "repo"
    repo.mkdir()
    git(["init", "-b", "main"], repo)
    git(["config", "user.email", "test@example.com"], repo)
    git(["config", "user.name", "Tester"], repo)

    (repo / "docs").mkdir()
    (repo / "docs" / "req.md").write_text(
        '<!-- REQ-META id="REQ_1" point="1.1" name="First" -->\n', encoding="utf-8"
    )
    (repo / "src.py").write_text("print(1)\n", encoding="utf-8")
    git(["add", "."], repo)
    git(["commit", "-m", "initial"], repo)

    (repo / "src.py").write_text("print(2)\n", encoding="utf-8")
    (repo / "bin.dat").write_bytes(b"\x00\x01\x02")
    git(["add", "."], repo)
    git(["commit", "-m", "second commit\n\nBody line"], repo)

    git(["checkout", "-b", "feature"], repo)
    (repo / "feature.txt").write_text("feature\n", encoding="utf-8")
    git(["add", "."], repo)
    git(["commit", "-m", "feature commit"], repo)
    git(["checkout", "main"], repo)
    return repo


@pytest.fixture
def config_file(tmp_path: Path, git_repo: Path) -> Path:
    """Create a nested config.json and a secrets file."""
    secrets = tmp_path / ".secrets"
    secrets.write_text("MISTRAL_API_KEY=test_key\nGITHUB_TOKEN=test_token\n", encoding="utf-8")

    data = {
        "SECRETS_PATH": str(secrets),
        "SCAN": {
            "GIT_PROJECTS": [str(git_repo)],
            "DB_PATH": "db/scan.db",
            "MIRROR_DIR": "db/repos/",
            "MIRROR_FILTER": "none",
        },
        "ANALYSIS": {
            "GIT_DAY": 30,
            "DB_PATH": "db/analysis.db",
            "OUTPUT_DIR": "reports/",
            "MEMORY_DIR": "docs/memory/",
            "MAX_DIFF_CHARS_PER_CHUNK": 12_000,
            "MAX_PATCH_CHARS_PER_FILE": 2_000,
            "MAX_CHUNK_RETRIES": 1,
            "BACKOFF_FACTOR": 1.0,
            "LINK_REQUIREMENTS": False,
        },
        "LLM": {
            "PROVIDER": "mistral",
            "MODEL": "mistral/mistral-small-latest",
            "NON_INTERACTIVE": True,
        },
    }
    path = tmp_path / "config.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


@pytest.fixture
def settings(config_file: Path) -> Settings:
    return Settings.from_file(config_file)


@pytest.fixture
def sample_commit_data() -> CommitData:
    return CommitData(
        sha="abc123def456",
        author="testuser",
        date=datetime(2026, 10, 18, 12, 0, tzinfo=timezone.utc),
        message="Test commit message",
        files=[
            FileChange(
                path="test_file.py",
                status="modified",
                additions=10,
                deletions=5,
                patch="diff --git a/test_file.py b/test_file.py\n@@ -1,3 +1,5 @@\n+new line\n-old line",
            ),
        ],
    )


@pytest.fixture
def sample_analysis_result():
    from scan_git_push.analyzer import AnalysisResult

    return AnalysisResult(
        sha="abc123def456",
        author="testuser",
        date="2026-10-18T12:00:00+00:00",
        message="Test commit message",
        files=["test_file.py"],
        summary="Test summary",
        chunks_analyzed=1,
        file_changes=[
            FileChange(
                path="test_file.py",
                status="modified",
                additions=10,
                deletions=5,
            )
        ],
        additions=10,
        deletions=5,
    )


@pytest.fixture
def fake_llm():
    """A provider that never touches the network."""
    from scan_git_push.llm.base import LLMProvider, LLMResponse

    class FakeLLM(LLMProvider):
        provider_name = "fake"

        def __init__(self, model: str = "fake-model") -> None:
            super().__init__(model)
            self.prompts: list[str] = []

        def generate(self, prompt: str, system_prompt: str | None = None) -> LLMResponse:
            self.prompts.append(prompt)
            return LLMResponse(text="Fake summary", model=self.model, provider="fake")

        def is_available(self) -> bool:
            return True

        def list_models(self) -> list[str]:
            return [self.model]

    return FakeLLM()
