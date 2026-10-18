"""GitHub Models API LLM provider using OpenAI-compatible endpoint."""

from __future__ import annotations

from typing import Any

from loguru import logger

from scan_git_push.llm.base import LLMProvider, LLMResponse


class GitHubModelsProvider(LLMProvider):
    """LLM provider for GitHub Models API (free tier, OpenAI-compatible)."""

    def __init__(
        self,
        model: str = "gpt-4o-mini",
        endpoint: str = "https://models.github.ai/inference",
        token: str = "",
        **kwargs: Any,
    ) -> None:
        super().__init__(model, **kwargs)
        self.endpoint = endpoint.rstrip("/")
        self.token = token
        self._client: Any = None

    @property
    def client(self):
        """Lazy-initialize the OpenAI-compatible client."""
        if self._client is None:
            from openai import OpenAI

            self._client = OpenAI(
                base_url=self.endpoint,
                api_key=self.token,
            )
        return self._client

    def is_available(self) -> bool:
        """Check if GitHub Models API is reachable with the token."""
        try:
            self.client.models.list()
            return True
        except Exception as exc:
            logger.warning(f"GitHub Models API not available: {exc}")
            return False

    def list_models(self) -> list[str]:
        """List models available on GitHub Models API."""
        try:
            response = self.client.models.list()
            return [m.id for m in response.data]
        except Exception as exc:
            logger.warning(f"Failed to list GitHub Models: {exc}")
            return []

    def generate(
        self,
        prompt: str,
        system_prompt: str | None = None,
    ) -> LLMResponse:
        """Generate a response via GitHub Models API with retry logic."""

        def _call():
            messages = []
            if system_prompt:
                messages.append({"role": "system", "content": system_prompt})
            messages.append({"role": "user", "content": prompt})

            response = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
            )
            return response

        response = self._retry_with_backoff(_call)

        usage = response.usage
        return LLMResponse(
            text=response.choices[0].message.content or "",
            model=self.model,
            provider="github_models",
            usage={
                "prompt_tokens": usage.prompt_tokens if usage else 0,
                "completion_tokens": usage.completion_tokens if usage else 0,
                "total_tokens": usage.total_tokens if usage else 0,
            },
        )
