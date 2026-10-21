"""Mistral AI provider (OpenAI-compatible, free tier available)."""

from __future__ import annotations

from scan_git_push.llm.openai_compatible import OpenAICompatibleProvider

# Common models on Mistral. The live list is fetched from /models when available.
KNOWN_MISTRAL_MODELS = [
    "mistral-large-latest",
    "mistral-medium-latest",
    "mistral-small-latest",
    "ministral-8b-latest",
    "ministral-3b-latest",
    "open-mistral-nemo",
    "open-mistral-7b",
    "codestral-latest",
    "pixtral-large-latest",
]


class MistralProvider(OpenAICompatibleProvider):
    """LLM provider for Mistral AI (https://api.mistral.ai/v1)."""

    PROVIDER_NAME = "mistral"
    DEFAULT_ENDPOINT = "https://api.mistral.ai/v1"
    DEFAULT_MODEL = "mistral-small-latest"
    KNOWN_MODELS = KNOWN_MISTRAL_MODELS
    PROBE_MODEL = "mistral-small-latest"
