"""Configuration management with Pydantic Settings.

The configuration is split into three nested sections:

``SCAN``      repositories, mirrors and the ``scan.db`` parameters;
``ANALYSIS``  commit collection, chunking, reports and ``analysis.db``;
``LLM``       provider selection and endpoints.

Flat keys from the 0.1 release (``GIT_PROJECT``, ``GIT_DAY``, ...) are accepted
once, translated into the nested structure and reported as deprecated.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

from loguru import logger
from pydantic import BaseModel, ConfigDict, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_ANALYSIS_EXCLUDE = [
    "*.lock",
    "package-lock.json",
    "pnpm-lock.yaml",
    "yarn.lock",
    "composer.lock",
    "poetry.lock",
    "Cargo.lock",
    "*.min.js",
    "*.min.css",
    "*.map",
    "dist/**",
    "build/**",
    "vendor/**",
    "node_modules/**",
    ".git/**",
]

BranchMode = Literal["all", "default"]
FilterMode = Literal["auto", "none", "blob:none"]


class _Section(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True, str_strip_whitespace=True)


class ScanSettings(_Section):
    """Parameters of the ``scan`` mode (see ``dop_tz01.md``)."""

    git_projects: list[str] = Field(default_factory=list, alias="GIT_PROJECTS")
    db_path: Path = Field(default=Path("db/scan.db"), alias="DB_PATH")
    mirror_dir: Path = Field(default=Path("db/repos/"), alias="MIRROR_DIR")
    mirror_filter: FilterMode = Field(default="auto", alias="MIRROR_FILTER")
    branches: BranchMode | list[str] = Field(default="all", alias="BRANCHES")
    branch_include: list[str] = Field(default_factory=list, alias="BRANCH_INCLUDE")
    branch_exclude: list[str] = Field(default_factory=list, alias="BRANCH_EXCLUDE")
    include: list[str] = Field(
        default_factory=lambda: ["**/*.txt", "**/*.md"], alias="INCLUDE"
    )
    exclude: list[str] = Field(default_factory=list, alias="EXCLUDE")
    exclude_secrets: list[str] = Field(default_factory=list, alias="EXCLUDE_SECRETS")
    max_file_size: int = Field(default=1_048_576, alias="MAX_FILE_SIZE", ge=0)
    encoding: str = Field(default="utf-8", alias="ENCODING")
    fallback_encoding: str = Field(default="cp1251", alias="FALLBACK_ENCODING")
    strict: bool = Field(default=False, alias="STRICT")
    gc_hashes: bool = Field(default=True, alias="GC_HASHES")
    log_path: Path = Field(default=Path("logs/scan.log"), alias="LOG_PATH")

    @field_validator("git_projects")
    @classmethod
    def _validate_projects(cls, value: list[str]) -> list[str]:
        return [item.strip() for item in value if item and item.strip()]


class AnalysisSettings(_Section):
    """Parameters of the commit analysis mode."""

    git_day: int = Field(default=7, alias="GIT_DAY", ge=1, le=365)
    branches: BranchMode = Field(default="all", alias="BRANCHES")
    first_parent: bool = Field(default=False, alias="FIRST_PARENT")
    max_diff_chars_per_chunk: int = Field(
        default=12_000, alias="MAX_DIFF_CHARS_PER_CHUNK", ge=1_000, le=50_000
    )
    max_patch_chars_per_file: int = Field(
        default=4_000, alias="MAX_PATCH_CHARS_PER_FILE", ge=200, le=50_000
    )
    max_chunk_retries: int = Field(default=3, alias="MAX_CHUNK_RETRIES", ge=1, le=5)
    backoff_factor: float = Field(default=2.0, alias="BACKOFF_FACTOR", ge=1.0, le=10.0)
    exclude: list[str] = Field(
        default_factory=lambda: list(DEFAULT_ANALYSIS_EXCLUDE), alias="EXCLUDE"
    )
    prompt_version: int = Field(default=1, alias="PROMPT_VERSION", ge=1)
    link_requirements: bool = Field(default=True, alias="LINK_REQUIREMENTS")
    output_dir: Path = Field(default=Path("reports/"), alias="OUTPUT_DIR")
    memory_dir: Path = Field(default=Path("docs/memory/"), alias="MEMORY_DIR")
    log_path: Path = Field(default=Path("logs/analysis.log"), alias="LOG_PATH")
    db_path: Path = Field(default=Path("db/analysis.db"), alias="DB_PATH")
    db_retention_days: int = Field(default=60, alias="DB_RETENTION_DAYS", ge=1, le=3650)


class LLMSettings(_Section):
    """LLM provider selection and endpoints."""

    provider: Literal["ollama", "mistral"] = Field(default="mistral", alias="PROVIDER")
    model: str | None = Field(default=None, alias="MODEL")
    non_interactive: bool = Field(default=False, alias="NON_INTERACTIVE")
    probe_ollama: bool = Field(default=True, alias="PROBE_OLLAMA")
    model_cache_ttl: int = Field(default=3_600, alias="MODEL_CACHE_TTL", ge=0, le=86_400)
    http_timeout: float = Field(default=30.0, alias="HTTP_TIMEOUT", ge=5.0, le=120.0)
    ollama_http_timeout: float = Field(
        default=180.0, alias="OLLAMA_HTTP_TIMEOUT", ge=5.0, le=600.0
    )

    mistral_model: str = Field(default="mistral-small-latest", alias="MISTRAL_MODEL")
    mistral_endpoint: str = Field(
        default="https://api.mistral.ai/v1", alias="MISTRAL_ENDPOINT"
    )
    ollama_base_url: str = Field(
        default="http://localhost:11434", alias="OLLAMA_BASE_URL"
    )
    ollama_model: str = Field(default="llama3.1:8b", alias="OLLAMA_MODEL")


# --------------------------------------------------------------------------
# Legacy (0.1) flat keys. Only used when the file has no nested sections.
# --------------------------------------------------------------------------
_LEGACY_KEYS = {
    "GIT_PROJECT": ("SCAN", "GIT_PROJECTS"),
    "GIT_DAY": ("ANALYSIS", "GIT_DAY"),
    "LLM_PROVIDER": ("LLM", "PROVIDER"),
    "OLLAMA_BASE_URL": ("LLM", "OLLAMA_BASE_URL"),
    "OLLAMA_MODEL": ("LLM", "OLLAMA_MODEL"),
    "MISTRAL_MODEL": ("LLM", "MISTRAL_MODEL"),
    "MISTRAL_ENDPOINT": ("LLM", "MISTRAL_ENDPOINT"),
    "MAX_DIFF_CHARS_PER_CHUNK": ("ANALYSIS", "MAX_DIFF_CHARS_PER_CHUNK"),
    "MAX_CHUNK_RETRIES": ("ANALYSIS", "MAX_CHUNK_RETRIES"),
    "BACKOFF_FACTOR": ("ANALYSIS", "BACKOFF_FACTOR"),
    "OUTPUT_DIR": ("ANALYSIS", "OUTPUT_DIR"),
    "MEMORY_DIR": ("ANALYSIS", "MEMORY_DIR"),
    "DB_RETENTION_DAYS": ("ANALYSIS", "DB_RETENTION_DAYS"),
    "HTTP_TIMEOUT": ("LLM", "HTTP_TIMEOUT"),
}

_REMOVED_KEYS = {
    "GH_MODELS_MODEL",
    "GH_MODELS_ENDPOINT",
    "GROQ_MODEL",
    "GROQ_ENDPOINT",
    "GITHUB_MODELS_TOKEN",
    "GROQ_API_KEY",
}


class ConfigError(ValueError):
    """Invalid configuration detected before the run starts."""


class Settings(BaseSettings):
    """Root application settings."""

    model_config = SettingsConfigDict(
        env_prefix="SGP_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    secrets_path: str = Field(
        default=r"d:\projects\fullstack-demo\.secrets",
        alias="SECRETS_PATH",
    )
    scan: ScanSettings = Field(default_factory=ScanSettings, alias="SCAN")
    analysis: AnalysisSettings = Field(default_factory=AnalysisSettings, alias="ANALYSIS")
    llm: LLMSettings = Field(default_factory=LLMSettings, alias="LLM")

    # Directory of the config file; relative paths are resolved against it.
    base_dir: Path = Field(default_factory=Path.cwd, exclude=True)

    # ------------------------------------------------------------------ paths
    def resolve(self, value: Path) -> Path:
        path = Path(value).expanduser()
        if path.is_absolute():
            return path
        return (self.base_dir / path).resolve()

    @property
    def secrets_file(self) -> Path:
        return Path(self.secrets_path).expanduser()

    @property
    def scan_db_path(self) -> Path:
        return self.resolve(self.scan.db_path)

    @property
    def analysis_db_path(self) -> Path:
        return self.resolve(self.analysis.db_path)

    @property
    def mirror_dir(self) -> Path:
        return self.resolve(self.scan.mirror_dir)

    @property
    def output_dir(self) -> Path:
        return self.resolve(self.analysis.output_dir)

    @property
    def memory_dir(self) -> Path:
        return self.resolve(self.analysis.memory_dir)

    @property
    def scan_log_path(self) -> Path:
        return self.resolve(self.scan.log_path)

    @property
    def analysis_log_path(self) -> Path:
        return self.resolve(self.analysis.log_path)

    @property
    def model_cache_path(self) -> Path:
        return self.analysis_db_path.parent / "llm_models.json"

    # ---------------------------------------------------------------- secrets
    @property
    def github_token(self) -> str:
        """Optional GitHub token (used by git credential helpers, if any)."""
        return self._read_secret("GITHUB_TOKEN")

    @property
    def mistral_api_key(self) -> str:
        return self._read_secret("MISTRAL_API_KEY")

    def _read_secret(self, key: str) -> str:
        secrets_file = self.secrets_file
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

    def optional_secret(self, key: str) -> str | None:
        """Return a secret or ``None`` when it is missing or empty."""
        try:
            return self._read_secret(key) or None
        except (FileNotFoundError, KeyError, ValueError):
            return None

    # ------------------------------------------------------------------ load
    @classmethod
    def from_file(cls, path: str | Path = "config.json") -> Settings:
        """Load settings from a JSON file, upgrading legacy flat keys."""
        config_path = Path(path)
        if not config_path.exists():
            raise FileNotFoundError(f"Config file not found: {config_path}")

        data = json.loads(config_path.read_text(encoding="utf-8"))
        data = _upgrade_config(data)
        settings = cls(**data)
        settings.base_dir = config_path.resolve().parent
        return settings


def _upgrade_config(data: dict[str, Any]) -> dict[str, Any]:
    """Translate a flat 0.1 config into the nested structure."""
    data = dict(data)
    for key in list(data):
        if key in _REMOVED_KEYS:
            logger.warning("Config key {} was removed and is ignored", key)
            data.pop(key)

    has_sections = any(section in data for section in ("SCAN", "ANALYSIS", "LLM"))
    if "GIT_PROJECT" in data and has_sections:
        raise ConfigError(
            "GIT_PROJECT is no longer supported; move projects to SCAN.GIT_PROJECTS"
        )
    if has_sections:
        return data

    logger.warning(
        "Flat config detected: keys are translated to SCAN/ANALYSIS/LLM. "
        "Update config.json to the nested format."
    )

    scan: dict[str, Any] = {}
    analysis: dict[str, Any] = {}
    llm: dict[str, Any] = {}
    root: dict[str, Any] = {}
    buckets = {"SCAN": scan, "ANALYSIS": analysis, "LLM": llm}

    project = data.pop("GIT_PROJECT", None)
    if project:
        scan["GIT_PROJECTS"] = [project]

    legacy_db = data.pop("DB_PATH", None)
    if legacy_db:
        path = Path(str(legacy_db))
        analysis["DB_PATH"] = path if path.suffix == ".db" else path / "analysis.db"
        scan["DB_PATH"] = (path.parent if path.suffix == ".db" else path) / "scan.db"

    for key, value in data.items():
        if key == "SECRETS_PATH":
            root[key] = value
            continue
        target = _LEGACY_KEYS.get(key)
        if target is None:
            logger.warning("Unknown config key {} is ignored", key)
            continue
        section, new_key = target
        buckets[section][new_key] = value

    result = dict(root)
    if scan:
        result["SCAN"] = scan
    if analysis:
        result["ANALYSIS"] = analysis
    if llm:
        result["LLM"] = llm
    return result
