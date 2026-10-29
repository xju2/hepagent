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
from pathlib import Path
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
    name-substring tool matching and its return contracts, and adds the one tool
    that only makes sense in a browser: `create_analysis`, which turns a
    conversation into a plan and hands the user its editor.
    """

    def __init__(self, bridge: WebBridge, config: Any, model: Any = None):
        self._bridge = bridge
        self._config = config
        self._model = model

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
        # Appended rather than swapped: nothing in the terminal tool sets
        # corresponds to it, because there is no page to send anyone to there.
        if not any(getattr(tool, "name", "") == "create_analysis" for tool in wrapped):
            wrapped.append(self._create_analysis_tool())
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

    async def create_analysis(
        self,
        name: str,
        physics_prompt: str,
        analysis_type: str = "measurement",
        template: str | None = None,
    ) -> str:
        """Propose a plan for a new analysis, scaffold it, and open its editor.

        The architect shapes the template to the physics prompt, the scaffolder
        writes the directory tree and the provenance graph, and the user is sent
        to the plan page — where they edit the graph and, when they are happy
        with it, launch the run.

        Returns a sentence for the agent to relay, or one starting with "Error:".
        """
        from hepagent.agents.jfc.architect import propose_plan
        from hepagent.plan.service import analyses_dir
        from hepagent.plan.templates import DEFAULT_TEMPLATE
        from hepagent.tools.jfc.scaffold import _scaffold_impl
        from hepagent.web.server import plan_editor_url

        slug = name.strip().strip("/")
        if not slug or slug != Path(slug).name or slug.startswith("."):
            return f"Error: {name!r} is not a usable analysis name — use a short slug."
        if analysis_type not in {"measurement", "search"}:
            return f"Error: analysis_type must be 'measurement' or 'search', not {analysis_type!r}."
        root = analyses_dir() / slug
        if root.exists():
            return f"Error: an analysis named {slug!r} already exists at {root}."

        platform = getattr(self._model, "platform", None) or "cborg"
        model_name = getattr(self._model, "name", None) or None
        proposal = await propose_plan(
            physics_prompt=physics_prompt,
            analysis_name=slug,
            analysis_type=analysis_type,
            template=template or DEFAULT_TEMPLATE,
            model_provider=platform,
            model_name=model_name,
        )
        # The proposal is already validated or discarded by the architect, so
        # what arrives here always scaffolds; the notes say which happened.
        written = await _scaffold_impl(
            slug,
            physics_prompt,
            analysis_type,
            str(analyses_dir()),
            proposal.plan.template or DEFAULT_TEMPLATE,
            proposal.plan,
        )
        if written.startswith("Error"):
            return written

        url = plan_editor_url(slug)
        try:
            await self._bridge.open_plan(slug, url)
        except Exception:  # noqa: BLE001 - the analysis exists either way
            pass
        shaping = (
            "the architect adapted the template"
            if proposal.accepted and proposal.rationale
            else "the standard template fits as it is"
        )
        return (
            f"Created analysis '{slug}' ({analysis_type}) at {written} with "
            f"{len(proposal.plan.nodes)} nodes — {shaping}. Its plan editor is open at {url}; "
            "tell the user to review the graph there and press “Approve & run” to start it."
        )

    def _create_analysis_tool(self):
        wrapper = self

        @function_tool
        async def create_analysis(
            name: str,
            physics_prompt: str,
            analysis_type: str = "measurement",
            template: str = "",
        ) -> str:
            """Create a new physics analysis and open its plan editor for the user.

            Call this only once the physics question is specific enough to work
            from: what is being measured or searched for, in what data, and what
            the final result should be. Ask the user for whatever is missing
            first — the prompt you pass here becomes the analysis's brief and
            every node's instructions are written from it.

            Args:
                name: Short slug for the analysis, e.g. "z_bb_xsec".
                physics_prompt: The full physics brief, in the user's terms.
                analysis_type: "measurement" or "search".
                template: Plan template to start from. Leave empty for the default.
            """
            return await wrapper.create_analysis(
                name=name,
                physics_prompt=physics_prompt,
                analysis_type=analysis_type,
                template=template or None,
            )

        return create_analysis

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
