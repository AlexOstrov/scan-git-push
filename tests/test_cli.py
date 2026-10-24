"""Tests for CLI secret/token validation helpers."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import typer
from typer.testing import CliRunner

from scan_git_push.cli import app
from scan_git_push.cli import (
    _check_secrets,
    _require_github_models_token,
    _require_groq_token,
    _require_mistral_token,
    _try_github_models_token,
)
from scan_git_push.config import Settings
from scan_git_push.database import DatabaseManager
from scan_git_push.github_client import CommitData, FileChange


def _settings(secrets_path: Path, llm_provider: str = "github_models") -> Settings:
    return Settings(
        git_project="https://github.com/owner/repo",
        secrets_path=str(secrets_path),
        llm_provider=llm_provider,
    )


class TestRequireGitHubModelsToken:
    """Test _require_github_models_token."""

    def test_token_present(self, tmp_path: Path) -> None:
        secrets = tmp_path / ".secrets"
        secrets.write_text("GITHUB_TOKEN=t\nGITHUB_MODELS_TOKEN=m\n", encoding="utf-8")
        settings = _settings(secrets)
        assert _require_github_models_token(settings) == "m"

    def test_token_missing(self, tmp_path: Path) -> None:
        secrets = tmp_path / ".secrets"
        secrets.write_text("GITHUB_TOKEN=t\n", encoding="utf-8")
        settings = _settings(secrets)
        with pytest.raises(typer.Exit) as exc:
            _require_github_models_token(settings)
        assert exc.value.exit_code == 6

    def test_token_empty(self, tmp_path: Path) -> None:
        secrets = tmp_path / ".secrets"
        secrets.write_text("GITHUB_TOKEN=t\nGITHUB_MODELS_TOKEN=\n", encoding="utf-8")
        settings = _settings(secrets)
        with pytest.raises(typer.Exit) as exc:
            _require_github_models_token(settings)
        assert exc.value.exit_code == 6

    def test_secrets_file_missing(self, tmp_path: Path) -> None:
        settings = _settings(tmp_path / "nonexistent")
        with pytest.raises(typer.Exit) as exc:
            _require_github_models_token(settings)
        assert exc.value.exit_code == 6


class TestRequireGroqToken:
    """Test _require_groq_token."""

    def test_token_present(self, tmp_path: Path) -> None:
        secrets = tmp_path / ".secrets"
        secrets.write_text("GITHUB_TOKEN=t\nGROQ_API_KEY=g\n", encoding="utf-8")
        settings = _settings(secrets)
        assert _require_groq_token(settings) == "g"

    def test_token_missing(self, tmp_path: Path) -> None:
        secrets = tmp_path / ".secrets"
        secrets.write_text("GITHUB_TOKEN=t\n", encoding="utf-8")
        settings = _settings(secrets)
        with pytest.raises(typer.Exit) as exc:
            _require_groq_token(settings)
        assert exc.value.exit_code == 7

    def test_token_empty(self, tmp_path: Path) -> None:
        secrets = tmp_path / ".secrets"
        secrets.write_text("GITHUB_TOKEN=t\nGROQ_API_KEY=\n", encoding="utf-8")
        settings = _settings(secrets)
        with pytest.raises(typer.Exit) as exc:
            _require_groq_token(settings)
        assert exc.value.exit_code == 7


class TestRequireMistralToken:
    """Test _require_mistral_token."""

    def test_token_present(self, tmp_path: Path) -> None:
        secrets = tmp_path / ".secrets"
        secrets.write_text("GITHUB_TOKEN=t\nMISTRAL_API_KEY=m\n", encoding="utf-8")
        settings = _settings(secrets)
        assert _require_mistral_token(settings) == "m"

    def test_token_missing(self, tmp_path: Path) -> None:
        secrets = tmp_path / ".secrets"
        secrets.write_text("GITHUB_TOKEN=t\n", encoding="utf-8")
        settings = _settings(secrets)
        with pytest.raises(typer.Exit) as exc:
            _require_mistral_token(settings)
        assert exc.value.exit_code == 8

    def test_token_empty(self, tmp_path: Path) -> None:
        secrets = tmp_path / ".secrets"
        secrets.write_text("GITHUB_TOKEN=t\nMISTRAL_API_KEY=\n", encoding="utf-8")
        settings = _settings(secrets)
        with pytest.raises(typer.Exit) as exc:
            _require_mistral_token(settings)
        assert exc.value.exit_code == 8


class TestTryGitHubModelsToken:
    """Test _try_github_models_token (best effort)."""

    def test_token_present(self, tmp_path: Path) -> None:
        secrets = tmp_path / ".secrets"
        secrets.write_text("GITHUB_TOKEN=t\nGITHUB_MODELS_TOKEN=m\n", encoding="utf-8")
        settings = _settings(secrets)
        assert _try_github_models_token(settings) == "m"

    def test_token_missing_returns_none(self, tmp_path: Path) -> None:
        secrets = tmp_path / ".secrets"
        secrets.write_text("GITHUB_TOKEN=t\n", encoding="utf-8")
        settings = _settings(secrets)
        assert _try_github_models_token(settings) is None


class TestCheckSecrets:
    """Test _check_secrets."""

    def test_github_token_missing(self, tmp_path: Path) -> None:
        secrets = tmp_path / ".secrets"
        secrets.write_text("GITHUB_MODELS_TOKEN=m\n", encoding="utf-8")
        settings = _settings(secrets)
        with pytest.raises(typer.Exit) as exc:
            _check_secrets(settings)
        assert exc.value.exit_code == 2

    def test_github_models_provider_requires_models_token(self, tmp_path: Path) -> None:
        secrets = tmp_path / ".secrets"
        secrets.write_text("GITHUB_TOKEN=t\n", encoding="utf-8")
        settings = _settings(secrets, llm_provider="github_models")
        with pytest.raises(typer.Exit) as exc:
            _check_secrets(settings)
        assert exc.value.exit_code == 6

    def test_ollama_provider_does_not_require_models_token(self, tmp_path: Path) -> None:
        secrets = tmp_path / ".secrets"
        secrets.write_text("GITHUB_TOKEN=t\n", encoding="utf-8")
        settings = _settings(secrets, llm_provider="ollama")
        # Should not raise
        _check_secrets(settings)

    def test_groq_provider_requires_token(self, tmp_path: Path) -> None:
        secrets = tmp_path / ".secrets"
        secrets.write_text("GITHUB_TOKEN=t\n", encoding="utf-8")
        settings = _settings(secrets, llm_provider="groq")
        with pytest.raises(typer.Exit) as exc:
            _check_secrets(settings)
        assert exc.value.exit_code == 7

    def test_mistral_provider_requires_token(self, tmp_path: Path) -> None:
        secrets = tmp_path / ".secrets"
        secrets.write_text("GITHUB_TOKEN=t\n", encoding="utf-8")
        settings = _settings(secrets, llm_provider="mistral")
        with pytest.raises(typer.Exit) as exc:
            _check_secrets(settings)
        assert exc.value.exit_code == 8

    def test_secrets_file_missing(self, tmp_path: Path) -> None:
        settings = _settings(tmp_path / "nonexistent")
        with pytest.raises(typer.Exit) as exc:
            _check_secrets(settings)
        assert exc.value.exit_code == 1


class TestReportFromDb:
    """Test report-from-db command."""

    def test_report_from_db_empty(self, tmp_path: Path) -> None:
        """Test report-from-db with empty database."""
        db_dir = tmp_path / "db"
        db = DatabaseManager(db_dir)
        db.initialize()

        secrets = tmp_path / ".secrets"
        secrets.write_text("GITHUB_TOKEN=t\nMISTRAL_API_KEY=m\n", encoding="utf-8")
        config = tmp_path / "config.json"
        config.write_text(
            json.dumps(
                {
                    "GIT_PROJECT": "https://github.com/o/r",
                    "SECRETS_PATH": str(secrets),
                    "DB_PATH": str(db_dir),
                }
            ),
            encoding="utf-8",
        )

        runner = CliRunner()
        result = runner.invoke(app, ["report-from-db", "--config", str(config), "--days", "7"])
        assert result.exit_code == 0
        assert "No analyzed commits found in database" in result.output

    def test_report_from_db_with_data(self, tmp_path: Path) -> None:
        """Test report-from-db with data in database."""
        db_dir = tmp_path / "db"
        db = DatabaseManager(db_dir)
        db.initialize()

        # Save a commit to DB
        from datetime import datetime, timezone

        commit_date = datetime(2026, 10, 20, 12, 0, tzinfo=timezone.utc)
        db.save_commit_analysis(
            commit_sha="abc123",
            repo_url="https://github.com/o/r",
            analyzed_at=commit_date,
            summary="Test summary",
            files=[
                FileChange(
                    filename="test.py",
                    status="modified",
                    additions=5,
                    deletions=2,
                    patch="",
                ),
            ],
            chunks_count=1,
            author="testuser",
            message="Test commit",
        )

        secrets = tmp_path / ".secrets"
        secrets.write_text("GITHUB_TOKEN=t\nMISTRAL_API_KEY=m\n", encoding="utf-8")
        config = tmp_path / "config.json"
        config.write_text(
            json.dumps(
                {
                    "GIT_PROJECT": "https://github.com/o/r",
                    "SECRETS_PATH": str(secrets),
                    "DB_PATH": str(db_dir),
                }
            ),
            encoding="utf-8",
        )

        runner = CliRunner()
        result = runner.invoke(app, ["report-from-db", "--config", str(config), "--days", "7"])
        assert result.exit_code == 0
        assert "abc123" in result.output
        assert "testuser" in result.output
        assert "Test summary" in result.output
        assert "test.py" in result.output


class TestDatabaseMigration:
    """Test database migration for existing databases without author/message columns."""

    def test_migration_adds_columns(self, tmp_path: Path) -> None:
        """Test that existing database gets author/message columns added."""
        import sqlite3

        db_dir = tmp_path / "db"
        db_dir.mkdir(parents=True, exist_ok=True)
        db_path = db_dir / "analysis.db"

        # Create old schema without author/message
        conn = sqlite3.connect(str(db_path))
        conn.execute(
            """
            CREATE TABLE analyzed_commits (
                sha TEXT NOT NULL,
                repo_url TEXT NOT NULL,
                analyzed_at TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'success',
                summary TEXT,
                files_count INTEGER DEFAULT 0,
                chunks_count INTEGER DEFAULT 0,
                PRIMARY KEY (sha, repo_url)
            );
            """
        )
        conn.commit()
        conn.close()

        # Initialize should add columns without error
        db = DatabaseManager(db_dir)
        db.initialize()

        # Should be able to save with author/message
        from scan_git_push.github_client import CommitData, FileChange
        from datetime import datetime, timezone

        commit_date = datetime(2026, 10, 20, 12, 0, tzinfo=timezone.utc)
        db.save_commit_analysis(
            commit_sha="abc123",
            repo_url="https://github.com/o/r",
            analyzed_at=commit_date,
            summary="Test summary",
            files=[
                FileChange(
                    filename="test.py",
                    status="modified",
                    additions=5,
                    deletions=2,
                    patch="",
                ),
            ],
            chunks_count=1,
            author="testuser",
            message="Test commit",
        )

        analyses = db.get_recent_analyses("https://github.com/o/r", days=7)
        assert len(analyses) == 1
        assert analyses[0]["author"] == "testuser"
        assert analyses[0]["message"] == "Test commit"

