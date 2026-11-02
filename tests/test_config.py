"""Tests for the configuration model."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from scan_git_push.config import DEFAULT_ANALYSIS_EXCLUDE, ConfigError, Settings


def _write(tmp_path: Path, data: dict) -> Path:
    path = tmp_path / "config.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


class TestNestedConfig:
    """Nested SCAN/ANALYSIS/LLM configuration."""

    def test_load_nested(self, config_file: Path) -> None:
        settings = Settings.from_file(config_file)
        assert settings.scan.git_projects
        assert settings.analysis.git_day == 30
        assert settings.llm.provider == "mistral"
        assert settings.analysis.branches == "all"
        assert settings.analysis.link_requirements is False

    def test_paths_resolve_against_config_dir(self, config_file: Path) -> None:
        settings = Settings.from_file(config_file)
        assert settings.analysis_db_path == (config_file.parent / "db" / "analysis.db")
        assert settings.scan_db_path == (config_file.parent / "db" / "scan.db")
        assert settings.mirror_dir == (config_file.parent / "db" / "repos")
        assert settings.output_dir == (config_file.parent / "reports")

    def test_default_exclude_list(self, config_file: Path) -> None:
        settings = Settings.from_file(config_file)
        assert "*.lock" in settings.analysis.exclude
        assert "dist/**" in settings.analysis.exclude
        assert "*.min.js" in DEFAULT_ANALYSIS_EXCLUDE

    def test_mirror_filter_default(self, tmp_path: Path) -> None:
        settings = Settings.from_file(_write(tmp_path, {"SCAN": {"GIT_PROJECTS": ["x"]}}))
        assert settings.scan.mirror_filter == "auto"

    def test_unknown_key_in_scan_is_rejected(self, tmp_path: Path) -> None:
        path = _write(
            tmp_path, {"SCAN": {"GIT_PROJECTS": ["https://github.com/o/r"], "TYPO": 1}}
        )
        with pytest.raises(ValidationError):
            Settings.from_file(path)

    def test_git_project_with_sections_is_rejected(self, tmp_path: Path) -> None:
        path = _write(
            tmp_path,
            {
                "GIT_PROJECT": "https://github.com/o/r",
                "SCAN": {"GIT_PROJECTS": ["https://github.com/o/r"]},
            },
        )
        with pytest.raises(ConfigError):
            Settings.from_file(path)


class TestLegacyConfig:
    """Flat 0.1 keys are translated once."""

    def test_flat_keys_are_translated(self, tmp_path: Path) -> None:
        path = _write(
            tmp_path,
            {
                "GIT_PROJECT": "https://github.com/o/r.git",
                "GIT_DAY": 5,
                "DB_PATH": "db/",
                "OUTPUT_DIR": "out/",
                "LLM_PROVIDER": "mistral",
                "MAX_DIFF_CHARS_PER_CHUNK": 9000,
            },
        )
        settings = Settings.from_file(path)
        assert settings.scan.git_projects == ["https://github.com/o/r.git"]
        assert settings.analysis.git_day == 5
        assert settings.analysis.max_diff_chars_per_chunk == 9000
        assert settings.analysis_db_path.name == "analysis.db"
        assert settings.scan_db_path.name == "scan.db"

    def test_db_path_with_file(self, tmp_path: Path) -> None:
        path = _write(tmp_path, {"GIT_PROJECT": "x", "DB_PATH": "data/custom.db"})
        settings = Settings.from_file(path)
        assert settings.analysis_db_path == (tmp_path / "data" / "custom.db")
        assert settings.scan_db_path == (tmp_path / "data" / "scan.db")

    def test_removed_provider_keys_are_ignored(self, tmp_path: Path) -> None:
        path = _write(
            tmp_path,
            {
                "GIT_PROJECT": "x",
                "GROQ_MODEL": "llama",
                "GH_MODELS_MODEL": "gpt",
            },
        )
        settings = Settings.from_file(path)
        assert not hasattr(settings.llm, "groq_model")


class TestSecrets:
    """Secrets are read from the configured file."""

    def test_mistral_key(self, config_file: Path) -> None:
        settings = Settings.from_file(config_file)
        assert settings.mistral_api_key == "test_key"

    def test_optional_secret_missing(self, tmp_path: Path) -> None:
        secrets = tmp_path / ".secrets"
        secrets.write_text("OTHER=1\n", encoding="utf-8")
        path = _write(tmp_path, {"SECRETS_PATH": str(secrets)})
        settings = Settings.from_file(path)
        assert settings.optional_secret("MISTRAL_API_KEY") is None

    def test_missing_secrets_file(self, tmp_path: Path) -> None:
        path = _write(tmp_path, {"SECRETS_PATH": str(tmp_path / "missing")})
        settings = Settings.from_file(path)
        with pytest.raises(FileNotFoundError):
            _ = settings.mistral_api_key

    def test_missing_config(self) -> None:
        with pytest.raises(FileNotFoundError):
            Settings.from_file("does-not-exist.json")
