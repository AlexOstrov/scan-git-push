"""Groq provider (OpenAI-compatible, free tier available)."""

from __future__ import annotations

from scan_git_push.llm.openai_compatible import OpenAICompatibleProvider

# Common models on Groq. The live list is fetched from /models when available.
KNOWN_GROQ_MODELS = [
    "llama-3.3-70b-versatile",
    "llama-3.1-8b-instant",
    "openai/gpt-oss-120b",
    "openai/gpt-oss-20b",
    "qwen/qwen3-32b",
    "meta-llama/llama-4-scout-17b-16e-instruct",
    "meta-llama/llama-4-maverick-17b-128e-instruct",
    "mistral-saba-24b",
    "gemma2-9b-it",
    "llama-3.2-3b-preview",
]


class GroqProvider(OpenAICompatibleProvider):
    """LLM provider for Groq (https://api.groq.com/openai/v1)."""

    PROVIDER_NAME = "groq"
    DEFAULT_ENDPOINT = "https://api.groq.com/openai/v1"
    DEFAULT_MODEL = "llama-3.3-70b-versatile"
    KNOWN_MODELS = KNOWN_GROQ_MODELS
    PROBE_MODEL = "llama-3.1-8b-instant"
