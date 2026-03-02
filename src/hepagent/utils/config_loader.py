import shutil
from importlib import resources
from pathlib import Path

USER_CONFIG_DIR = Path.home() / ".config" / "hepagent"


def initialize_user_config():
    """Copies default config/agents from the package to ~/.config/hepagent."""
    if not USER_CONFIG_DIR.exists():
        USER_CONFIG_DIR.mkdir(parents=True, exist_ok=True)

        # Get the path to your internal assets
        pkg_assets = resources.files("hepagent").joinpath("assets")

        # Copy everything over
        # Note: as_posix() or string conversion might be needed for shutil in older Python
        shutil.copytree(str(pkg_assets), str(USER_CONFIG_DIR), dirs_exist_ok=True)
        print(f"Initialized default configurations at {USER_CONFIG_DIR}")


def get_config_path(filename: str) -> Path:
    """Returns the path to a file in the user's config directory."""
    return USER_CONFIG_DIR / filename
