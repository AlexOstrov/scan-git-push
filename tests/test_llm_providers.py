"""Tests for LLM providers and model discovery."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import httpx
import pytest

from scan_git_push.llm.mistral import KNOWN_MISTRAL_MODELS, MistralProvider
from scan_git_push.llm.ollama import OllamaProvider
from scan_git_push.llm.registry import ModelOption, ModelRegistry


def _response(status_code: int, payload: dict | None = None) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = payload or {}
    resp.raise_for_status.return_value = None
    return resp


def _http_error(status_code: int) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = {}
    resp.raise_for_status.side_effect = httpx.HTTPStatusError(
        f"HTTP {status_code}", request=MagicMock(), response=resp
    )
    return resp


class TestOllamaProvider:
    @patch("ollama.Client")
    def test_is_available(self, client_cls: MagicMock) -> None:
        client_cls.return_value.list.return_value = []
        assert OllamaProvider(model="m").is_available() is True

    @patch("ollama.Client")
    def test_is_available_failure(self, client_cls: MagicMock) -> None:
        client_cls.return_value.list.side_effect = Exception("refused")
        assert OllamaProvider(model="m").is_available() is False

    @patch("ollama.Client")
    def test_list_models(self, client_cls: MagicMock) -> None:
        model = MagicMock()
        model.model = "llama3.1:8b"
        client_cls.return_value.list.return_value = MagicMock(models=[model])
        assert OllamaProvider(model="m").list_models() == ["llama3.1:8b"]

    @patch("ollama.Client")
    def test_generate(self, client_cls: MagicMock) -> None:
        response = MagicMock()
        response.message.content = "answer"
        response.get.return_value = 7
        client_cls.return_value.chat.return_value = response

        result = OllamaProvider(model="test-model").generate("hi")

        assert result.text == "answer"
        assert result.provider == "ollama"
        assert result.model == "test-model"
        assert result.usage == {"prompt_eval_count": 7, "eval_count": 7}

    @patch("ollama.Client")
    def test_generate_retries(self, client_cls: MagicMock) -> None:
        response = MagicMock()
        response.message.content = "ok"
        response.get.return_value = 0
        client_cls.return_value.chat.side_effect = [
            RuntimeError("boom"),
            response,
        ]

        provider = OllamaProvider(model="m", max_retries=2, backoff_factor=1.0)
        with patch("time.sleep"):
            assert provider.generate("hi").text == "ok"
        assert client_cls.return_value.chat.call_count == 2


class TestMistralProvider:
    @patch("scan_git_push.llm.openai_compatible.httpx.post")
    def test_is_available_success(self, post: MagicMock) -> None:
        post.return_value = _response(200, {"choices": [{"x": 1}]})
        assert MistralProvider(model="m", token="t").is_available() is True

    @patch("scan_git_push.llm.openai_compatible.httpx.post")
    def test_is_available_rate_limited(self, post: MagicMock) -> None:
        post.return_value = _http_error(429)
        assert MistralProvider(model="m", token="t").is_available() is True

    @patch("scan_git_push.llm.openai_compatible.httpx.post")
    def test_is_available_error(self, post: MagicMock) -> None:
        post.return_value = _http_error(401)
        assert MistralProvider(model="m", token="t").is_available() is False

    def test_is_available_without_token(self) -> None:
        assert MistralProvider(model="m", token="").is_available() is False

    @patch("scan_git_push.llm.openai_compatible.httpx.get")
    def test_list_models_from_endpoint(self, get: MagicMock) -> None:
        get.return_value = _response(200, {"data": [{"id": "m1"}, {"id": "m2"}]})
        assert MistralProvider(model="m", token="t").list_models() == ["m1", "m2"]

    @patch("scan_git_push.llm.openai_compatible.httpx.get")
    def test_list_models_fallback(self, get: MagicMock) -> None:
        get.side_effect = RuntimeError("down")
        models = MistralProvider(model="m", token="t").list_models()
        assert models == KNOWN_MISTRAL_MODELS
        assert "mistral-small-latest" in models

    @patch("scan_git_push.llm.openai_compatible.httpx.post")
    def test_generate(self, post: MagicMock) -> None:
        post.return_value = _response(
            200,
            {
                "choices": [{"message": {"content": "answer"}}],
                "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
            },
        )
        result = MistralProvider(model="mistral-small-latest", token="t").generate("hi")

        assert result.text == "answer"
        assert result.provider == "mistral"
        assert result.usage["total_tokens"] == 5

    @patch("scan_git_push.llm.openai_compatible.httpx.post")
    def test_generate_retries_then_succeeds(self, post: MagicMock) -> None:
        post.side_effect = [
            RuntimeError("boom"),
            _response(200, {"choices": [{"message": {"content": "ok"}}]}),
        ]
        provider = MistralProvider(model="m", token="t", max_retries=2, backoff_factor=1.0)
        with patch("time.sleep"):
            assert provider.generate("hi").text == "ok"
        assert post.call_count == 2

    @patch("scan_git_push.llm.openai_compatible.httpx.post")
    def test_generate_raises_after_retries(self, post: MagicMock) -> None:
        post.side_effect = RuntimeError("boom")
        provider = MistralProvider(model="m", token="t", max_retries=2, backoff_factor=1.0)
        with patch("time.sleep"), pytest.raises(RuntimeError):
            provider.generate("hi")


class TestNoLegacyProviders:
    def test_github_models_module_is_gone(self) -> None:
        with pytest.raises(ModuleNotFoundError):
            __import__("scan_git_push.llm.github_models")

    def test_groq_module_is_gone(self) -> None:
        with pytest.raises(ModuleNotFoundError):
            __import__("scan_git_push.llm.groq")


class TestModelRegistry:
    def test_parse_spec_with_provider(self, settings) -> None:
        registry = ModelRegistry(settings)
        assert registry.parse_spec("ollama/llama3.1:8b") == ModelOption(
            "ollama", "llama3.1:8b"
        )

    def test_parse_spec_defaults_to_configured_provider(self, settings) -> None:
        registry = ModelRegistry(settings)
        assert registry.parse_spec("mistral-large-latest") == ModelOption(
            "mistral", "mistral-large-latest"
        )

    def test_parse_spec_rejects_unknown_provider(self, settings) -> None:
        registry = ModelRegistry(settings)
        with pytest.raises(ValueError):
            registry.parse_spec("groq/llama")

    def test_parse_spec_rejects_empty_model(self, settings) -> None:
        registry = ModelRegistry(settings)
        with pytest.raises(ValueError):
            registry.parse_spec("ollama/")

    def test_default_option(self, settings) -> None:
        assert ModelRegistry(settings).default_option() == ModelOption(
            "mistral", "mistral-small-latest"
        )

    def test_resolve_returns_none_when_interactive(self, settings) -> None:
        settings.llm.model = None
        settings.llm.non_interactive = False
        assert ModelRegistry(settings).resolve() is None

    def test_resolve_uses_non_interactive_default(self, settings) -> None:
        settings.llm.model = None
        settings.llm.non_interactive = True
        assert ModelRegistry(settings).resolve() == ModelOption(
            "mistral", "mistral-small-latest"
        )

    def test_build_mistral_uses_secret(self, settings) -> None:
        provider = ModelRegistry(settings).build(ModelOption("mistral", "m"))
        assert provider.token == "test_key"
        assert provider.provider_name == "mistral"

    def test_build_ollama(self, settings) -> None:
        provider = ModelRegistry(settings).build(ModelOption("ollama", "llama3"))
        assert provider.provider_name == "ollama"
        assert provider.model == "llama3"

    def test_build_uses_provider_specific_timeouts(self, settings) -> None:
        settings.llm.http_timeout = 30
        settings.llm.ollama_http_timeout = 180
        ollama = ModelRegistry(settings).build(ModelOption("ollama", "llama3"))
        mistral = ModelRegistry(settings).build(ModelOption("mistral", "m"))
        assert ollama.timeout == 180
        assert mistral.timeout == 30

    @patch("scan_git_push.llm.registry.OllamaProvider")
    def test_discover_merges_providers(self, ollama_cls: MagicMock, settings) -> None:
        instance = MagicMock()
        instance.is_available.return_value = True
        instance.list_models.return_value = ["local-model"]
        ollama_cls.return_value = instance

        registry = ModelRegistry(settings)
        with patch.object(registry, "_read_cache", return_value=["mistral-small-latest"]):
            options = registry.discover()

        assert ModelOption("ollama", "local-model") in options
        assert ModelOption("mistral", "mistral-small-latest") in options

    def test_discover_without_ollama_or_key(self, settings, tmp_path) -> None:
        settings.llm.probe_ollama = False
        secrets = tmp_path / "empty.secrets"
        secrets.write_text("OTHER=1\n", encoding="utf-8")
        settings.secrets_path = str(secrets)

        with patch.object(ModelRegistry, "_read_cache", return_value=None):
            assert ModelRegistry(settings).discover() == []
