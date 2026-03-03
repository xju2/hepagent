import pathlib

from hepagent.helpers import _copy_traversable
from hepagent.utils.config_loader import env_config


def test_get_config_dir():
    """Verify get_config_dir returns ~/.config/hepagent."""
    config_dir = env_config.config_path
    assert config_dir == pathlib.Path.home() / ".config" / "hepagent"


def test_copy_traversable(tmp_path):
    """Verify _copy_traversable correctly mirrors a resource tree to a filesystem path."""
    from importlib import resources

    src = resources.files("hepagent").joinpath("assets/agents")
    dst = tmp_path / "agents_copy"
    _copy_traversable(src, dst)

    assert (dst / "common" / "OPERATION.md").exists()
    assert (dst / "skills" / "nyx" / "SKILL.md").exists()
    assert (dst / "storage" / "MEMORY.md").exists()
