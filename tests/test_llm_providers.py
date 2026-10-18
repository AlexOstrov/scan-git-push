"""Tests for LLM provider modules."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from scan_git_push.llm.ollama import OllamaProvider
from scan_git_push.llm.github_models import GitHubModelsProvider
from scan_git_push.llm.base import LLMResponse


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

    @patch("openai.OpenAI")
    def test_is_available_success(self, mock_openai_cls) -> None:
        """Test availability check when API is reachable."""
        mock_client = MagicMock()
        mock_client.models.list.return_value = []
        mock_openai_cls.return_value = mock_client

        provider = GitHubModelsProvider(model="test-model", token="test-token")
        assert provider.is_available() is True

    @patch("openai.OpenAI")
    def test_is_available_failure(self, mock_openai_cls) -> None:
        """Test availability check when API is not reachable."""
        mock_client = MagicMock()
        mock_client.models.list.side_effect = Exception("Auth failed")
        mock_openai_cls.return_value = mock_client

        provider = GitHubModelsProvider(model="test-model", token="bad-token")
        assert provider.is_available() is False

    @patch("openai.OpenAI")
    def test_list_models(self, mock_openai_cls) -> None:
        """Test listing available GitHub Models."""
        mock_client = MagicMock()
        mock_model1 = MagicMock()
        mock_model1.id = "gpt-4o-mini"
        mock_model2 = MagicMock()
        mock_model2.id = "llama-3.1-70b"
        mock_client.models.list.return_value = MagicMock(data=[mock_model1, mock_model2])
        mock_openai_cls.return_value = mock_client

        provider = GitHubModelsProvider(model="test-model", token="test-token")
        models = provider.list_models()

        assert "gpt-4o-mini" in models
        assert "llama-3.1-70b" in models

    @patch("openai.OpenAI")
    def test_generate(self, mock_openai_cls) -> None:
        """Test generating a response via GitHub Models API."""
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_choice = MagicMock()
        mock_choice.message.content = "Test response"
        mock_response.choices = [mock_choice]
        mock_response.usage = MagicMock(prompt_tokens=10, completion_tokens=5, total_tokens=15)
        mock_client.chat.completions.create.return_value = mock_response
        mock_openai_cls.return_value = mock_client

        provider = GitHubModelsProvider(model="test-model", token="test-token")
        response = provider.generate("Test prompt")

        assert isinstance(response, LLMResponse)
        assert response.text == "Test response"
        assert response.model == "test-model"
        assert response.provider == "github_models"
        assert response.usage["total_tokens"] == 15
