"""Abstract base class for LLM providers."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class LLMResponse:
    """Standardized response from any LLM provider."""

    text: str
    model: str
    provider: str
    usage: dict[str, Any] = field(default_factory=dict)


class LLMProvider(ABC):
    """Abstract base for LLM providers (Ollama, GitHub Models, etc.)."""

    def __init__(self, model: str, **kwargs: Any) -> None:
        self.model = model
        self.kwargs = kwargs

    @abstractmethod
    def generate(self, prompt: str, system_prompt: str | None = None) -> LLMResponse:
        """Send a prompt to the LLM and return the response."""
        raise NotImplementedError

    @abstractmethod
    def is_available(self) -> bool:
        """Check if the provider is reachable and ready."""
        raise NotImplementedError

    @abstractmethod
    def list_models(self) -> list[str]:
        """List available models from this provider."""
        raise NotImplementedError

    def _retry_with_backoff(self, func, max_retries: int = 3, backoff_factor: float = 2.0):
        """Execute a function with exponential backoff retry logic.

        Retries on transient network/HTTP errors up to max_retries attempts.
        """
        import time
        from loguru import logger

        last_exception: Exception | None = None

        for attempt in range(1, max_retries + 1):
            try:
                return func()
            except Exception as exc:
                last_exception = exc
                if attempt < max_retries:
                    wait_time = backoff_factor ** (attempt - 1)
                    logger.warning(
                        f"Attempt {attempt}/{max_retries} failed: {exc}. "
                        f"Retrying in {wait_time:.1f}s..."
                    )
                    time.sleep(wait_time)
                else:
                    logger.error(f"All {max_retries} attempts failed: {exc}")

        raise last_exception  # type: ignore[misc]
