import pathlib

from hepagent.helpers import _copy_traversable, get_config_dir
from hepagent.utils.config_loader import initialize_user_config as ensure_config_initialized


def test_get_config_dir():
    """Verify get_config_dir returns ~/.config/hepagent."""
    config_dir = get_config_dir()
    assert config_dir == pathlib.Path.home() / ".config" / "hepagent"


def test_ensure_config_initialized_creates_agents_dir(tmp_path, monkeypatch):
    """Verify ensure_config_initialized copies bundled agent data to the config dir."""
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setattr(pathlib.Path, "home", staticmethod(lambda: fake_home))

    config_dir = fake_home / ".config" / "hepagent"
    agents_dir = config_dir / ".agents"
    assert not agents_dir.exists()

    ensure_config_initialized()

    assert agents_dir.exists()
    assert (agents_dir / "common" / "OPERATION.md").exists()
    assert (agents_dir / "storage" / "MEMORY.md").exists()
    assert (agents_dir / "skills" / "nyx" / "SKILL.md").exists()


def test_ensure_config_initialized_does_not_overwrite(tmp_path, monkeypatch):
    """Verify ensure_config_initialized does not overwrite existing config."""
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setattr(pathlib.Path, "home", staticmethod(lambda: fake_home))

    agents_dir = fake_home / ".config" / "hepagent" / ".agents"
    agents_dir.mkdir(parents=True)
    sentinel = agents_dir / "sentinel.txt"
    sentinel.write_text("custom")

    ensure_config_initialized()

    # Should not have been touched
    assert sentinel.read_text() == "custom"


def test_copy_traversable(tmp_path):
    """Verify _copy_traversable correctly mirrors a resource tree to a filesystem path."""
    from importlib import resources

    src = resources.files("hepagent").joinpath("data/agents")
    dst = tmp_path / "agents_copy"
    _copy_traversable(src, dst)

    assert (dst / "common" / "OPERATION.md").exists()
    assert (dst / "skills" / "nyx" / "SKILL.md").exists()
    assert (dst / "storage" / "MEMORY.md").exists()
