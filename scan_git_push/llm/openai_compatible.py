"""Generic OpenAI-compatible LLM provider.

Works with any service that exposes an OpenAI-compatible ``/chat/completions``
endpoint (Groq, OpenAI, Azure AI Foundry, vLLM, ...). Implemented with plain
``httpx`` so parsing does not depend on the ``openai`` SDK quirks.
"""

from __future__ import annotations

from typing import Any

import httpx
from loguru import logger

from scan_git_push.llm.base import LLMProvider, LLMResponse


class OpenAICompatibleProvider(LLMProvider):
    """Base class for OpenAI-compatible providers.

    Subclasses set the defaults via class attributes.
    """

    PROVIDER_NAME: str = "openai_compatible"
    DEFAULT_ENDPOINT: str = ""
    DEFAULT_MODEL: str = ""
    KNOWN_MODELS: list[str] = []
    PROBE_MODEL: str = ""
    _HTTP_TIMEOUT: float = 30.0

    def __init__(
        self,
        model: str | None = None,
        endpoint: str | None = None,
        token: str = "",
        **kwargs: Any,
    ) -> None:
        super().__init__(model or self.DEFAULT_MODEL, **kwargs)
        self.endpoint = (endpoint or self.DEFAULT_ENDPOINT).rstrip("/")
        self.token = token

    @property
    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
        }

    def _post_chat(self, messages: list[dict[str, str]], max_tokens: int | None = None) -> dict:
        """Send a chat completion request and return the parsed JSON body."""
        payload: dict[str, Any] = {"model": self.model, "messages": messages}
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens

        resp = httpx.post(
            f"{self.endpoint}/chat/completions",
            headers=self._headers,
            json=payload,
            timeout=self._HTTP_TIMEOUT,
        )
        resp.raise_for_status()
        return resp.json()

    def is_available(self) -> bool:
        """Check reachability and token validity with a minimal chat request.

        Returns True when the provider is reachable, including when it is
        temporarily rate-limited (HTTP 429). The latter lets the model appear
        in the interactive list so the user can select it; generation then
        relies on the built-in retry-with-backoff logic.
        """
        if not self.token:
            logger.warning(f"{self.PROVIDER_NAME}: no token provided")
            return False
        try:
            data = self._post_chat(
                [{"role": "user", "content": "hi"}], max_tokens=1
            )
            return bool(data.get("choices"))
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 429:
                logger.warning(
                    f"{self.PROVIDER_NAME} API rate-limited (HTTP 429) — "
                    f"provider is available but busy"
                )
                return True
            logger.warning(f"{self.PROVIDER_NAME} API not available: {exc}")
            return False
        except Exception as exc:
            logger.warning(f"{self.PROVIDER_NAME} API not available: {exc}")
            return False

    def list_models(self) -> list[str]:
        """List available models, falling back to the curated list."""
        try:
            resp = httpx.get(
                f"{self.endpoint}/models",
                headers=self._headers,
                timeout=self._HTTP_TIMEOUT,
            )
            if resp.status_code == 200:
                data = resp.json()
                if isinstance(data, dict) and isinstance(data.get("data"), list):
                    ids = [
                        m["id"]
                        for m in data["data"]
                        if isinstance(m, dict) and m.get("id")
                    ]
                    if ids:
                        return ids
        except Exception as exc:
            logger.debug(f"{self.PROVIDER_NAME}: model listing failed: {exc}")

        logger.debug(f"{self.PROVIDER_NAME}: using curated model list")
        return list(self.KNOWN_MODELS)

    def generate(
        self,
        prompt: str,
        system_prompt: str | None = None,
    ) -> LLMResponse:
        """Generate a response with retry logic."""
        messages: list[dict[str, str]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        data = self._retry_with_backoff(lambda: self._post_chat(messages))

        try:
            choice = data["choices"][0]
            content = (choice.get("message") or {}).get("content") or ""
        except (KeyError, IndexError, TypeError):
            content = ""

        usage = data.get("usage") or {}
        return LLMResponse(
            text=content,
            model=self.model,
            provider=self.PROVIDER_NAME,
            usage={
                "prompt_tokens": usage.get("prompt_tokens", 0),
                "completion_tokens": usage.get("completion_tokens", 0),
                "total_tokens": usage.get("total_tokens", 0),
            },
        )
