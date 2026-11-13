import asyncio
import json
import sys
from dataclasses import dataclass
from time import monotonic
from typing import Any

from openai import (
    APIConnectionError,
    APITimeoutError,
    AsyncOpenAI,
    InternalServerError,
    OpenAI,
    RateLimitError,
)

from agents import OpenAIChatCompletionsModel
from hepagent.helpers import get_env_var, load_providers_config


@dataclass(frozen=True)
class ModelProviderSettings:
    base_url: str
    api_key: str | None
    api_key_env: str
    default_model: str
    api_key_default: str | None = None


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

    api_key_default = cfg.get("api_key_default")
    api_key = get_env_var(api_key_env, default="")
    if not api_key:
        if api_key_default is not None:
            api_key = api_key_default
        else:
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
        api_key_default=api_key_default,
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


def _repair_json(text: str) -> str:
    """Drop up to 3 stray characters where the parser chokes (weak models emit `"]}`)."""
    for _ in range(3):
        try:
            json.loads(text)
            break
        except json.JSONDecodeError as exc:
            text = text[: exc.pos] + text[exc.pos + 1 :]
    return text


def sanitize_tool_calls(output: list) -> None:
    """Fix tool calls a weak model garbles, which otherwise abort the run or waste the turn:
    chat-template tokens glued to the tool name and near-valid JSON arguments."""
    for item in output:
        if getattr(item, "type", None) != "function_call":
            continue
        item.name = item.name.split("<|")[0].strip()
        if item.arguments:
            repaired = _repair_json(item.arguments)
            try:
                json.loads(repaired)
                item.arguments = repaired
            except json.JSONDecodeError:
                pass  # unrepairable: the SDK reports the bad arguments to the model


RETRY_FOR_S = 1800
RETRY_MAX_DELAY_S = 60


class RetryingChatCompletionsModel(OpenAIChatCompletionsModel):
    """Ride out endpoint outages: one 500 must not end a run with hours of work behind it."""

    async def get_response(self, *args, **kwargs):
        start, delay = monotonic(), 5.0
        while True:
            try:
                response = await super().get_response(*args, **kwargs)
                sanitize_tool_calls(response.output)
                return response
            except (
                APIConnectionError,
                APITimeoutError,
                InternalServerError,
                RateLimitError,
            ) as exc:
                if monotonic() - start + delay > RETRY_FOR_S:
                    raise
                print(
                    f"[model call failed: {type(exc).__name__}; retrying in {delay:.0f} s]",
                    file=sys.stderr,
                )
                await asyncio.sleep(delay)
                delay = min(2 * delay, RETRY_MAX_DELAY_S)


def get_model_provider(
    model_provider: str = "cborg",
    model_name: str | None = None,
) -> OpenAIChatCompletionsModel:
    """Get a model provider for the specified platform.
    Args:
        model_provider: One of the configured providers (e.g. cborg, amsc, openai, gemini).
        model_name: Name of the model to use. If None, uses the provider default.
    Returns:
        An OpenAIChatCompletionsModel configured to use the provider API.
    """
    settings = get_model_provider_settings(model_provider)
    if model_name is None:
        model_name = settings.default_model
    client = AsyncOpenAI(base_url=settings.base_url, api_key=settings.api_key)
    return RetryingChatCompletionsModel(model=model_name, openai_client=client)


def list_available_models(
    model_provider: str,
    *,
    settings: ModelProviderSettings | None = None,
) -> tuple[str, ...]:
    """List the available models for a configured provider."""
    resolved_settings = settings or get_model_provider_settings(model_provider)
    client = OpenAI(
        base_url=resolved_settings.base_url,
        api_key=resolved_settings.api_key,
    )
    models = client.models.list()
    return tuple(sorted(model.id for model in models.data))


def get_cborg_model_provider(
    model_name: str | None = None,
) -> OpenAIChatCompletionsModel:
    """Backward-compatible wrapper for CBORG."""
    return get_model_provider("cborg", model_name)
