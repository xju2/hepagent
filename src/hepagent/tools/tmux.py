"""Tmux tools for running commands in persistent terminal sessions.

Enables the agent to create tmux sessions, send commands to them (including
SLURM salloc for interactive jobs), and read back output — all without blocking
the main agent loop.
"""

from __future__ import annotations

import re
import subprocess
import time

from agents import function_tool


def _run(args: list[str]) -> tuple[str, int]:
    """Run a tmux subcommand and return (stdout, returncode)."""
    result = subprocess.run(
        args,
        text=True,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
    )
    return result.stdout.strip(), result.returncode


def _send_keys(session_name: str, window_name: str, command: str) -> str:
    target = f"{session_name}:{window_name}"
    _, rc = _run(["tmux", "send-keys", "-t", target, command, "Enter"])
    if rc != 0:
        return f"Error: could not send keys to '{target}'. Check that the session/window exists."
    return f"Sent to '{target}': {command!r}"


def _capture_pane(session_name: str, window_name: str, lines: int = 200) -> str:
    target = f"{session_name}:{window_name}"
    out, rc = _run([
        "tmux", "capture-pane",
        "-p",
        "-S", str(-lines),
        "-t", target,
    ])
    if rc != 0:
        return ""
    return out


def _wait_for_pattern(
    session_name: str,
    window_name: str,
    pattern: str,
    timeout: int,
    poll_interval: int,
) -> str:
    target = f"{session_name}:{window_name}"
    deadline = time.monotonic() + timeout
    compiled = re.compile(pattern)

    while time.monotonic() < deadline:
        out = _capture_pane(session_name, window_name)
        if compiled.search(out):
            return f"matched: pattern {pattern!r} found in pane '{target}'."
        time.sleep(poll_interval)

    return f"timeout: pattern {pattern!r} not found in '{target}' after {timeout}s."


# ---------------------------------------------------------------------------
# Public function tools
# ---------------------------------------------------------------------------

@function_tool
def tmux_list_sessions() -> str:
    """List all active tmux sessions with their window counts.

    Returns:
        str: Formatted list of sessions, or a message if none exist.
    """
    out, rc = _run(["tmux", "list-sessions", "-F", "#{session_name}: #{session_windows} windows"])
    if rc != 0:
        return "No active tmux sessions."
    return out or "No active tmux sessions."


@function_tool
def tmux_create_session(session_name: str, window_name: str = "main") -> str:
    """Create a new detached tmux session.

    If the session already exists this is a no-op.

    Args:
        session_name: Unique name for the session (e.g. 'work').
        window_name: Name for the initial window (default: 'main').

    Returns:
        str: Success or error message.
    """
    _, rc = _run(["tmux", "has-session", "-t", session_name])
    if rc == 0:
        return f"Session '{session_name}' already exists."

    _, rc = _run(["tmux", "new-session", "-d", "-s", session_name, "-n", window_name])
    if rc != 0:
        return f"Error: failed to create tmux session '{session_name}'."
    return f"Created tmux session '{session_name}' with window '{window_name}'."


@function_tool
def tmux_send_keys(session_name: str, window_name: str, command: str) -> str:
    """Send a command string to a tmux pane and press Enter.

    Args:
        session_name: Name of the target tmux session.
        window_name: Name of the target window inside the session.
        command: Shell command to send (Enter is appended automatically).

    Returns:
        str: Success or error message.
    """
    return _send_keys(session_name, window_name, command)


@function_tool
def tmux_capture_pane(session_name: str, window_name: str, lines: int = 100) -> str:
    """Capture and return the visible text of a tmux pane.

    Args:
        session_name: Name of the tmux session.
        window_name: Name of the window to capture.
        lines: Number of history lines to include (default 100).

    Returns:
        str: The captured pane content, or an error message.
    """
    target = f"{session_name}:{window_name}"
    out = _capture_pane(session_name, window_name, lines=lines)
    if out == "" and not out:
        return f"Error: could not capture pane '{target}' (may not exist)."
    return out or "(pane is empty)"


@function_tool
def tmux_wait_for_pattern(
    session_name: str,
    window_name: str,
    pattern: str,
    timeout: int = 120,
    poll_interval: int = 3,
) -> str:
    """Poll a tmux pane until its output matches a regex pattern or timeout expires.

    Useful for waiting until an salloc prompt appears or a script finishes.

    Args:
        session_name: Name of the tmux session.
        window_name: Name of the window to monitor.
        pattern: Python regex pattern to match against captured pane output.
        timeout: Maximum seconds to wait (default 120).
        poll_interval: Seconds between polls (default 3).

    Returns:
        str: Message starting with 'matched' if found, or 'timeout' if not.
    """
    return _wait_for_pattern(session_name, window_name, pattern, timeout, poll_interval)


@function_tool
def tmux_kill_session(session_name: str) -> str:
    """Kill a tmux session and all its windows/processes.

    Args:
        session_name: Name of the session to kill.

    Returns:
        str: Success or error message.
    """
    _, rc = _run(["tmux", "kill-session", "-t", session_name])
    if rc != 0:
        return f"Error: could not kill session '{session_name}' (may not exist)."
    return f"Killed tmux session '{session_name}'."


@function_tool
def request_slurm_interactive(
    session_name: str,
    window_name: str,
    nodes: int = 1,
    time_limit: str = "01:00:00",
    qos: str = "interactive",
    constraint: str = "cpu",
    account: str = "",
    extra_args: str = "",
) -> str:
    """Request an interactive SLURM allocation in an existing tmux pane.

    Sends an `salloc` command to the specified pane and waits until a shell
    prompt appears, indicating the allocation is ready.  The session/window must
    already exist — call tmux_create_session first.

    Args:
        session_name: Name of the existing tmux session.
        window_name: Name of the window to use.
        nodes: Number of nodes to request (default 1).
        time_limit: Wall-clock limit in HH:MM:SS (default '01:00:00').
        qos: SLURM QOS (default 'interactive').
        constraint: Node constraint, e.g. 'cpu' or 'gpu' (default 'cpu').
        account: SLURM account/project to charge (optional).
        extra_args: Additional salloc flags (optional, e.g. '--ntasks=32').

    Returns:
        str: 'ready' message if the interactive shell appeared, or error/timeout.
    """
    cmd_parts = [
        "salloc",
        f"--nodes={nodes}",
        f"--time={time_limit}",
        f"--qos={qos}",
        f"--constraint={constraint}",
    ]
    if account:
        cmd_parts.append(f"--account={account}")
    if extra_args:
        cmd_parts.append(extra_args)

    salloc_cmd = " ".join(cmd_parts)
    send_result = _send_keys(session_name, window_name, salloc_cmd)
    if "Error" in send_result:
        return send_result

    # salloc prints "salloc: Granted job allocation <id>" then drops to a shell prompt.
    # Match common prompt characters: $, #, >, ❯ at the end of a line.
    wait_result = _wait_for_pattern(
        session_name,
        window_name,
        pattern=r"[$#>❯]\s*$",
        timeout=300,
        poll_interval=5,
    )
    if wait_result.startswith("matched"):
        return f"Interactive SLURM allocation ready in '{session_name}:{window_name}'."
    return f"Warning: timed out waiting for shell prompt. Status: {wait_result}"
