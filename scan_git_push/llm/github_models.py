"""GitHub Models API provider (subclass of the OpenAI-compatible provider).

Note: GitHub Models was retired on July 30, 2026. This class is kept for
backward compatibility; it is reported as unavailable when the endpoint no
longer serves JSON responses.
"""

from __future__ import annotations

from scan_git_push.llm.openai_compatible import OpenAICompatibleProvider

# Well-known models on GitHub Models API (free tier). Kept as a fallback list.
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


class GitHubModelsProvider(OpenAICompatibleProvider):
    """LLM provider for the (now retired) GitHub Models API."""

    PROVIDER_NAME = "github_models"
    DEFAULT_ENDPOINT = "https://models.github.ai/inference"
    DEFAULT_MODEL = "gpt-4o-mini"
    KNOWN_MODELS = KNOWN_GITHUB_MODELS
    PROBE_MODEL = "gpt-4o-mini"
