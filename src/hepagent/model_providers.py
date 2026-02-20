import os
from dataclasses import dataclass

from openai import AsyncOpenAI

from agents import OpenAIChatCompletionsModel
from hepagent.helpers import load_env

DEFAULT_CBORG_MODEL: str = "gemini-flash"
DEFAULT_CBORG_BASE_URL: str = "https://api.cborg.lbl.gov"

DEFAULT_AMSC_MODEL: str = "gpt-oss-20b"
DEFAULT_AMSC_BASE_URL: str = "https://api.i2-core.american-science-cloud.org"

DEFAULT_OPENAI_MODEL: str = "gpt-5-mini"
DEFAULT_OPENAI_BASE_URL: str = "https://api.openai.com/v1"

SUPPORTED_MODEL_PROVIDERS: tuple[str, ...] = ("cborg", "amsc", "openai")


@dataclass(frozen=True)
class ModelProviderSettings:
    base_url: str
    api_key: str | None
    api_key_env: str
    default_model: str


def get_openai_api_key() -> str | None:
    load_env()
    return os.getenv("OPENAI_API_KEY")


def get_amsc_api_key() -> str | None:
    load_env()
    return os.getenv("AMSC_API_KEY")


def get_cborg_api_key() -> str | None:
    load_env()
    return os.getenv("CBORG_API_KEY")


def get_model_provider_settings(model_provider: str) -> ModelProviderSettings:
    provider = model_provider.strip().lower()
    if provider == "cborg":
        base_url = os.getenv("CBORG_BASE_URL") or DEFAULT_CBORG_BASE_URL
        return ModelProviderSettings(
            base_url=base_url,
            api_key=get_cborg_api_key(),
            api_key_env="CBORG_API_KEY",
            default_model=DEFAULT_CBORG_MODEL,
        )
    if provider == "amsc":
        load_env()
        base_url = os.getenv("AMSC_BASE_URL") or DEFAULT_AMSC_BASE_URL
        return ModelProviderSettings(
            base_url=base_url,
            api_key=get_amsc_api_key(),
            api_key_env="AMSC_API_KEY",
            default_model=DEFAULT_AMSC_MODEL,
        )
    if provider == "openai":
        load_env()
        base_url = os.getenv("OPENAI_BASE_URL") or DEFAULT_OPENAI_BASE_URL
        return ModelProviderSettings(
            base_url=base_url,
            api_key=get_openai_api_key(),
            api_key_env="OPENAI_API_KEY",
            default_model=DEFAULT_OPENAI_MODEL,
        )
    raise ValueError(f"Unsupported model provider: {model_provider}")


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
