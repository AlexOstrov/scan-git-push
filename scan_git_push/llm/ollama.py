"""Ollama LLM provider using the official Python SDK."""

from __future__ import annotations

from typing import Any

from loguru import logger

from scan_git_push.llm.base import LLMProvider, LLMResponse


class OllamaProvider(LLMProvider):
    """LLM provider that connects to a local Ollama instance."""

    def __init__(
        self,
        model: str,
        base_url: str = "http://localhost:11434",
        **kwargs: Any,
    ) -> None:
        super().__init__(model, **kwargs)
        self.base_url = base_url
        self._client: Any = None

    @property
    def client(self):
        """Lazy-initialize the Ollama client."""
        if self._client is None:
            import ollama

            self._client = ollama.Client(host=self.base_url)
        return self._client

    def is_available(self) -> bool:
        """Check if Ollama server is reachable."""
        try:
            self.client.list()
            return True
        except Exception as exc:
            logger.warning(f"Ollama not available at {self.base_url}: {exc}")
            return False

    def list_models(self) -> list[str]:
        """List models available in Ollama."""
        try:
            response = self.client.list()
            return [m.model for m in response.models if m.model]
        except Exception as exc:
            logger.warning(f"Failed to list Ollama models: {exc}")
            return []

    def generate(
        self,
        prompt: str,
        system_prompt: str | None = None,
    ) -> LLMResponse:
        """Generate a response via Ollama with retry logic."""

        def _call():
            messages = []
            if system_prompt:
                messages.append({"role": "system", "content": system_prompt})
            messages.append({"role": "user", "content": prompt})

            response = self.client.chat(model=self.model, messages=messages)
            return response

        response = self._retry_with_backoff(_call)

        return LLMResponse(
            text=response.message.content,
            model=self.model,
            provider="ollama",
            usage={
                "prompt_eval_count": response.get("prompt_eval_count", 0),
                "eval_count": response.get("eval_count", 0),
            },
        )
