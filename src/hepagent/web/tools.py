"""Tool wrapping for the web UI.

Two problems are solved here.

1. **Human-in-the-loop.** The stock ``execute_bash_command_with_confirmation``
   and ``ask_user_for_info`` tools block on ``input()``. As the REPL frontend
   already does, they are swapped for async equivalents that talk to a
   :class:`~hepagent.web.bridge.WebBridge`.

2. **Blocking the event loop.** The Agents SDK invokes *synchronous* function
   tools inline on the running event loop (``result = the_func(...)`` in
   ``agents/tool.py``). That is harmless for a terminal frontend that owns its
   own loop, but on a web server it stalls the ASGI loop and the websocket dies.
   Tools known to block for a long time are therefore re-dispatched onto a
   worker thread.
"""

from __future__ import annotations

import asyncio
import dataclasses
from collections.abc import Awaitable, Callable
from typing import Any

from agents import function_tool
from agents.tool import FunctionTool
from hepagent.agents.bash import TOOL_CANCEL_MESSAGE, execute_bash_command
from hepagent.web.bridge import WebBridge

#: Tools that can block for seconds-to-hours. Synchronous tools not listed here
#: return fast enough that running them inline on the event loop is fine. Add a
#: tool here when it sleeps, polls, shells out for a long time, or does heavy
#: CPU work.
LONG_BLOCKING_TOOL_NAMES = frozenset(
    {
        "wait_for_slurm_job_completion",
        "request_slurm_interactive",
        "tmux_wait_for_pattern",
        "create_transfer_function",
        "web_search",
    }
)


def _run_coroutine_on_new_loop(
    factory: Callable[..., Awaitable[Any]], *args: Any, **kwargs: Any
) -> Any:
    """Build and drive a coroutine to completion on a private event loop.

    The coroutine object is created inside the worker thread so it is never
    bound to the server's loop.
    """
    return asyncio.run(factory(*args, **kwargs))


def offload_blocking_tool(tool: FunctionTool) -> FunctionTool:
    """Return a copy of ``tool`` whose body runs on a worker thread."""
    original_invoke = tool.on_invoke_tool

    async def on_invoke_tool(ctx: Any, input: str) -> Any:
        return await asyncio.to_thread(_run_coroutine_on_new_loop, original_invoke, ctx, input)

    return dataclasses.replace(tool, on_invoke_tool=on_invoke_tool)


class WebToolWrapper:
    """Swap terminal-bound tools for browser-driven equivalents.

    Mirrors :class:`hepagent.agents.cli_repl.ReplToolWrapper`, including its
    name-substring tool matching and its return contracts.
    """

    def __init__(self, bridge: WebBridge, config: Any):
        self._bridge = bridge
        self._config = config

    def wrap_tools(self, tools: list[Any]) -> list[Any]:
        """Return ``tools`` with interactive and blocking tools replaced."""
        wrapped: list[Any] = []
        for tool in tools:
            name = getattr(tool, "name", "")
            if "execute_bash_command" in name:
                wrapped.append(self._create_bash_tool())
            elif "ask_user_for_info" in name:
                wrapped.append(self._create_ask_user_tool())
            elif name in LONG_BLOCKING_TOOL_NAMES and isinstance(tool, FunctionTool):
                wrapped.append(offload_blocking_tool(tool))
            else:
                wrapped.append(tool)
        return wrapped

    async def approve(self, cmd: str, cwd: str = "") -> tuple[bool, str]:
        """Apply the active approval mode, asking the human when required.

        Returns ``(approved, rejection_reason)`` using the same mode semantics
        as the REPL: ``yolo`` auto-approves, ``confirm`` treats an empty answer
        as approval, and ``human`` requires an explicit approval.
        """
        mode = getattr(self._config, "mode", "confirm")
        if mode == "yolo":
            return True, ""
        try:
            decision = await self._bridge.request_approval(cmd=cmd, cwd=cwd, mode=mode)
        except Exception as exc:  # noqa: BLE001 - an approval gate must fail closed
            return False, f"Approval could not be collected ({exc}); command not run."
        if decision.approved:
            return True, ""
        default_reason = "Rejected in human mode" if mode == "human" else "No reason provided"
        return False, decision.reason.strip() or default_reason

    async def run_bash(self, cmd: str, cwd: str = "", thought: str = "") -> dict[str, Any]:
        """Propose, approve, and execute a bash command."""
        await self._bridge.on_command_proposed(cmd=cmd, cwd=cwd, thought=thought)
        approved, reason = await self.approve(cmd=cmd, cwd=cwd)
        if not approved:
            result = {"output": TOOL_CANCEL_MESSAGE.format(reason=reason), "returncode": 1}
            await self._bridge.on_command_result(cmd, result)
            return result

        result = await asyncio.to_thread(execute_bash_command, cmd, cwd=cwd)
        await self._bridge.on_command_result(cmd, result)
        return result

    def _create_bash_tool(self):
        wrapper = self

        @function_tool
        async def execute_bash_command_with_confirmation(
            cmd: str, cwd: str = "", thought: str = ""
        ) -> dict:
            """Execute a bash command after the user approves it.

            Args:
                cmd: The bash command to run.
                cwd: Optional working directory for the command.
                thought: Why this command is being run.
            """
            return await wrapper.run_bash(cmd=cmd, cwd=cwd, thought=thought)

        return execute_bash_command_with_confirmation

    def _create_ask_user_tool(self):
        bridge = self._bridge

        @function_tool
        async def ask_user_for_info(prompt: str, thought: str = "") -> str:
            """Ask the user a question and return their answer.

            Args:
                prompt: The question to ask the user.
                thought: Why the information is needed.
            """
            answer = await bridge.ask_user(prompt=prompt, thought=thought)
            return answer.strip()

        return ask_user_for_info
