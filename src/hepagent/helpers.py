import os
import pathlib

from dotenv import find_dotenv, load_dotenv


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


def get_repo_root() -> pathlib.Path:
    """Returns the absolute path to the hepagent repository root."""
    # Looks for the pyproject.toml as a landmark
    current = pathlib.Path(__file__).resolve()
    for parent in current.parents:
        if (parent / "pyproject.toml").exists():
            return parent
    return current.parent  # Fallback


def get_agent_dir() -> pathlib.Path:
    return get_repo_root() / ".agents"
