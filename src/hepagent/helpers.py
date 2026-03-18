import os
import pathlib
import shutil
import tomllib
from collections.abc import Callable
from functools import lru_cache
from importlib import resources
from typing import Any

from dotenv import find_dotenv, load_dotenv


def get_hepagent_home() -> pathlib.Path:
    """Returns the user-level config/data directory: ~/.hepagent/

    The directory is *not* created here; callers that need to write should
    create it themselves (or call bootstrap_hepagent_home).  This keeps
    read-only paths (config lookup, agent-dir resolution) free of side
    effects, which matters in CI/HPC environments where $HOME is read-only.
    """
    return pathlib.Path.home() / ".hepagent"


def load_env():
    """Loads .env, preferring ~/.hepagent/.env over find_dotenv()."""
    user_env = get_hepagent_home() / ".env"
    if user_env.exists():
        load_dotenv(user_env)
    else:
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
    """Returns the active agents directory.

    Preference order:
    1) ~/.hepagent/agents when present.
    2) repo-root .agents (dev/source checkout fallback).
    3) create ~/.hepagent/agents as a minimal fallback.
    """
    user_agents = get_hepagent_home() / "agents"
    if user_agents.exists():
        return user_agents

    repo_agents = get_repo_root() / ".agents"
    if repo_agents.exists():
        return repo_agents

    user_agents.mkdir(parents=True, exist_ok=True)
    return user_agents


def read_md(path: pathlib.Path) -> str:
    """Safely reads a markdown file, returning an empty string if missing."""
    if path.exists():
        return path.read_text(encoding="utf-8")
    return ""


def _load_toml_resource(filename: str, key: str) -> dict[str, Any]:
    user_config = get_hepagent_home() / filename
    if user_config.exists():
        data = tomllib.loads(user_config.read_text(encoding="utf-8"))
    else:
        path = resources.files("hepagent.config").joinpath(filename)
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    result = data.get(key)
    if not isinstance(result, dict) or not result:
        raise ValueError(f"No {key} configured in {filename}")
    return result


def bootstrap_hepagent_home() -> None:
    """Populate ~/.hepagent/ with default configs on first install or run.

    Copies bundled TOML defaults and the repo .agents/ directory only when
    the corresponding target does not yet exist, making the operation safe to
    call on every startup.
    """
    home = get_hepagent_home()
    home.mkdir(parents=True, exist_ok=True)

    # --- TOML config files from package resources ---
    for filename in ("providers.toml", "env_vars.toml"):
        dest = home / filename
        if not dest.exists():
            src = resources.files("hepagent.config").joinpath(filename)
            dest.write_bytes(src.read_bytes())

    # --- agents/ directory from repo root (dev install) ---
    agents_dest = home / "agents"
    if not agents_dest.exists():
        repo_agents = get_repo_root() / ".agents"
        if repo_agents.exists():
            # In a source/dev environment, copy the bundled .agents directory.
            shutil.copytree(repo_agents, agents_dest)
        else:
            # In an installed (wheel) environment, .agents may not be present
            # in the repo. Ensure a minimal agents directory structure exists
            # to avoid first-run failures.
            for subdir in ("", "common", "storage", "skills"):
                target = agents_dest if not subdir else agents_dest / subdir
                target.mkdir(parents=True, exist_ok=True)


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


def _enable_amsc_x_api_key() -> bool:
    load_env()

    import mlflow.utils.rest_utils as rest_utils

    api_key = os.getenv("AMSC_MLFLOW_API_KEY", "")
    if not api_key:
        return False

    original_http_request = rest_utils.http_request

    def patched(host_creds, endpoint, method, *args, **kwargs):
        headers = dict(kwargs.get("extra_headers") or {})
        if kwargs.get("headers") is not None:
            headers.update(dict(kwargs["headers"]))
        headers["X-Api-Key"] = api_key
        kwargs["extra_headers"] = headers
        kwargs.pop("headers", None)
        return original_http_request(host_creds, endpoint, method, *args, **kwargs)

    rest_utils.http_request = patched
    return True


def enable_mlflow_for_tracing() -> bool:
    """Enable and configure MLflow-based tracing if MLflow is available.

    This function:
    - Imports MLflow and required urllib3 warning classes.
    - Monkey-patches MLflow's HTTP client to inject the AMSC API key.
    - Sets environment variables needed for insecure TLS connections.
    - Disables insecure TLS warnings from urllib3.
    - Configures the MLflow tracking URI and experiment for log tracing.

    Returns:
        True if MLflow is successfully imported and tracing is configured;
        False if MLflow is not installed (ImportError).
    """
    try:
        import mlflow
        import urllib3
        from urllib3.exceptions import InsecureRequestWarning

        load_env()

        tracking_uri = os.getenv("MLFLOW_TRACKING_URI", "")
        if not tracking_uri:
            print("MLFLOW_TRACKING_URI not set; skipping MLflow tracing setup.")
            return False

        if "american-science-cloud.org" in tracking_uri:
            if not _enable_amsc_x_api_key():
                print(
                    "AMSC_MLFLOW_API_KEY not set; "
                    "MLflow tracing to AMSC will fail due to authentication issues."
                )
                return False
            os.environ["MLFLOW_TRACKING_INSECURE_TLS"] = "true"
            urllib3.disable_warnings(InsecureRequestWarning)

        # Optional: Set a tracking URI and an experiment
        exp_name = os.getenv("MLFLOW_EXPERIMENT_NAME", "hepagent-log-tracing")
        mlflow.set_tracking_uri(tracking_uri)
        mlflow.set_experiment(exp_name)
        return True
    except ImportError:
        return False
