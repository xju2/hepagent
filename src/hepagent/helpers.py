import os
import pathlib
import re
import shutil
import tomllib
from collections.abc import Callable
from functools import lru_cache
from importlib import resources
from typing import Any

import yaml

USER_PROFILE_TEMPLATE = """# USER PROFILE

Keep this file concise. Record only durable facts that improve future interactions.

## Preferences
-

## Goals
-

## Projects
-

## Expertise
-

## Interests
-

## Constraints
-
"""


def get_hepagent_home() -> pathlib.Path:
    """Returns the user-level config/data directory: ~/.hepagent/

    The directory is *not* created here; callers that need to write should
    create it themselves (or call bootstrap_hepagent_home).  This keeps
    read-only paths (config lookup, agent-dir resolution) free of side
    effects, which matters in CI/HPC environments where $HOME is read-only.
    """
    return pathlib.Path.home() / ".hepagent"


def get_user_profile_path() -> pathlib.Path:
    """Returns the path to the user profile markdown: ~/.hepagent/USER.md."""
    return get_hepagent_home() / "USER.md"


def ensure_user_profile_file() -> pathlib.Path:
    """Ensure ~/.hepagent/USER.md exists with a minimal template."""
    profile_path = get_user_profile_path()
    profile_path.parent.mkdir(parents=True, exist_ok=True)
    if not profile_path.exists():
        profile_path.write_text(USER_PROFILE_TEMPLATE, encoding="utf-8")
    return profile_path


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
    # Always start from the packaged defaults so new entries (e.g. a freshly
    # added provider) are visible to existing installs whose user file predates
    # the addition.
    pkg_path = resources.files("hepagent.config").joinpath(filename)
    result: dict[str, Any] = dict(
        tomllib.loads(pkg_path.read_text(encoding="utf-8")).get(key) or {}
    )

    # Deep-merge user overrides: user values win at the section-field level,
    # but sections absent from the user file retain their packaged defaults.
    user_config = get_hepagent_home() / "config" / filename
    if user_config.exists():
        user_section = tomllib.loads(user_config.read_text(encoding="utf-8")).get(key) or {}
        for section_key, section_val in user_section.items():
            if (
                section_key in result
                and isinstance(result[section_key], dict)
                and isinstance(section_val, dict)
            ):
                result[section_key] = {**result[section_key], **section_val}
            else:
                result[section_key] = section_val

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
    config_dir = home / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    for filename in ("providers.toml", "env_vars.toml"):
        dest = config_dir / filename
        if not dest.exists():
            src = resources.files("hepagent.config").joinpath(filename)
            dest.write_bytes(src.read_bytes())

    # --- agents/ directory from repo root (dev install) ---
    agents_dest = home / "agents"
    repo_agents = get_repo_root() / ".agents"
    if not agents_dest.exists():
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
    elif repo_agents.exists():
        repo_skills = repo_agents / "skills"
        dest_skills = agents_dest / "skills"
        if repo_skills.exists():
            dest_skills.mkdir(parents=True, exist_ok=True)
            for src_skill in repo_skills.iterdir():
                dest_skill = dest_skills / src_skill.name
                if src_skill.is_dir() and not dest_skill.exists():
                    shutil.copytree(src_skill, dest_skill)

    # --- USER profile file ---
    ensure_user_profile_file()


@lru_cache
def load_env_config() -> dict[str, Any]:
    return _load_toml_resource("env_vars.toml", "config")


@lru_cache
def load_providers_config() -> dict[str, dict[str, Any]]:
    return _load_toml_resource("providers.toml", "providers")


def get_env_var[T](  # type: ignore
    key: str, *, dtype: type[T] = str, default: T | None = None, set_env: bool = True
) -> T:
    """Helper to access environment variables with a TOML fallback.
       The lookup order is:
            1) OS environment variables (`os.getenv`)
            2) TOML configuration loaded via `load_env_config`
            3) ``default`` (if provided)

       When a value is found in the TOML config,
       it will be set as an environment variable for future access
       if `set_env` is True (default).

    Args:
        key: Environment variable / config key.
        dtype: Target type (int, str, bool, float).
        default: Optional default value if key is missing from both env and TOML.
        set_env: If True, sets the value from TOML into the environment for future access.

    Returns:
        The value from environment, TOML, or default (if provided).

    Raises:
        KeyError: If key is missing from both env and TOML and no default is provided.
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
    if key in config:
        raw_toml = config[key]
        try:
            values = conv(raw_toml)  # type: ignore[return-value]
            is_empty_str = isinstance(values, str) and values.strip() == ""
            if values is not None and not is_empty_str:
                if set_env:
                    os.environ[key] = str(values)
                return values
        except (TypeError, ValueError) as e:
            raise ValueError(
                f"Invalid TOML value for {key}={raw_toml!r} (type {type(raw_toml).__name__}); "
                f"cannot convert to {dtype.__name__}"
            ) from e

    # 3) Use default if provided
    if default is not None:
        return default

    raise KeyError(f"Configuration key {key!r} not found in Environment or TOML.")


def _enable_amsc_x_api_key() -> bool:
    import mlflow.utils.rest_utils as rest_utils

    api_key = get_env_var("AMSC_MLFLOW_API_KEY")
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
        False if MLflow is not installed or tracing is not configured.
    """
    try:
        import mlflow
        import urllib3
        from urllib3.exceptions import InsecureRequestWarning

        tracking_uri = get_env_var("MLFLOW_TRACKING_URI", default="")
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
        exp_name = get_env_var("MLFLOW_EXPERIMENT_NAME", default="hepagent-log-tracing")
        mlflow.set_tracking_uri(tracking_uri)
        mlflow.set_experiment(exp_name)
        return True
    except ImportError:
        return False


def extract_yaml(path) -> tuple[dict[str, Any], str]:
    content = read_md(path)
    match = re.search(r"^---\s*(.*?)\s*---", content, re.DOTALL)
    frontmatter = yaml.safe_load(match.group(1)) if match else {}
    if not isinstance(frontmatter, dict):
        frontmatter = {}

    body = content[match.end() :] if match else content
    return frontmatter, body
