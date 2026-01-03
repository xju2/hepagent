from openai import AsyncOpenAI
from hepagent.helpers import get_cborg_api_key
from agents import OpenAIChatCompletionsModel


def get_cborg_model_provider(
    model_name: str | None = None,
) -> OpenAIChatCompletionsModel:
    """Get a model provider for CBorg models.
    Args:
        model_name: Name of the CBorg model to use. If None, defaults to "google/gemini-flash".
    Returns:
        An OpenAIChatCompletionsModel configured to use the CBorg API.
    """
    client = AsyncOpenAI(base_url="https://api.cborg.lbl.gov", api_key=get_cborg_api_key())
    if model_name is None:
        model_name = "google/gemini-flash"
    return OpenAIChatCompletionsModel(model=model_name, openai_client=client)
