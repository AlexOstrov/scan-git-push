"""Configuration management with Pydantic Settings."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from config.json and environment."""

    model_config = SettingsConfigDict(
        env_prefix="SGP_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Git settings
    git_project: str = Field(default="", alias="GIT_PROJECT")
    git_days: int = Field(default=7, alias="GIT_DAY", ge=1, le=365)

    # Secrets
    secrets_path: str = Field(
        default=r"d:\projects\fullstack-demo\.secrets",
        alias="SECRETS_PATH",
    )

    # LLM provider selection
    llm_provider: Literal["ollama", "github_models", "groq"] = Field(
        default="ollama",
        alias="LLM_PROVIDER",
    )

    # Ollama settings
    ollama_base_url: str = Field(
        default="http://localhost:11434",
        alias="OLLAMA_BASE_URL",
    )
    ollama_model: str = Field(default="llama3.1:8b", alias="OLLAMA_MODEL")

    # GitHub Models settings
    gh_models_model: str = Field(default="gpt-4o-mini", alias="GH_MODELS_MODEL")
    gh_models_endpoint: str = Field(
        default="https://models.github.ai/inference",
        alias="GH_MODELS_ENDPOINT",
    )

    # Groq settings
    groq_model: str = Field(
        default="llama-3.3-70b-versatile",
        alias="GROQ_MODEL",
    )
    groq_endpoint: str = Field(
        default="https://api.groq.com/openai/v1",
        alias="GROQ_ENDPOINT",
    )

    # Analysis settings
    max_diff_chars_per_chunk: int = Field(
        default=12_000,
        alias="MAX_DIFF_CHARS_PER_CHUNK",
        ge=1000,
        le=50_000,
    )
    max_retries: int = Field(default=3, alias="MAX_CHUNK_RETRIES", ge=1, le=5)
    backoff_factor: float = Field(default=2.0, alias="BACKOFF_FACTOR", ge=1.0, le=10.0)
    http_timeout: float = Field(default=30.0, alias="HTTP_TIMEOUT", ge=5.0, le=120.0)

    # Output settings
    output_dir: Path = Field(default=Path("reports/"), alias="OUTPUT_DIR")
    memory_dir: Path = Field(default=Path("docs/memory/"), alias="MEMORY_DIR")

    @field_validator("git_project")
    @classmethod
    def validate_git_project(cls, v: str) -> str:
        if v and not (v.startswith("https://github.com/") or v.startswith("git@github.com:")):
            raise ValueError("GIT_PROJECT must be a GitHub URL")
        return v

    @property
    def github_token(self) -> str:
        """Read GitHub token from secrets file."""
        return self._read_secret("GITHUB_TOKEN")

    @property
    def github_models_token(self) -> str:
        """Read GitHub Models token from secrets file."""
        return self._read_secret("GITHUB_MODELS_TOKEN")

    @property
    def groq_api_key(self) -> str:
        """Read Groq API key from secrets file."""
        return self._read_secret("GROQ_API_KEY")

    def _read_secret(self, key: str) -> str:
        """Read a secret value from the secrets file."""
        secrets_file = Path(self.secrets_path)
        if not secrets_file.exists():
            raise FileNotFoundError(f"Secrets file not found: {secrets_file}")

        content = secrets_file.read_text(encoding="utf-8")
        for line in content.splitlines():
            line = line.strip()
            if line.startswith(f"{key}="):
                value = line.split("=", 1)[1].strip()
                if not value:
                    raise ValueError(f"Secret {key} is empty in {secrets_file}")
                return value

        raise KeyError(f"Secret {key} not found in {secrets_file}")

    @classmethod
    def from_file(cls, path: str | Path = "config.json") -> Settings:
        """Load settings from a JSON config file, overriding defaults."""
        config_path = Path(path)
        if not config_path.exists():
            raise FileNotFoundError(f"Config file not found: {config_path}")

        data = json.loads(config_path.read_text(encoding="utf-8"))
        return cls(**data)
