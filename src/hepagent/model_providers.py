from openai import AsyncOpenAI
from hepagent.helpers import get_cborg_api_key
from agents import OpenAIChatCompletionsModel


def get_cborg_model_provider(
    model_name: str = "google/gemini-flash",
) -> OpenAIChatCompletionsModel:
    client = AsyncOpenAI(base_url="https://api.cborg.lbl.gov", api_key=get_cborg_api_key())
    return OpenAIChatCompletionsModel(model=model_name, openai_client=client)
