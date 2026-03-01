import os
import pathlib
import tomllib
from collections.abc import Callable
from functools import lru_cache
from importlib import resources
from importlib.resources.abc import Traversable
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


def get_config_dir() -> pathlib.Path:
    """Returns the user-level hepagent config directory (~/.config/hepagent)."""
    return pathlib.Path.home() / ".config" / "hepagent"


def _copy_traversable(src: Traversable, dst: pathlib.Path) -> None:
    """Recursively copy a Traversable resource tree to a filesystem path."""
    dst.mkdir(parents=True, exist_ok=True)
    for item in src.iterdir():
        dest_path = dst / item.name
        if item.is_dir():
            _copy_traversable(item, dest_path)
        else:
            dest_path.write_bytes(item.read_bytes())


def ensure_config_initialized() -> None:
    """Copy bundled agent data to ~/.config/hepagent/.agents if not already present."""
    agents_dir = get_config_dir() / ".agents"
    if agents_dir.exists():
        return
    pkg_agents: Traversable = resources.files("hepagent").joinpath("data/agents")
    _copy_traversable(pkg_agents, agents_dir)


def get_agent_dir() -> pathlib.Path:
    """Returns the path to the agent registry directory, initializing it if needed."""
    ensure_config_initialized()
    return get_config_dir() / ".agents"


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


def get_env_var[T](key: str, dtype: type[T] = int) -> T:
    """Helper to access environment variables with a TOML fallback.

    Args:
        key: Environment variable / config key.
        dtype: Target type (int, str, bool, float).

    Raises:
        KeyError: If key is missing from both env and TOML.
        TypeError: If dtype is unsupported.
        ValueError: If conversion fails.
    """
    converters: dict[type[Any], Callable[[Any], Any]] = {
        int: int,
        str: lambda x: x if isinstance(x, str) else str(x),
        float: float,
        bool: lambda x: (
            x if isinstance(x, bool) else str(x).strip().lower() in {"1", "true", "yes", "on"}
        ),
    }

    conv = converters.get(dtype)
    if conv is None:
        supported = ", ".join(t.__name__ for t in converters)
        raise TypeError(f"Unsupported dtype: {dtype!r}. Supported: {supported}")

    # 1) Environment has precedence. If set, it will always be a string.
    raw_env = os.getenv(key)
    if raw_env is not None and raw_env.strip() != "":
        try:
            return conv(raw_env)  # type: ignore[return-value]
        except (TypeError, ValueError) as e:
            raise ValueError(
                f"Invalid value for env var {key}={raw_env!r}; cannot convert to {dtype.__name__}"
            ) from e

    # 2) TOML fallback
    config = load_env_config()
    if key not in config:
        raise KeyError(f"Configuration key {key!r} not found in Environment or TOML.")

    raw_toml = config[key]
    try:
        return conv(raw_toml)  # type: ignore[return-value]
    except (TypeError, ValueError) as e:
        raise ValueError(
            f"Invalid TOML value for {key}={raw_toml!r} (type {type(raw_toml).__name__}); "
            f"cannot convert to {dtype.__name__}"
        ) from e
