import pathlib
import tomllib
from functools import lru_cache
from importlib import resources
from typing import Any

from dotenv import find_dotenv, load_dotenv


def load_env():
    _ = load_dotenv(find_dotenv())


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


def read_md(path: pathlib.Path) -> str:
    """Safely reads a markdown file, returning an empty string if missing."""
    if path.exists():
        return path.read_text(encoding="utf-8")
    return ""


def _load_toml_resource(filename: str, key: str) -> dict[str, Any]:
    path = resources.files("hepagent.config").joinpath(filename)
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    result = data.get(key)
    if not isinstance(result, dict) or not result:
        raise ValueError(f"No {key} configured in {filename}")
    return result


@lru_cache
def load_env_config() -> dict[str, Any]:
    return _load_toml_resource("env_vars.toml", "env_vars")


@lru_cache
def load_providers_config() -> dict[str, dict[str, Any]]:
    return _load_toml_resource("providers.toml", "providers")
