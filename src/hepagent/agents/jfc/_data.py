"""Bundled JFC data directory locator — no package imports to avoid circular deps."""

from pathlib import Path


def get_jfc_data_dir() -> Path:
    """Return the path to the bundled JFC data directory."""
    return Path(__file__).parent / "data"
