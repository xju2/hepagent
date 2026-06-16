"""Tests for tmux tools.

The @function_tool decorator wraps functions into FunctionTool objects for the
agent framework, so tests call the private helper functions directly, which
contain all the logic. Integration behavior (session lifecycle, send+capture)
is also verified via the helpers.

Requires tmux to be installed and a server to be startable.
"""

import time

import pytest

from hepagent.tools.tmux import (
    _capture_pane,
    _run,
    _send_keys,
    _wait_for_pattern,
)

SESSION = "__hepagent_test__"
WINDOW = "test"


def _tmux_available() -> bool:
    _, rc = _run(["tmux", "-V"])
    return rc == 0


pytestmark = pytest.mark.skipif(not _tmux_available(), reason="tmux not available")


@pytest.fixture(autouse=True)
def cleanup_session():
    """Ensure the test session is absent before and after each test."""
    _run(["tmux", "kill-session", "-t", SESSION])
    yield
    _run(["tmux", "kill-session", "-t", SESSION])


def _create_session():
    _, rc = _run(["tmux", "new-session", "-d", "-s", SESSION, "-n", WINDOW])
    assert rc == 0, "Failed to create test tmux session"


# ---------------------------------------------------------------------------
# _run helper
# ---------------------------------------------------------------------------


def test_run_success():
    out, rc = _run(["echo", "hello"])
    assert rc == 0
    assert "hello" in out


def test_run_failure():
    _, rc = _run(["tmux", "has-session", "-t", "nonexistent_session_xyz"])
    assert rc != 0


# ---------------------------------------------------------------------------
# Session lifecycle
# ---------------------------------------------------------------------------


def test_create_session():
    _, rc = _run(["tmux", "new-session", "-d", "-s", SESSION, "-n", WINDOW])
    assert rc == 0
    out, rc2 = _run(["tmux", "list-sessions", "-F", "#{session_name}"])
    assert rc2 == 0
    assert SESSION in out


def test_kill_session():
    _create_session()
    _, rc = _run(["tmux", "kill-session", "-t", SESSION])
    assert rc == 0
    out, _ = _run(["tmux", "list-sessions", "-F", "#{session_name}"])
    assert SESSION not in out


def test_kill_nonexistent_session_returns_error():
    _, rc = _run(["tmux", "kill-session", "-t", "nonexistent_xyz"])
    assert rc != 0


# ---------------------------------------------------------------------------
# _send_keys / _capture_pane
# ---------------------------------------------------------------------------


def test_send_and_capture():
    _create_session()
    _send_keys(SESSION, WINDOW, "echo hepagent_marker")
    time.sleep(0.5)
    output = _capture_pane(SESSION, WINDOW)
    assert "hepagent_marker" in output


def test_send_keys_to_missing_session():
    result = _send_keys("nonexistent_xyz", "win", "echo hi")
    assert "Error" in result


def test_capture_pane_missing_returns_empty():
    out = _capture_pane("nonexistent_xyz", "win")
    assert out == ""


# ---------------------------------------------------------------------------
# _wait_for_pattern
# ---------------------------------------------------------------------------


def test_wait_for_pattern_matches():
    _create_session()
    # Shell prompt is visible after session creation; match common prompt chars ($, #, >, ❯)
    result = _wait_for_pattern(SESSION, WINDOW, r"[$#>❯]", timeout=10, poll_interval=1)
    assert result.startswith("matched")


def test_wait_for_pattern_timeout():
    _create_session()
    result = _wait_for_pattern(
        SESSION, WINDOW, r"THIS_WILL_NEVER_APPEAR_XYZ_12345", timeout=4, poll_interval=1
    )
    assert result.startswith("timeout")


def test_wait_pattern_on_missing_pane_times_out():
    start = time.monotonic()
    result = _wait_for_pattern("nonexistent_xyz", "win", r"\$", timeout=3, poll_interval=1)
    elapsed = time.monotonic() - start
    assert result.startswith("timeout")
    assert elapsed < 6  # must not hang
