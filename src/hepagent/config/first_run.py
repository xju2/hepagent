"""First-run setup wizard: prompts the user to choose a platform and set an API key."""

import os
import pathlib
import tomllib

import click

from hepagent.helpers import load_providers_config

_CONFIG_DIR = pathlib.Path.home() / ".config" / "hepagent"
_CONFIG_FILE = _CONFIG_DIR / "config.toml"


def get_user_config_path() -> pathlib.Path:
    """Return the path to the user config file."""
    return _CONFIG_FILE


def load_user_config() -> dict:
    """Load the user config from disk, returning an empty dict if absent."""
    if _CONFIG_FILE.exists():
        with open(_CONFIG_FILE, "rb") as fh:
            return tomllib.load(fh)
    return {}


def _escape_toml_string(value: str) -> str:
    """Escape special characters for a TOML basic string."""
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _to_toml(config: dict) -> str:
    """Minimal TOML serialiser for flat dicts and one level of nested dicts."""
    lines: list[str] = []
    for key, value in config.items():
        if isinstance(value, dict):
            lines.append(f"\n[{key}]")
            for sub_key, sub_val in value.items():
                lines.append(f'{sub_key} = "{_escape_toml_string(str(sub_val))}"')
        else:
            lines.append(f'{key} = "{_escape_toml_string(str(value))}"')
    return "\n".join(lines) + "\n"


def save_user_config(config: dict) -> None:
    """Persist *config* to the user config file as TOML."""
    _CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    _CONFIG_FILE.write_text(_to_toml(config), encoding="utf-8")


def is_first_run() -> bool:
    """Return True if no user config file exists yet."""
    return not _CONFIG_FILE.exists()


def run_setup_wizard() -> None:
    """Interactively guide the user through first-run configuration.

    Asks the user to:
    1. Choose a platform (from providers.toml).
    2. Enter the API key for that platform.

    Saves the selection to ``~/.config/hepagent/config.toml`` and exports the
    API-key env-var so the current process can use it immediately.
    """
    click.echo("Welcome to HepAgent!  Let's set up your configuration.\n")

    providers = load_providers_config()
    provider_names = list(providers.keys())

    # --- choose platform ---
    click.echo("Available platforms:")
    for i, name in enumerate(provider_names, start=1):
        click.echo(f"  {i}. {name}")

    choice = click.prompt(
        "Choose a platform",
        type=click.Choice([str(i) for i in range(1, len(provider_names) + 1)]),
        show_choices=False,
    )
    platform = provider_names[int(choice) - 1]
    api_key_env = providers[platform]["api_key_env"]

    click.echo(f"\nSelected platform: {platform}")

    # --- check / enter API key ---
    existing_key = os.getenv(api_key_env, "").strip()
    if existing_key:
        click.echo(f"{api_key_env} is already set in your environment.")
        api_key = existing_key
    else:
        api_key = click.prompt(
            f"Enter your API key for {platform} ({api_key_env})",
            hide_input=True,
            confirmation_prompt=False,
        ).strip()

    # Export for the current session
    os.environ[api_key_env] = api_key

    # Persist to config file
    config = {"default_platform": platform, "api_keys": {api_key_env: api_key}}
    save_user_config(config)

    click.echo(f"\nConfiguration saved to {_CONFIG_FILE}")
    click.echo("You can re-run setup by deleting that file.\n")


def maybe_run_setup_wizard() -> None:
    """Run the setup wizard if this is the first time ``hepagent`` is run."""
    if is_first_run():
        run_setup_wizard()
    else:
        # Load saved API keys into the environment so they are available
        # even when the user did not set them via a .env file.
        config = load_user_config()
        for env_var, value in config.get("api_keys", {}).items():
            if not os.getenv(env_var):
                os.environ[env_var] = value
