"""Tests for CLI secret/token validation helpers."""

from __future__ import annotations

from pathlib import Path

import pytest
import typer

from scan_git_push.cli import (
    _check_secrets,
    _require_github_models_token,
    _require_groq_token,
    _try_github_models_token,
)
from scan_git_push.config import Settings


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

    def test_secrets_file_missing(self, tmp_path: Path) -> None:
        settings = _settings(tmp_path / "nonexistent")
        with pytest.raises(typer.Exit) as exc:
            _check_secrets(settings)
        assert exc.value.exit_code == 1
