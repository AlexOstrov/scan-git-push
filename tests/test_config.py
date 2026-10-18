"""Tests for configuration module."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scan_git_push.config import Settings


class TestSettings:
    """Test Settings class."""

    def test_from_file(self, temp_config: Path, temp_secrets: Path) -> None:
        """Test loading settings from a config file."""
        # Update secrets path in config to point to temp secrets
        config_data = json.loads(temp_config.read_text(encoding="utf-8"))
        config_data["SECRETS_PATH"] = str(temp_secrets)
        temp_config.write_text(json.dumps(config_data), encoding="utf-8")

        settings = Settings.from_file(temp_config)
        assert settings.git_project == "https://github.com/test/repo"
        assert settings.git_days == 7
        assert settings.llm_provider == "ollama"

    def test_from_file_not_found(self) -> None:
        """Test that FileNotFoundError is raised for missing config."""
        with pytest.raises(FileNotFoundError):
            Settings.from_file("nonexistent.json")

    def test_github_token_property(self, temp_config: Path, temp_secrets: Path) -> None:
        """Test reading GitHub token from secrets file."""
        config_data = json.loads(temp_config.read_text(encoding="utf-8"))
        config_data["SECRETS_PATH"] = str(temp_secrets)
        temp_config.write_text(json.dumps(config_data), encoding="utf-8")

        settings = Settings.from_file(temp_config)
        assert settings.github_token == "test_github_token"

    def test_github_models_token_property(self, temp_config: Path, temp_secrets: Path) -> None:
        """Test reading GitHub Models token from secrets file."""
        config_data = json.loads(temp_config.read_text(encoding="utf-8"))
        config_data["SECRETS_PATH"] = str(temp_secrets)
        temp_config.write_text(json.dumps(config_data), encoding="utf-8")

        settings = Settings.from_file(temp_config)
        assert settings.github_models_token == "test_models_token"

    def test_missing_secrets_file(self, temp_config: Path) -> None:
        """Test that FileNotFoundError is raised for missing secrets file."""
        config_data = json.loads(temp_config.read_text(encoding="utf-8"))
        config_data["SECRETS_PATH"] = "nonexistent_secrets"
        temp_config.write_text(json.dumps(config_data), encoding="utf-8")

        settings = Settings.from_file(temp_config)
        with pytest.raises(FileNotFoundError):
            _ = settings.github_token

    def test_missing_token_in_secrets(self, temp_config: Path, temp_secrets: Path) -> None:
        """Test that KeyError is raised for missing token in secrets file."""
        temp_secrets.write_text("OTHER_TOKEN=value\n", encoding="utf-8")

        config_data = json.loads(temp_config.read_text(encoding="utf-8"))
        config_data["SECRETS_PATH"] = str(temp_secrets)
        temp_config.write_text(json.dumps(config_data), encoding="utf-8")

        settings = Settings.from_file(temp_config)
        with pytest.raises(KeyError):
            _ = settings.github_token

    def test_empty_token_in_secrets(self, temp_config: Path, temp_secrets: Path) -> None:
        """Test that ValueError is raised for empty token in secrets file."""
        temp_secrets.write_text("GITHUB_TOKEN=\n", encoding="utf-8")

        config_data = json.loads(temp_config.read_text(encoding="utf-8"))
        config_data["SECRETS_PATH"] = str(temp_secrets)
        temp_config.write_text(json.dumps(config_data), encoding="utf-8")

        settings = Settings.from_file(temp_config)
        with pytest.raises(ValueError):
            _ = settings.github_token

    def test_validate_git_project_invalid_url(self) -> None:
        """Test that non-GitHub URLs are rejected."""
        with pytest.raises(ValueError):
            Settings(git_project="https://gitlab.com/user/repo")

    def test_validate_git_project_https(self, temp_config: Path) -> None:
        """Test that HTTPS GitHub URLs are accepted."""
        settings = Settings.from_file(temp_config)
        assert settings.git_project == "https://github.com/test/repo"

    def test_validate_git_project_ssh(self) -> None:
        """Test that SSH GitHub URLs are accepted."""
        settings = Settings(git_project="git@github.com:user/repo.git")
        assert settings.git_project == "git@github.com:user/repo.git"
