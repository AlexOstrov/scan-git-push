"""Model discovery with a small on-disk cache.

Only two providers are supported: local Ollama and Mistral AI (OpenAI-compatible
endpoint). GitHub Models and Groq were removed because the first is shut down
and the second is geo-blocked.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path

from loguru import logger

from scan_git_push.config import LLMSettings, Settings
from scan_git_push.llm.base import LLMProvider
from scan_git_push.llm.mistral import MistralProvider
from scan_git_push.llm.ollama import OllamaProvider

PROVIDERS = ("ollama", "mistral")


@dataclass(frozen=True)
class ModelOption:
    """A selectable provider/model pair."""

    provider: str
    model: str

    @property
    def label(self) -> str:
        return f"[{self.provider}] {self.model}"

    @property
    def spec(self) -> str:
        return f"{self.provider}/{self.model}"


class ModelRegistry:
    """Builds providers and lists available models lazily."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.llm: LLMSettings = settings.llm

    # ------------------------------------------------------------- discovery
    def discover(self, probe_ollama: bool | None = None) -> list[ModelOption]:
        """Return models from providers that are actually usable right now."""
        options: list[ModelOption] = []

        if probe_ollama is None:
            probe_ollama = self.llm.probe_ollama
        if probe_ollama:
            ollama = OllamaProvider(model="", base_url=self.llm.ollama_base_url)
            if ollama.is_available():
                options.extend(
                    ModelOption("ollama", name) for name in ollama.list_models()
                )
            else:
                logger.info("Ollama is not available at {}", self.llm.ollama_base_url)

        mistral_token = self.settings.optional_secret("MISTRAL_API_KEY")
        if mistral_token:
            cached = self._read_cache()
            if cached is not None:
                options.extend(ModelOption("mistral", name) for name in cached)
            else:
                provider = MistralProvider(
                    model=self.llm.mistral_model,
                    endpoint=self.llm.mistral_endpoint,
                    token=mistral_token,
                    http_timeout=self.llm.http_timeout,
                )
                if provider.is_available():
                    models = provider.list_models()
                    self._write_cache(models)
                    options.extend(ModelOption("mistral", name) for name in models)
                else:
                    logger.warning("Mistral is not reachable")
        else:
            logger.info("MISTRAL_API_KEY is not set — Mistral models are not listed")

        # Remove duplicates while keeping order.
        seen: set[tuple[str, str]] = set()
        unique: list[ModelOption] = []
        for option in options:
            key = (option.provider, option.model)
            if key not in seen:
                seen.add(key)
                unique.append(option)
        return unique

    # --------------------------------------------------------------- resolve
    def resolve(self, spec: str | None = None) -> ModelOption | None:
        """Resolve ``provider/model`` or fall back to configuration defaults."""
        candidate = (spec or self.llm.model or "").strip()
        if candidate:
            return self.parse_spec(candidate)
        if self.llm.non_interactive:
            return self.default_option()
        return None

    def parse_spec(self, spec: str) -> ModelOption:
        spec = spec.strip()
        if "/" in spec:
            provider, model = spec.split("/", 1)
            provider = provider.strip().lower()
        else:
            provider, model = self.llm.provider, spec
        if provider not in PROVIDERS:
            raise ValueError(
                f"unknown provider '{provider}' (supported: {', '.join(PROVIDERS)})"
            )
        if not model:
            raise ValueError("model name is empty")
        return ModelOption(provider, model.strip())

    def default_option(self) -> ModelOption:
        if self.llm.provider == "ollama":
            return ModelOption("ollama", self.llm.ollama_model)
        return ModelOption("mistral", self.llm.mistral_model)

    # ----------------------------------------------------------------- build
    def build(self, option: ModelOption) -> LLMProvider:
        """Instantiate a provider for the selected option."""
        retries = self.settings.analysis.max_chunk_retries
        backoff = self.settings.analysis.backoff_factor
        if option.provider == "ollama":
            return OllamaProvider(
                model=option.model,
                base_url=self.llm.ollama_base_url,
                http_timeout=self.llm.http_timeout,
                max_retries=retries,
                backoff_factor=backoff,
            )
        token = self.settings.mistral_api_key
        return MistralProvider(
            model=option.model,
            endpoint=self.llm.mistral_endpoint,
            token=token,
            http_timeout=self.llm.http_timeout,
            max_retries=retries,
            backoff_factor=backoff,
        )

    # ----------------------------------------------------------------- cache
    def _read_cache(self) -> list[str] | None:
        if self.llm.model_cache_ttl <= 0:
            return None
        path = self.settings.model_cache_path
        if not path.exists():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            fetched_at = float(payload.get("fetched_at", 0))
            if time.time() - fetched_at > self.llm.model_cache_ttl:
                return None
            models = payload.get("models")
            if isinstance(models, list) and all(isinstance(item, str) for item in models):
                return models
        except (OSError, ValueError, TypeError) as exc:
            logger.debug("Ignoring invalid model cache {}: {}", path, exc)
        return None

    def _write_cache(self, models: list[str]) -> None:
        path = self.settings.model_cache_path
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps({"fetched_at": time.time(), "models": models}, indent=2),
                encoding="utf-8",
            )
        except OSError as exc:  # pragma: no cover - cache is best effort
            logger.debug("Cannot write model cache {}: {}", path, exc)


def cache_path_for(settings: Settings) -> Path:
    """Expose the cache location for tests and diagnostics."""
    return settings.model_cache_path
