"""Tests for hepagent.model_providers module."""

import pytest

from hepagent.model_providers import (
    SUPPORTED_MODEL_PROVIDERS,
    ModelProviderSettings,
    get_cborg_model_provider,
    get_model_provider,
    get_model_provider_settings,
    get_supported_model_providers,
    list_model_costs,
    parse_model_spec,
)


def test_get_supported_model_providers_returns_tuple():
    """get_supported_model_providers returns a non-empty tuple of strings."""
    providers = get_supported_model_providers()
    assert isinstance(providers, tuple)
    assert len(providers) > 0
    assert all(isinstance(p, str) for p in providers)


def test_supported_model_providers_constant_includes_cborg():
    """SUPPORTED_MODEL_PROVIDERS includes at least 'cborg'."""
    assert "cborg" in SUPPORTED_MODEL_PROVIDERS


def test_parse_model_spec_none_returns_default_provider():
    """parse_model_spec(None) returns (default_provider, None)."""
    provider, model = parse_model_spec(None)
    assert provider == "cborg"
    assert model is None


def test_parse_model_spec_bare_model_name():
    """parse_model_spec with bare model name uses default provider."""
    provider, model = parse_model_spec("gpt-4-turbo")
    assert provider == "cborg"
    assert model == "gpt-4-turbo"


def test_parse_model_spec_with_provider_prefix():
    """parse_model_spec correctly splits 'provider:model' format."""
    provider, model = parse_model_spec("openai:gpt-4")
    assert provider == "openai"
    assert model == "gpt-4"


def test_parse_model_spec_with_gemini_provider_prefix():
    """parse_model_spec supports gemini:model format."""
    provider, model = parse_model_spec("gemini:gemini-2.5-flash")
    assert provider == "gemini"
    assert model == "gemini-2.5-flash"


def test_parse_model_spec_empty_model_after_colon():
    """parse_model_spec treats 'provider:' (empty model) as model=None."""
    provider, model = parse_model_spec("openai:")
    assert provider == "openai"
    assert model is None


def test_parse_model_spec_unknown_provider_raises():
    """parse_model_spec raises ValueError for unsupported provider."""
    with pytest.raises(ValueError, match="Unsupported model provider"):
        parse_model_spec("unknown_provider:some-model")


def test_get_model_provider_settings_returns_settings():
    """get_model_provider_settings returns ModelProviderSettings for 'cborg'."""
    settings = get_model_provider_settings("cborg")
    assert settings.base_url is not None
    assert settings.api_key_env is not None
    assert settings.default_model is not None


def test_get_model_provider_settings_normalizes_gemini_base_url(monkeypatch):
    """Gemini provider uses OpenAI-compatible endpoint path."""
    monkeypatch.setenv("GEMINI_API_KEY", "fake-gemini-key")
    settings = get_model_provider_settings("gemini")
    assert settings.base_url.endswith("/openai")


def test_get_model_provider_settings_unsupported_raises():
    """get_model_provider_settings raises ValueError for an unknown provider."""
    with pytest.raises(ValueError, match="Unsupported model provider"):
        get_model_provider_settings("nonexistent_provider_xyz")


def test_get_model_provider_returns_model_object(monkeypatch):
    """get_model_provider returns an OpenAIChatCompletionsModel-compatible object."""
    from unittest.mock import MagicMock, patch

    from agents import OpenAIChatCompletionsModel

    with patch("hepagent.model_providers.AsyncOpenAI") as mock_client_cls:
        mock_client_cls.return_value = MagicMock()
        model = get_model_provider(model_provider="cborg")
    assert isinstance(model, OpenAIChatCompletionsModel)


def test_get_model_provider_with_explicit_model_name(monkeypatch):
    """get_model_provider accepts an explicit model_name."""
    from unittest.mock import MagicMock, patch

    from agents import OpenAIChatCompletionsModel

    with patch("hepagent.model_providers.AsyncOpenAI") as mock_client_cls:
        mock_client_cls.return_value = MagicMock()
        model = get_model_provider(model_provider="cborg", model_name="some-test-model")
    assert isinstance(model, OpenAIChatCompletionsModel)


def test_get_cborg_model_provider_returns_model():
    """get_cborg_model_provider is a convenience wrapper around get_model_provider."""
    from unittest.mock import MagicMock, patch

    from agents import OpenAIChatCompletionsModel

    with patch("hepagent.model_providers.AsyncOpenAI") as mock_client_cls:
        mock_client_cls.return_value = MagicMock()
        model = get_cborg_model_provider()
    assert isinstance(model, OpenAIChatCompletionsModel)


def test_supported_model_providers_includes_ollama():
    """SUPPORTED_MODEL_PROVIDERS includes 'ollama'."""
    assert "ollama" in SUPPORTED_MODEL_PROVIDERS


def test_get_model_provider_settings_ollama_uses_default_key(monkeypatch):
    """ollama provider uses api_key_default when env var is not set."""
    from hepagent.model_providers import get_model_provider_settings

    monkeypatch.delenv("OLLAMA_API_KEY", raising=False)
    settings = get_model_provider_settings("ollama")
    assert settings.base_url == "http://localhost:11434/v1"
    assert settings.default_model == "gemma4:e4b"
    assert settings.api_key == "ollama"


def test_get_model_provider_settings_ollama_respects_env_key(monkeypatch):
    """ollama provider uses OLLAMA_API_KEY when set."""
    monkeypatch.setenv("OLLAMA_API_KEY", "custom-key")
    settings = get_model_provider_settings("ollama")
    assert settings.api_key == "custom-key"


def test_parse_model_spec_with_ollama_provider():
    """parse_model_spec supports ollama:model format."""
    provider, model = parse_model_spec("ollama:llama3")
    assert provider == "ollama"
    assert model == "llama3"


def test_get_model_provider_ollama_returns_model_object():
    """get_model_provider returns a model object for ollama."""
    from unittest.mock import MagicMock, patch

    from agents import OpenAIChatCompletionsModel

    with patch("hepagent.model_providers.AsyncOpenAI") as mock_client_cls:
        mock_client_cls.return_value = MagicMock()
        model = get_model_provider(model_provider="ollama")
    assert isinstance(model, OpenAIChatCompletionsModel)


# --------------------------------------------------------------- model costs

_SETTINGS = ModelProviderSettings(
    base_url="https://gateway.example/",
    api_key="k",
    api_key_env="X_API_KEY",
    default_model="m",
)


class _Response:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status = status

    def raise_for_status(self):
        if self.status != 200:
            raise RuntimeError(self.status)

    def json(self):
        return self._payload


def test_list_model_costs_reads_a_litellm_gateway(monkeypatch):
    """Prices come back per million tokens, keyed by the id /v1/models uses."""
    captured = {}

    def fake_get(url, headers=None, timeout=None):
        captured["url"] = url
        captured["headers"] = headers
        return _Response(
            {
                "data": [
                    {
                        "model_name": "gpt-4o",
                        "model_info": {
                            "input_cost_per_token": 2.5e-06,
                            "output_cost_per_token": 1e-05,
                        },
                    }
                ]
            }
        )

    monkeypatch.setattr("httpx.get", fake_get)

    costs = list_model_costs("cborg", settings=_SETTINGS)

    assert costs == {"gpt-4o": {"input": 2.5, "output": 10.0}}
    assert captured["url"] == "https://gateway.example/model/info"
    assert captured["headers"]["Authorization"] == "Bearer k"


def test_list_model_costs_is_empty_when_the_gateway_does_not_price(monkeypatch):
    """A provider with no /model/info is not an error — OpenAI and Gemini have none.

    The dropdown must render bare model ids rather than fail to list models at all.
    """

    def fake_get(url, headers=None, timeout=None):
        return _Response({"error": "Not Found"}, status=404)

    monkeypatch.setattr("httpx.get", fake_get)

    assert list_model_costs("openai", settings=_SETTINGS) == {}


def test_list_model_costs_skips_models_quoting_no_price(monkeypatch):
    """An entry without an input price is dropped, not recorded as free."""

    def fake_get(url, headers=None, timeout=None):
        return _Response(
            {
                "data": [
                    {"model_name": "priced", "model_info": {"input_cost_per_token": 1e-06}},
                    {"model_name": "unpriced", "model_info": {"input_cost_per_token": None}},
                    {"model_name": "no-info"},
                ]
            }
        )

    monkeypatch.setattr("httpx.get", fake_get)

    costs = list_model_costs("cborg", settings=_SETTINGS)

    assert list(costs) == ["priced"]
    assert costs["priced"] == {"input": 1.0, "output": 0.0}
