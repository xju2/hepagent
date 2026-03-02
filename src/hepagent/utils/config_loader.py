import shutil
from importlib import resources
from pathlib import Path


def get_user_config_dir() -> Path:
    """Returns the path to the user config directory (~/.config/hepagent)."""
    return Path.home() / ".config" / "hepagent"

def initialize_user_config():
    """Copies default config/agents from the package to ~/.config/hepagent."""
    user_config_dir = get_user_config_dir()
    if not user_config_dir.exists():
        user_config_dir.mkdir(parents=True, exist_ok=True)

        # Get the path to your internal assets
        pkg_assets = resources.files("hepagent").joinpath("assets")

        # Copy everything over
        # Note: as_posix() or string conversion might be needed for shutil in older Python
        shutil.copytree(str(pkg_assets), str(user_config_dir), dirs_exist_ok=True)
        print(f"Initialized default configurations at {user_config_dir}")


def get_config_path(filename: str) -> Path:
    """Returns the path to a file in the user's config directory."""
    return get_user_config_dir() / filename
