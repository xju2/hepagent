import os
import shutil
import tomllib
from collections.abc import Callable
from contextlib import ExitStack
from functools import cached_property
from importlib import resources
from pathlib import Path
from typing import Any


class ConfigLoader:
    """Class responsible for loading and providing access to HepAgent configuration."""

    def __init__(self) -> None:
        self.user_dir = Path.home() / ".config" / "hepagent"
        self.config_path = self.user_dir / "config.toml"

        if not self.config_path.exists():
            self.initialize_user_config()

    def initialize_user_config(self) -> None:
        """Copies default assets/agents from the package to ~/.config/hepagent."""

        # ensure the user config directory exists
        self.user_dir.mkdir(parents=True, exist_ok=True)
        # Get the path to your internal assets
        pkg_assets = resources.files("hepagent").joinpath("assets")

        # Copy config.toml if it doesn't exist
        if not self.config_path.exists():
            with ExitStack() as stack:
                src_path = stack.enter_context(
                    resources.as_file(pkg_assets.joinpath("config.toml"))
                )
                shutil.copy(src_path, self.config_path)

    @cached_property
    def config(self) -> dict:
        """Loads and returns the configuration from config.toml."""
        if not self.config_path.exists():
            self.initialize_user_config()

        data = tomllib.loads(self.config_path.read_text(encoding="utf-8"))
        return data

    def getenv[T](self, key: str, dtype: type[T] = int, default: T | None = None) -> T:
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
            if default is not None:
                return default
            supported = ", ".join(t.__name__ for t in converters)
            raise TypeError(f"Unsupported dtype: {dtype!r}. Supported: {supported}")

        # 1) Environment has precedence. If set, it will always be a string.
        raw_env = os.getenv(key)
        if raw_env is not None and raw_env.strip() != "":
            try:
                return conv(raw_env)  # type: ignore[return-value]
            except (TypeError, ValueError) as e:
                raise ValueError(
                    f"Invalid var for env var {key}={raw_env!r}; cannot convert to {dtype.__name__}"
                ) from e

        # 2) TOML fallback
        config = self.config
        if key not in config:
            if default is not None:
                return default
            raise KeyError(f"Configuration key {key!r} not found in Environment or TOML.")

        raw_toml = config[key]
        try:
            return conv(raw_toml)  # type: ignore[return-value]
        except (TypeError, ValueError) as e:
            raise ValueError(
                f"Invalid TOML value for {key}={raw_toml!r} (type {type(raw_toml).__name__}); "
                f"cannot convert to {dtype.__name__}"
            ) from e

    @cached_property
    def output_word_limit(self) -> int:
        """Get the output word limit from environment variable or env_vars.toml."""
        return self.getenv("HEPAGENT_OUTPUT_WORD_LIMIT", int)

    @cached_property
    def yolo_mode(self) -> bool:
        """Check if YOLO mode is enabled via environment variable or env_vars.toml."""
        return self.getenv("HEPAGENT_YOLO", bool)

    @cached_property
    def use_mlflow_tracing(self) -> bool:
        """Check if MLflow tracing is enabled via environment variable or env_vars.toml."""
        return self.getenv("HEPAGENT_USE_MLFLOW_Tracing", bool)


env_config = ConfigLoader()
