"""Tests for user-defined function tools loader."""

import textwrap
from pathlib import Path
from unittest.mock import patch

import pytest

from agents import FunctionTool
from hepagent.tools.user_tools import load_user_function_tools


@pytest.fixture
def function_tools_dir(tmp_path: Path) -> Path:
    """Creates a temporary function_tools directory."""
    d = tmp_path / "function_tools"
    d.mkdir()
    return d


def test_load_plain_function(function_tools_dir: Path) -> None:
    """Plain Python functions are automatically wrapped as FunctionTool."""
    (function_tools_dir / "mytools.py").write_text(
        textwrap.dedent("""\
        def add_numbers(x: int, y: int) -> int:
            \"\"\"Adds two numbers together.

            Args:
                x: First number.
                y: Second number.
            \"\"\"
            return x + y
        """)
    )
    tools = load_user_function_tools(function_tools_dir)
    assert len(tools) == 1
    assert isinstance(tools[0], FunctionTool)
    assert tools[0].name == "add_numbers"


def test_load_already_decorated_function(function_tools_dir: Path) -> None:
    """Functions already decorated with @function_tool are used as-is."""
    (function_tools_dir / "decorated.py").write_text(
        textwrap.dedent("""\
        from agents import function_tool

        @function_tool
        def greet(name: str) -> str:
            \"\"\"Greets a person.

            Args:
                name: The name to greet.
            \"\"\"
            return f"Hello, {name}!"
        """)
    )
    tools = load_user_function_tools(function_tools_dir)
    assert len(tools) == 1
    assert isinstance(tools[0], FunctionTool)
    assert tools[0].name == "greet"


def test_skips_private_functions(function_tools_dir: Path) -> None:
    """Functions whose names start with '_' are skipped."""
    (function_tools_dir / "helpers.py").write_text(
        textwrap.dedent("""\
        def public_tool(x: int) -> int:
            \"\"\"Public tool.

            Args:
                x: Input.
            \"\"\"
            return x

        def _private_helper(x: int) -> int:
            return x + 1
        """)
    )
    tools = load_user_function_tools(function_tools_dir)
    assert len(tools) == 1
    assert tools[0].name == "public_tool"


def test_skips_private_files(function_tools_dir: Path) -> None:
    """Files whose names start with '_' (e.g. __init__.py) are skipped."""
    (function_tools_dir / "__init__.py").write_text(
        textwrap.dedent("""\
        def should_be_ignored(x: int) -> int:
            \"\"\"Should not be loaded.

            Args:
                x: Input.
            \"\"\"
            return x
        """)
    )
    tools = load_user_function_tools(function_tools_dir)
    assert tools == []


def test_empty_directory_returns_no_tools(function_tools_dir: Path) -> None:
    """An empty directory returns an empty list."""
    tools = load_user_function_tools(function_tools_dir)
    assert tools == []


def test_missing_directory_returns_no_tools(tmp_path: Path) -> None:
    """A non-existent directory returns an empty list without error."""
    tools = load_user_function_tools(tmp_path / "nonexistent")
    assert tools == []


def test_default_dir_uses_agent_dir(tmp_path: Path) -> None:
    """load_user_function_tools() with no args reads from .agents/function_tools/."""
    agents_dir = tmp_path / ".agents"
    ft_dir = agents_dir / "function_tools"
    ft_dir.mkdir(parents=True)
    (ft_dir / "mytools.py").write_text(
        textwrap.dedent("""\
        def echo(msg: str) -> str:
            \"\"\"Echoes a message.

            Args:
                msg: The message.
            \"\"\"
            return msg
        """)
    )

    with patch("hepagent.helpers.get_agent_dir", return_value=agents_dir):
        tools = load_user_function_tools()

    assert len(tools) == 1
    assert tools[0].name == "echo"
