"""Tests for the web tool wrapper and its approval semantics."""

from __future__ import annotations

import asyncio
import time
from typing import Any

import pytest

from agents import function_tool
from hepagent.agents.cli_repl import ReplConfig
from hepagent.web.bridge import ApprovalDecision
from hepagent.web.tools import (
    LONG_BLOCKING_TOOL_NAMES,
    WebToolWrapper,
    offload_blocking_tool,
)


class FakeBridge:
    """Records bridge traffic and replays canned approvals/answers."""

    def __init__(self, decision: ApprovalDecision | None = None, answer: str = ""):
        self.decision = decision or ApprovalDecision(approved=True)
        self.answer = answer
        self.proposed: list[tuple[str, str, str]] = []
        self.results: list[tuple[str, dict[str, Any]]] = []
        self.approval_requests: list[tuple[str, str, str]] = []
        self.questions: list[tuple[str, str]] = []

    async def on_command_proposed(self, cmd: str, cwd: str, thought: str) -> None:
        self.proposed.append((cmd, cwd, thought))

    async def request_approval(self, cmd: str, cwd: str, mode: str) -> ApprovalDecision:
        self.approval_requests.append((cmd, cwd, mode))
        return self.decision

    async def on_command_result(self, cmd: str, result: dict[str, Any]) -> None:
        self.results.append((cmd, result))

    async def ask_user(self, prompt: str, thought: str) -> str:
        self.questions.append((prompt, thought))
        return self.answer


def _wrapper(mode: str = "confirm", **bridge_kwargs: Any) -> tuple[WebToolWrapper, FakeBridge]:
    bridge = FakeBridge(**bridge_kwargs)
    return WebToolWrapper(bridge, ReplConfig(mode=mode)), bridge


async def test_yolo_mode_never_asks_for_approval():
    wrapper, bridge = _wrapper(mode="yolo")
    approved, reason = await wrapper.approve("echo hi")
    assert approved is True
    assert reason == ""
    assert bridge.approval_requests == []


async def test_confirm_mode_asks_and_can_approve():
    wrapper, bridge = _wrapper(mode="confirm")
    approved, reason = await wrapper.approve("echo hi", cwd="/tmp")
    assert approved is True
    assert reason == ""
    assert bridge.approval_requests == [("echo hi", "/tmp", "confirm")]


async def test_rejection_reason_is_passed_through():
    wrapper, _ = _wrapper(
        mode="confirm", decision=ApprovalDecision(approved=False, reason="too risky")
    )
    approved, reason = await wrapper.approve("rm -rf /")
    assert approved is False
    assert reason == "too risky"


@pytest.mark.parametrize(
    ("mode", "expected"),
    [("confirm", "No reason provided"), ("human", "Rejected in human mode")],
)
async def test_empty_rejection_reason_gets_mode_specific_default(mode: str, expected: str):
    wrapper, _ = _wrapper(mode=mode, decision=ApprovalDecision(approved=False, reason="  "))
    approved, reason = await wrapper.approve("echo hi")
    assert approved is False
    assert reason == expected


async def test_rejected_command_returns_cancel_contract_and_is_not_run():
    wrapper, bridge = _wrapper(
        mode="confirm", decision=ApprovalDecision(approved=False, reason="nope")
    )
    result = await wrapper.run_bash("touch /tmp/hepagent-should-not-exist", thought="why")

    assert result["returncode"] == 1
    assert "nope" in result["output"]
    assert bridge.proposed == [("touch /tmp/hepagent-should-not-exist", "", "why")]
    assert bridge.results[0][1] == result


async def test_approval_failure_fails_closed():
    """If the approval prompt itself breaks, the command must not run."""

    class BrokenBridge(FakeBridge):
        async def request_approval(self, cmd: str, cwd: str, mode: str) -> ApprovalDecision:
            raise RuntimeError("websocket died")

    wrapper = WebToolWrapper(BrokenBridge(), ReplConfig(mode="confirm"))
    approved, reason = await wrapper.approve("rm -rf /")

    assert approved is False
    assert "websocket died" in reason
    assert "not run" in reason


async def test_approval_failure_does_not_execute_the_command():
    class BrokenBridge(FakeBridge):
        async def request_approval(self, cmd: str, cwd: str, mode: str) -> ApprovalDecision:
            raise RuntimeError("boom")

    bridge = BrokenBridge()
    wrapper = WebToolWrapper(bridge, ReplConfig(mode="confirm"))
    result = await wrapper.run_bash("echo should-not-run")

    assert result["returncode"] == 1
    assert "should-not-run" not in result["output"]


async def test_approved_command_actually_executes():
    wrapper, bridge = _wrapper(mode="yolo")
    result = await wrapper.run_bash("echo hepagent-web")

    assert result["returncode"] == 0
    assert "hepagent-web" in result["output"]
    assert bridge.results[0][0] == "echo hepagent-web"


def test_wrap_tools_replaces_interactive_tools():
    from hepagent.agents.bash import execute_bash_command_with_confirmation
    from hepagent.tools.common import ask_user_for_info, read_file

    wrapper, _ = _wrapper()
    original = [execute_bash_command_with_confirmation, ask_user_for_info, read_file]
    wrapped = wrapper.wrap_tools(original)

    assert wrapped[0] is not original[0]
    assert wrapped[1] is not original[1]
    # A tool with no interactive or blocking behaviour is passed through untouched.
    assert wrapped[2] is original[2]


def test_wrap_tools_offloads_known_blocking_tools():
    from hepagent.tools.common import wait_for_slurm_job_completion

    assert wait_for_slurm_job_completion.name in LONG_BLOCKING_TOOL_NAMES
    wrapper, _ = _wrapper()
    (wrapped,) = wrapper.wrap_tools([wait_for_slurm_job_completion])

    assert wrapped is not wait_for_slurm_job_completion
    assert wrapped.name == wait_for_slurm_job_completion.name
    assert wrapped.params_json_schema == wait_for_slurm_job_completion.params_json_schema


async def test_offloaded_tool_keeps_the_event_loop_responsive():
    """A blocking tool must not stall the loop, or the websocket would drop."""

    @function_tool
    def slow_tool(seconds: float) -> str:
        """Sleep synchronously.

        Args:
            seconds: How long to block for.
        """
        time.sleep(seconds)
        return "done"

    offloaded = offload_blocking_tool(slow_tool)

    ticks = 0

    async def heartbeat() -> None:
        nonlocal ticks
        while True:
            await asyncio.sleep(0.01)
            ticks += 1

    beat = asyncio.create_task(heartbeat())
    try:
        result = await offloaded.on_invoke_tool(None, '{"seconds": 0.3}')
    finally:
        beat.cancel()

    assert result == "done"
    assert ticks > 3, "event loop was blocked while the tool ran"


async def test_ask_user_tool_delegates_to_the_bridge():
    wrapper, bridge = _wrapper(answer="  /pscratch/work  ")
    tool = wrapper._create_ask_user_tool()

    answer = await tool.on_invoke_tool(None, '{"prompt": "Where?", "thought": "need a path"}')

    assert answer == "/pscratch/work"
    assert bridge.questions == [("Where?", "need a path")]
