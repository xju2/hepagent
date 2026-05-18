"""JFC pixi task execution tools."""

from __future__ import annotations

import subprocess

from agents import function_tool

_MAX_OUTPUT = 8000


@function_tool
async def run_pixi_task(
    task_name: str,
    analysis_root: str,
    args: list[str] | None = None,
    timeout_seconds: int = 300,
) -> str:
    """
    Run a pixi task in the analysis directory.

    Examples:
      run_pixi_task("all", "/analyses/z_boson")
      run_pixi_task("plot", "/analyses/z_boson", args=["--phase", "3"])

    Returns stdout+stderr combined. Raises on non-zero exit.

    Args:
        task_name: Name of the pixi task to run.
        analysis_root: Absolute path to the analysis root directory.
        args: Additional arguments to pass after the task name.
        timeout_seconds: Timeout in seconds (default 300).
    """
    cmd = ["pixi", "run", task_name] + (args or [])
    try:
        result = subprocess.run(
            cmd,
            cwd=analysis_root,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
        output = result.stdout + result.stderr
        if len(output) > _MAX_OUTPUT:
            output = output[:_MAX_OUTPUT] + f"\n...[truncated, {len(output)} total chars]"
        if result.returncode != 0:
            return f"pixi run {task_name} failed (exit {result.returncode}):\n{output}"
        return output or f"pixi run {task_name} completed (no output)"
    except subprocess.TimeoutExpired:
        return f"Error: pixi run {task_name} timed out after {timeout_seconds}s"
    except FileNotFoundError:
        return "Error: pixi not found. Ensure pixi is installed and on PATH."
    except OSError as e:
        return f"Error running pixi task: {e}"


@function_tool
async def list_pixi_tasks(analysis_root: str) -> str:
    """
    List available pixi tasks in the analysis directory.

    Args:
        analysis_root: Absolute path to the analysis root directory.
    """
    try:
        result = subprocess.run(
            ["pixi", "task", "list"],
            cwd=analysis_root,
            capture_output=True,
            text=True,
            timeout=30,
        )
        output = result.stdout + result.stderr
        return output or "No tasks found or pixi task list returned no output."
    except FileNotFoundError:
        return "Error: pixi not found."
    except OSError as e:
        return f"Error listing pixi tasks: {e}"
