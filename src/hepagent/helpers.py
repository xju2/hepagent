import os
from dotenv import load_dotenv, find_dotenv


def load_env():
    _ = load_dotenv(find_dotenv())


def get_openai_api_key():
    load_env()
    openai_api_key = os.getenv("OPENAI_API_KEY")
    return openai_api_key


def get_cborg_api_key():
    load_env()
    cborg_api_key = os.getenv("CBORG_API_KEY")
    return cborg_api_key
