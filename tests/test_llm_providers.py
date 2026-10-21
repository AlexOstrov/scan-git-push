"""Tests for LLM provider modules."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

import httpx

from scan_git_push.llm.ollama import OllamaProvider
from scan_git_push.llm.github_models import GitHubModelsProvider, KNOWN_GITHUB_MODELS
from scan_git_push.llm.groq import GroqProvider, KNOWN_GROQ_MODELS
from scan_git_push.llm.mistral import MistralProvider, KNOWN_MISTRAL_MODELS
from scan_git_push.llm.base import LLMResponse


def _mock_http_response(status_code: int, json_data=None):
    """Create a mock httpx.Response."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data or {}
    resp.raise_for_status.return_value = None
    return resp


def _mock_http_error(status_code: int):
    """Create a mock httpx.Response that raises HTTPStatusError on raise_for_status."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = {}
    exc = httpx.HTTPStatusError(f"HTTP {status_code}", request=MagicMock(), response=resp)
    resp.raise_for_status.side_effect = exc
    return resp


class TestOllamaProvider:
    """Test OllamaProvider class."""

    @patch("ollama.Client")
    def test_is_available_success(self, mock_client_cls) -> None:
        """Test availability check when Ollama is running."""
        mock_client = MagicMock()
        mock_client.list.return_value = []
        mock_client_cls.return_value = mock_client

        provider = OllamaProvider(model="test-model")
        assert provider.is_available() is True

    @patch("ollama.Client")
    def test_is_available_failure(self, mock_client_cls) -> None:
        """Test availability check when Ollama is not running."""
        mock_client = MagicMock()
        mock_client.list.side_effect = Exception("Connection refused")
        mock_client_cls.return_value = mock_client

        provider = OllamaProvider(model="test-model")
        assert provider.is_available() is False

    @patch("ollama.Client")
    def test_list_models(self, mock_client_cls) -> None:
        """Test listing available Ollama models."""
        mock_client = MagicMock()
        mock_model1 = MagicMock()
        mock_model1.model = "llama3.1:8b"
        mock_model2 = MagicMock()
        mock_model2.model = "gemma2:9b"
        mock_client.list.return_value = MagicMock(models=[mock_model1, mock_model2])
        mock_client_cls.return_value = mock_client

        provider = OllamaProvider(model="test-model")
        models = provider.list_models()

        assert "llama3.1:8b" in models
        assert "gemma2:9b" in models

    @patch("ollama.Client")
    def test_generate(self, mock_client_cls) -> None:
        """Test generating a response via Ollama."""
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.message.content = "Test response"
        mock_response.get.return_value = 0
        mock_client.chat.return_value = mock_response
        mock_client_cls.return_value = mock_client

        provider = OllamaProvider(model="test-model")
        response = provider.generate("Test prompt")

        assert isinstance(response, LLMResponse)
        assert response.text == "Test response"
        assert response.model == "test-model"
        assert response.provider == "ollama"


class TestGitHubModelsProvider:
    """Test GitHubModelsProvider class."""

    @patch("scan_git_push.llm.openai_compatible.httpx.post")
    def test_is_available_success(self, mock_post) -> None:
        """Test availability check when API is reachable."""
        mock_post.return_value = _mock_http_response(200, {"choices": [{"x": 1}]})

        provider = GitHubModelsProvider(model="test-model", token="test-token")
        assert provider.is_available() is True

    @patch("scan_git_push.llm.openai_compatible.httpx.post")
    def test_is_available_failure(self, mock_post) -> None:
        """Test availability check when API is not reachable."""
        mock_post.side_effect = Exception("Auth failed")

        provider = GitHubModelsProvider(model="test-model", token="bad-token")
        assert provider.is_available() is False

    def test_is_available_without_token(self) -> None:
        """No token means the provider is not available."""
        provider = GitHubModelsProvider(model="test-model", token="")
        assert provider.is_available() is False

    @patch("scan_git_push.llm.openai_compatible.httpx.get")
    def test_list_models_from_endpoint(self, mock_get) -> None:
        """Test listing models parsed from the OpenAI-compatible endpoint."""
        mock_get.return_value = _mock_http_response(200, {"data": [{"id": "m1"}, {"id": "m2"}]})
        provider = GitHubModelsProvider(model="test-model", token="test-token")
        assert provider.list_models() == ["m1", "m2"]

    @patch("scan_git_push.llm.openai_compatible.httpx.get")
    def test_list_models_fallback(self, mock_get) -> None:
        """Test fallback to the curated list when the API is unreachable."""
        mock_get.side_effect = Exception("network down")
        provider = GitHubModelsProvider(model="test-model", token="test-token")
        models = provider.list_models()
        assert models == KNOWN_GITHUB_MODELS
        assert "gpt-4o-mini" in models

    @patch("scan_git_push.llm.openai_compatible.httpx.post")
    def test_generate(self, mock_post) -> None:
        """Test generating a response via GitHub Models API."""
        mock_post.return_value = _mock_http_response(
            200,
            {
                "choices": [{"message": {"content": "Test response"}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            },
        )

        provider = GitHubModelsProvider(model="test-model", token="test-token")
        response = provider.generate("Test prompt")

        assert isinstance(response, LLMResponse)
        assert response.text == "Test response"
        assert response.model == "test-model"
        assert response.provider == "github_models"
        assert response.usage["total_tokens"] == 15


class TestGroqProvider:
    """Test GroqProvider class."""

    @patch("scan_git_push.llm.openai_compatible.httpx.post")
    def test_is_available_success(self, mock_post) -> None:
        """Test availability check when API is reachable."""
        mock_post.return_value = _mock_http_response(200, {"choices": [{"x": 1}]})

        provider = GroqProvider(model="llama-3.1-8b-instant", token="test-key")
        assert provider.is_available() is True

    def test_is_available_without_token(self) -> None:
        """No token means the provider is not available."""
        provider = GroqProvider(model="llama-3.1-8b-instant", token="")
        assert provider.is_available() is False

    @patch("scan_git_push.llm.openai_compatible.httpx.get")
    def test_list_models_fallback(self, mock_get) -> None:
        """Test fallback to the curated list when the API is unreachable."""
        mock_get.side_effect = Exception("network down")
        provider = GroqProvider(model="llama-3.1-8b-instant", token="test-key")
        models = provider.list_models()
        assert models == KNOWN_GROQ_MODELS
        assert "llama-3.3-70b-versatile" in models

    @patch("scan_git_push.llm.openai_compatible.httpx.post")
    def test_generate(self, mock_post) -> None:
        """Test generating a response via Groq."""
        mock_post.return_value = _mock_http_response(
            200,
            {
                "choices": [{"message": {"content": "Groq response"}}],
                "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
            },
        )

        provider = GroqProvider(model="llama-3.1-8b-instant", token="test-key")
        response = provider.generate("hi")

        assert response.text == "Groq response"
        assert response.provider == "groq"
        assert response.usage["total_tokens"] == 5


class TestMistralProvider:
    """Test MistralProvider class."""

    @patch("scan_git_push.llm.openai_compatible.httpx.post")
    def test_is_available_success(self, mock_post) -> None:
        """Test availability check when API is reachable."""
        mock_post.return_value = _mock_http_response(200, {"choices": [{"x": 1}]})

        provider = MistralProvider(model="mistral-small-latest", token="test-key")
        assert provider.is_available() is True

    def test_is_available_without_token(self) -> None:
        """No token means the provider is not available."""
        provider = MistralProvider(model="mistral-small-latest", token="")
        assert provider.is_available() is False

    @patch("scan_git_push.llm.openai_compatible.httpx.post")
    def test_is_available_rate_limited(self, mock_post) -> None:
        """429 means provider is reachable but busy — still available."""
        mock_post.return_value = _mock_http_error(429)
        provider = MistralProvider(model="mistral-small-latest", token="test-key")
        assert provider.is_available() is True

    @patch("scan_git_push.llm.openai_compatible.httpx.get")
    def test_list_models_fallback(self, mock_get) -> None:
        """Test fallback to the curated list when the API is unreachable."""
        mock_get.side_effect = Exception("network down")
        provider = MistralProvider(model="mistral-small-latest", token="test-key")
        models = provider.list_models()
        assert models == KNOWN_MISTRAL_MODELS
        assert "mistral-small-latest" in models

    @patch("scan_git_push.llm.openai_compatible.httpx.post")
    def test_generate(self, mock_post) -> None:
        """Test generating a response via Mistral."""
        mock_post.return_value = _mock_http_response(
            200,
            {
                "choices": [{"message": {"content": "Mistral response"}}],
                "usage": {"prompt_tokens": 4, "completion_tokens": 2, "total_tokens": 6},
            },
        )

        provider = MistralProvider(model="mistral-small-latest", token="test-key")
        response = provider.generate("hi")

        assert response.text == "Mistral response"
        assert response.provider == "mistral"
        assert response.usage["total_tokens"] == 6
