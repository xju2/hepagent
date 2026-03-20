"""Tests for hepagent.agents.role module."""

import platform

import pytest

import hepagent.agents.role as role_module
from hepagent.agents.role import (
    RoleAgentConfig,
    _os_name,
    _shell_name,
    create,
    create_role_cfg,
)

# ---------------------------------------------------------------------------
# _shell_name
# ---------------------------------------------------------------------------


def test_shell_name_returns_string():
    name = _shell_name()
    assert isinstance(name, str)
    assert len(name) > 0


def test_shell_name_on_windows_powershell(monkeypatch):
    """On Windows with PSModulePath set to 3+ entries -> powershell.exe."""
    import os

    monkeypatch.setattr(platform, "system", lambda: "Windows")
    monkeypatch.setenv("PSModulePath", os.pathsep.join(["a", "b", "c"]))
    assert _shell_name() == "powershell.exe"


def test_shell_name_on_windows_cmd(monkeypatch):
    """On Windows without enough PSModulePath entries -> cmd.exe."""
    import os

    monkeypatch.setattr(platform, "system", lambda: "Windows")
    monkeypatch.setenv("PSModulePath", os.pathsep.join(["a"]))
    assert _shell_name() == "cmd.exe"


def test_shell_name_on_linux(monkeypatch):
    """On Linux returns basename of SHELL env var."""
    monkeypatch.setattr(platform, "system", lambda: "Linux")
    monkeypatch.setenv("SHELL", "/bin/zsh")
    assert _shell_name() == "zsh"


# ---------------------------------------------------------------------------
# _os_name
# ---------------------------------------------------------------------------


def test_os_name_returns_string():
    name = _os_name()
    assert isinstance(name, str)
    assert len(name) > 0


def test_os_name_on_windows(monkeypatch):
    monkeypatch.setattr(platform, "system", lambda: "Windows")
    monkeypatch.setattr(platform, "release", lambda: "10")
    assert _os_name() == "Windows 10"


def test_os_name_on_darwin(monkeypatch):
    monkeypatch.setattr(platform, "system", lambda: "Darwin")
    monkeypatch.setattr(platform, "mac_ver", lambda: ("13.0", (), ""))
    result = _os_name()
    assert result.startswith("Darwin/MacOS")


def test_os_name_on_unknown_platform(monkeypatch):
    monkeypatch.setattr(platform, "system", lambda: "FreeBSD")
    assert _os_name() == "FreeBSD"


# ---------------------------------------------------------------------------
# create_role_cfg
# ---------------------------------------------------------------------------


def test_create_role_cfg_contains_expected_roles():
    cfg = create_role_cfg()
    assert "shell" in cfg
    assert "shell_describer" in cfg
    assert "coder" in cfg


def test_create_role_cfg_entries_are_role_agent_config():
    cfg = create_role_cfg()
    for key, value in cfg.items():
        assert isinstance(value, RoleAgentConfig), f"{key} is not a RoleAgentConfig"


# ---------------------------------------------------------------------------
# create
# ---------------------------------------------------------------------------


def test_create_valid_role(monkeypatch):
    """create() should return an Agent-like object for a known role."""
    captured = {}

    class StubAgent:
        def __init__(self, name, instructions, model, tools, **kwargs):
            captured["name"] = name
            captured["instructions"] = instructions
            captured["model"] = model
            captured["tools"] = tools

    sentinel_model = object()
    monkeypatch.setattr(role_module, "Agent", StubAgent)
    monkeypatch.setattr(role_module, "get_model_provider", lambda **_: sentinel_model)

    agent = create(role_name="shell")

    assert isinstance(agent, StubAgent)
    assert captured["model"] is sentinel_model
    assert "ShellGPT" in captured["name"] or "shell" in captured["instructions"].lower()


def test_create_raises_for_unknown_role():
    """create() raises ValueError for unrecognised role names."""
    with pytest.raises(ValueError, match="not found"):
        create(role_name="NonExistentRole")


def test_create_shell_role(monkeypatch):
    """create() works for shell role key."""
    captured = {}

    class StubAgent:
        def __init__(self, name, instructions, model, tools, **kwargs):
            captured["name"] = name

    monkeypatch.setattr(role_module, "Agent", StubAgent)
    monkeypatch.setattr(role_module, "get_model_provider", lambda **_: object())

    create(role_name="shell")
    assert "ShellGPT" in captured["name"]


def test_create_code_generator(monkeypatch):
    """create() works for CodeGenerator role."""
    captured = {}

    class StubAgent:
        def __init__(self, name, instructions, model, tools, **kwargs):
            captured["name"] = name

    monkeypatch.setattr(role_module, "Agent", StubAgent)
    monkeypatch.setattr(role_module, "get_model_provider", lambda **_: object())

    create(role_name="coder")
    assert "Code Generator" in captured["name"]
