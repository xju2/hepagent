"""Tests for the simple hepagent/tools.py module (read_markdown, run_shell_command, report_status).

Note: ``hepagent/tools.py`` is shadowed by the ``hepagent/tools/`` package so it must be loaded
via importlib rather than a standard import.
"""

import importlib.util
import sys
from pathlib import Path

import pytest

# Load the standalone tools.py file directly (it's shadowed by the tools/ package).
_tools_path = Path(__file__).resolve().parents[1] / "src" / "hepagent" / "tools.py"
_spec = importlib.util.spec_from_file_location("hepagent._tools_standalone", str(_tools_path))
tools = importlib.util.module_from_spec(_spec)  # type: ignore[arg-type]
_spec.loader.exec_module(tools)  # type: ignore[union-attr]


def test_read_markdown_returns_file_content(tmp_path):
    """read_markdown reads and returns file text."""
    md_file = tmp_path / "instructions.md"
    md_file.write_text("# Title\nContent here.\n")
    result = tools.read_markdown(str(md_file))
    assert result == "# Title\nContent here.\n"


def test_read_markdown_raises_on_missing_file(tmp_path):
    """read_markdown propagates FileNotFoundError for missing files."""
    with pytest.raises(FileNotFoundError):
        tools.read_markdown(str(tmp_path / "nonexistent.md"))


def test_run_shell_command_returns_stdout():
    """run_shell_command captures and returns stdout."""
    result = tools.run_shell_command("echo hello_world")
    assert "hello_world" in result


def test_run_shell_command_raises_on_failure():
    """run_shell_command raises RuntimeError when exit code is non-zero."""
    with pytest.raises(RuntimeError, match="Command failed"):
        tools.run_shell_command("exit 1")


def test_report_status_prints_message(capsys):
    """report_status prints its message to stdout."""
    tools.report_status("progress: 50%")
    captured = capsys.readouterr()
    assert "progress: 50%" in captured.out
