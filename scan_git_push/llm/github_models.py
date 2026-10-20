"""GitHub Models API LLM provider using an OpenAI-compatible endpoint.

Notes
-----
The GitHub Models inference endpoint (``https://models.github.ai/inference``) is
OpenAI-compatible for ``/chat/completions`` but does **not** reliably expose a
JSON ``/models`` listing. Therefore availability is checked with a tiny chat
request and model listing falls back to a curated list of known models.
"""

from __future__ import annotations

from typing import Any

import httpx
from loguru import logger

from scan_git_push.llm.base import LLMProvider, LLMResponse

# Well-known models on GitHub Models API (free tier). Used as a fallback when the
# API's listing endpoint is unavailable or returns a non-JSON response.
KNOWN_GITHUB_MODELS = [
    "gpt-4o-mini",
    "gpt-4o",
    "o1",
    "o3-mini",
    "claude-3.5-sonnet",
    "claude-sonnet-4",
    "llama-3.1-70b-instruct",
    "llama-3.3-70b-instruct",
    "meta-llama/Meta-Llama-3.1-8B-Instruct",
    "mistralai/Mistral-Small",
    "phi-3.5-mini-instruct",
    "cohere-command-r-plus",
]

# A model known to exist on GitHub Models API, used only for the availability probe.
_AVAILABILITY_PROBE_MODEL = "gpt-4o-mini"
_CATALOG_URL = "https://models.github.ai/catalog/models"
_HTTP_TIMEOUT = 30.0


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

    def _headers(self) -> dict[str, str]:
        """Auth headers for raw HTTP calls."""
        return {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
        }

    def is_available(self) -> bool:
        """Check reachability and token validity with a minimal chat request."""
        if not self.token:
            logger.warning("GitHub Models API: no token provided")
            return False
        try:
            response = self.client.chat.completions.create(
                model=_AVAILABILITY_PROBE_MODEL,
                max_tokens=1,
                messages=[{"role": "user", "content": "hi"}],
            )
            return bool(response.choices)
        except Exception as exc:
            logger.warning(f"GitHub Models API not available: {exc}")
            return False

    def list_models(self) -> list[str]:
        """List available models.

        Tries the OpenAI-compatible endpoint, then the GitHub Models catalog,
        and finally falls back to :data:`KNOWN_GITHUB_MODELS`.
        """
        ids = self._list_from_endpoint(f"{self.endpoint}/models")
        if ids:
            return ids

        ids = self._list_from_endpoint(_CATALOG_URL)
        if ids:
            return ids

        logger.debug("Falling back to the curated GitHub Models list")
        return list(KNOWN_GITHUB_MODELS)

    def _list_from_endpoint(self, url: str) -> list[str]:
        """Fetch and parse a model list from a JSON endpoint. Returns [] on failure."""
        try:
            resp = httpx.get(url, headers=self._headers(), timeout=_HTTP_TIMEOUT)
            if resp.status_code != 200:
                return []
            data = resp.json()
        except Exception as exc:
            logger.debug(f"Model listing from {url} failed: {exc}")
            return []

        if isinstance(data, dict) and isinstance(data.get("data"), list):
            return [m["id"] for m in data["data"] if isinstance(m, dict) and m.get("id")]
        if isinstance(data, list):
            return [m["id"] for m in data if isinstance(m, dict) and m.get("id")]
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
