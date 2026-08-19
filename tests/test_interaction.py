"""Tests for the pluggable human-interaction backend.

The point of `hepagent.interaction` is that three terminal-bound call sites can
be answered from somewhere else — today, the plan page — without any of them
learning what a browser is. These tests pin both halves: with a backend bound the
question goes there, and with none bound nothing about the CLI changed.
"""

from __future__ import annotations

import threading

from hepagent.interaction import Approval, bound_backend, current_backend, set_backend


class Recorder:
    """A backend that answers instantly and remembers what it was asked."""

    def __init__(self, approved: bool = True, answer: str = "42"):
        self.approvals: list[tuple[str, str, str]] = []
        self.questions: list[tuple[str, str, tuple[str, ...]]] = []
        self._approved = approved
        self._answer = answer

    def approve(self, cmd: str, cwd: str = "", thought: str = "") -> Approval:
        self.approvals.append((cmd, cwd, thought))
        return Approval(approved=self._approved, reason="" if self._approved else "no")

    def ask(self, prompt: str, thought: str = "", choices=()) -> str:
        self.questions.append((prompt, thought, tuple(choices)))
        return self._answer


class _NotYolo:
    """Pins the approval path under test: yolo short-circuits before the backend."""

    yolo_mode = False
    output_word_limit = 1000


def _invoke(tool, **kwargs):
    """Call a `@function_tool`-wrapped function the way the SDK does."""
    import asyncio
    import json

    return asyncio.run(tool.on_invoke_tool(None, json.dumps(kwargs)))


def test_no_backend_is_bound_by_default():
    assert current_backend() is None


def test_bound_backend_restores_what_was_there_before():
    outer, inner = Recorder(), Recorder()
    set_backend(outer)
    try:
        with bound_backend(inner):
            assert current_backend() is inner
        assert current_backend() is outer
    finally:
        set_backend(None)


def test_a_backend_is_bound_per_thread():
    """A run gets its own thread precisely so the server's is left alone."""
    seen: dict[str, object] = {}
    backend = Recorder()

    def worker():
        with bound_backend(backend):
            seen["inside"] = current_backend()

    thread = threading.Thread(target=worker)
    thread.start()
    thread.join()

    assert seen["inside"] is backend
    assert current_backend() is None


def test_the_bash_tool_asks_the_backend_instead_of_stdin(monkeypatch):
    from hepagent.agents.bash import execute_bash_command_with_confirmation

    monkeypatch.setattr("hepagent.agents.bash.env_config", _NotYolo())
    backend = Recorder(approved=True)
    with bound_backend(backend):
        result = _invoke(
            execute_bash_command_with_confirmation, cmd="echo hepagent", thought="check"
        )

    assert "hepagent" in result["output"]
    assert backend.approvals == [("echo hepagent", "", "check")]


def test_a_rejected_command_does_not_run(monkeypatch):
    from hepagent.agents.bash import execute_bash_command_with_confirmation

    monkeypatch.setattr("hepagent.agents.bash.env_config", _NotYolo())
    backend = Recorder(approved=False)
    with bound_backend(backend):
        result = _invoke(
            execute_bash_command_with_confirmation, cmd="echo should-not-appear", thought=""
        )

    assert "should-not-appear" not in result["output"]
    assert result["returncode"] == 1


def test_ask_user_for_info_goes_to_the_backend():
    from hepagent.tools.common import ask_user_for_info

    backend = Recorder(answer="  /data/zbb  ")
    with bound_backend(backend):
        answer = _invoke(ask_user_for_info, prompt="Where is the data?", thought="need a path")

    assert answer == "/data/zbb"
    assert backend.questions[0][0] == "Where is the data?"


def test_the_human_gate_asks_the_backend_with_its_two_choices():
    """The gate is a raw `input()` on the CLI; on the page it is two buttons."""
    import asyncio

    from hepagent.agents.jfc.orchestrator import _human_gate
    from hepagent.plan.templates import DEFAULT_TEMPLATE, instantiate

    plan = instantiate(
        DEFAULT_TEMPLATE, analysis_name="zbb", analysis_type="measurement", physics_prompt="p"
    )
    node = plan.nodes[0]

    backend = Recorder(answer="APPROVE")
    with bound_backend(backend):
        approved = asyncio.run(_human_gate(node, None))
    assert approved is True
    assert backend.questions[0][2] == ("APPROVE", "ITERATE")

    rejecting = Recorder(answer="ITERATE")
    with bound_backend(rejecting):
        assert asyncio.run(_human_gate(node, None)) is False
