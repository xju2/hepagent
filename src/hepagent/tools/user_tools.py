"""Loader for user-defined function tools dropped into the function_tools directory."""

import importlib.util
import inspect
from pathlib import Path

from agents import FunctionTool, function_tool


def get_function_tools_dir() -> Path:
    """Returns the path to the user function_tools directory inside .agents/."""
    from hepagent.helpers import get_agent_dir

    return get_agent_dir() / "function_tools"


def load_user_function_tools(function_tools_dir: Path | None = None) -> list[FunctionTool]:
    """Load user-defined function tools from Python files in a directory.

    Plain Python functions are automatically wrapped with @function_tool.
    Functions already decorated with @function_tool are used as-is.

    .. warning::
        This function executes Python files found in *function_tools_dir*.
        Only load files from directories you trust.

    Args:
        function_tools_dir: Directory to scan. Defaults to .agents/function_tools/.

    Returns:
        List of FunctionTool instances discovered from the directory.
    """
    if function_tools_dir is None:
        function_tools_dir = get_function_tools_dir()

    tools: list[FunctionTool] = []
    if not function_tools_dir.exists():
        return tools

    for py_file in sorted(function_tools_dir.glob("*.py")):
        if py_file.name.startswith("_"):
            continue

        spec = importlib.util.spec_from_file_location(py_file.stem, py_file)
        if spec is None or spec.loader is None:
            continue
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)  # type: ignore[union-attr]

        for name, obj in inspect.getmembers(module):
            if name.startswith("_"):
                continue
            if isinstance(obj, FunctionTool):
                tools.append(obj)
            elif inspect.isfunction(obj) and obj.__module__ == module.__name__:
                tools.append(function_tool(obj))

    return tools
