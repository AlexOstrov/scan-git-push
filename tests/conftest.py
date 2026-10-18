"""Shared fixtures for scan-git-push tests."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from scan_git_push.config import Settings


@pytest.fixture
def temp_config(tmp_path: Path) -> Path:
    """Create a temporary config.json file."""
    config_data = {
        "GIT_PROJECT": "https://github.com/test/repo",
        "GIT_DAY": 7,
        "SECRETS_PATH": str(tmp_path / ".secrets"),
        "LLM_PROVIDER": "ollama",
        "OLLAMA_BASE_URL": "http://localhost:11434",
        "OLLAMA_MODEL": "test-model",
        "GH_MODELS_MODEL": "test-model",
        "GH_MODELS_ENDPOINT": "https://models.github.ai/inference",
        "MAX_DIFF_CHARS_PER_CHUNK": 12000,
        "MAX_CHUNK_RETRIES": 3,
        "BACKOFF_FACTOR": 2.0,
        "OUTPUT_DIR": str(tmp_path / "reports"),
        "MEMORY_DIR": str(tmp_path / "docs/memory"),
    }
    config_file = tmp_path / "config.json"
    config_file.write_text(json.dumps(config_data), encoding="utf-8")
    return config_file


@pytest.fixture
def temp_secrets(tmp_path: Path) -> Path:
    """Create a temporary secrets file."""
    secrets_file = tmp_path / ".secrets"
    secrets_file.write_text(
        "GITHUB_TOKEN=test_github_token\n"
        "GITHUB_MODELS_TOKEN=test_models_token\n",
        encoding="utf-8",
    )
    return secrets_file


@pytest.fixture
def sample_commit_data():
    """Create sample CommitData for testing."""
    from scan_git_push.github_client import CommitData, FileChange

    return CommitData(
        sha="abc123def456",
        author="testuser",
        date=datetime(2026, 10, 18, 12, 0, tzinfo=timezone.utc),
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


@pytest.fixture
def sample_analysis_result():
    """Create sample AnalysisResult for testing."""
    from scan_git_push.analyzer import AnalysisResult

    return AnalysisResult(
        sha="abc123def456",
        author="testuser",
        date="2026-10-18T12:00:00+00:00",
        message="Test commit message",
        files=["test_file.py"],
        summary="Test summary",
        chunks_analyzed=1,
    )


@pytest.fixture
def mock_llm_response():
    """Create a mock LLM response for testing."""
    from scan_git_push.llm.base import LLMResponse

    return LLMResponse(
        text="Test LLM response",
        model="test-model",
        provider="ollama",
        usage={"prompt_eval_count": 100, "eval_count": 50},
    )
