import os
from dataclasses import dataclass
from typing import Any

from openai import AsyncOpenAI

from agents import OpenAIChatCompletionsModel
from hepagent.helpers import load_providers_config


@dataclass(frozen=True)
class ModelProviderSettings:
    base_url: str
    api_key: str | None
    api_key_env: str
    default_model: str


def get_supported_model_providers() -> tuple[str, ...]:
    return tuple(load_providers_config().keys())


SUPPORTED_MODEL_PROVIDERS: tuple[str, ...] = get_supported_model_providers()


def _get_provider_config(model_provider: str) -> dict[str, Any]:
    provider = model_provider.strip().lower()
    providers = load_providers_config()
    if provider not in providers:
        raise ValueError(f"Unsupported model provider: {model_provider}")
    return providers[provider]


def get_model_provider_settings(model_provider: str) -> ModelProviderSettings:
    cfg = _get_provider_config(model_provider)
    base_url = cfg.get("base_url")
    api_key_env = cfg.get("api_key_env")
    default_model = cfg.get("default_model")
    if not base_url or not api_key_env or not default_model:
        raise ValueError(f"Incomplete provider configuration for {model_provider}")

    api_key = os.getenv(api_key_env)
    if not api_key:
        raise ValueError(
            f"API key not found. Please set the environment variable '{api_key_env}' "
            f"or modify your configuration $HOME/.hepagent/config/env_vars.toml "
            f"to add the API key for provider '{model_provider}'."
        )

    return ModelProviderSettings(
        base_url=base_url,
        api_key=api_key,
        api_key_env=api_key_env,
        default_model=default_model,
    )


def parse_model_spec(
    model_spec: str | None,
    default_provider: str = "cborg",
) -> tuple[str, str | None]:
    """Parse a model spec in the form 'provider:model' or a bare model name.

    Args:
        model_spec: The model specification string.
        default_provider: The default provider to use if not specified in the model_spec.

    Returns:
        A tuple of (provider, model_name) where provider is one of the supported providers

    """
    if not model_spec:
        return default_provider, None

    if ":" not in model_spec:
        return default_provider, model_spec

    provider, model_name = model_spec.split(":", 1)
    provider = provider.strip().lower()
    model_name = model_name.strip() or None
    if provider not in SUPPORTED_MODEL_PROVIDERS:
        raise ValueError(f"Unsupported model provider: {provider}")
    return provider, model_name


def get_model_provider(
    model_provider: str = "cborg",
    model_name: str | None = None,
) -> OpenAIChatCompletionsModel:
    """Get a model provider for the specified platform.
    Args:
        model_provider: One of "cborg", "amsc", or "openai".
        model_name: Name of the model to use. If None, uses the provider default.
    Returns:
        An OpenAIChatCompletionsModel configured to use the provider API.
    """
    settings = get_model_provider_settings(model_provider)
    if model_name is None:
        model_name = settings.default_model
    client = AsyncOpenAI(base_url=settings.base_url, api_key=settings.api_key)
    return OpenAIChatCompletionsModel(model=model_name, openai_client=client)


def get_cborg_model_provider(
    model_name: str | None = None,
) -> OpenAIChatCompletionsModel:
    """Backward-compatible wrapper for CBORG."""
    return get_model_provider("cborg", model_name)
