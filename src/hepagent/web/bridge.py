"""Transport-agnostic protocols connecting the agent run to a web frontend.

The terminal frontends couple human-in-the-loop directly to the console
(``input()`` in :mod:`hepagent.agents.bash`, ``prompt_async`` in
:mod:`hepagent.agents.cli_repl`). The web UI instead talks to these protocols so
the approval flow and the streaming loop can be tested without a browser.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class ApprovalDecision:
    """Outcome of asking a human to approve a bash command."""

    approved: bool
    reason: str = ""


class WebBridge(Protocol):
    """Human-in-the-loop channel used by the wrapped tools."""

    async def on_command_proposed(self, cmd: str, cwd: str, thought: str) -> None:
        """Show a proposed bash command before approval is requested."""
        ...

    async def request_approval(self, cmd: str, cwd: str, mode: str) -> ApprovalDecision:
        """Ask the human to approve a bash command."""
        ...

    async def on_command_result(self, cmd: str, result: dict[str, Any]) -> None:
        """Show the result of an executed bash command."""
        ...

    async def ask_user(self, prompt: str, thought: str) -> str:
        """Ask the human a free-form question and return their answer."""
        ...


class TurnUI(Protocol):
    """Rendering callbacks driven by :func:`hepagent.web.turn.run_turn`."""

    async def on_text_delta(self, delta: str) -> None:
        """Receive one streamed token of assistant text."""
        ...

    async def on_text_done(self, text: str) -> None:
        """Signal that a streamed assistant message is complete."""
        ...

    async def on_tool_call(self, name: str, arguments: str) -> None:
        """Announce a tool call emitted by the model."""
        ...

    async def on_tool_output(self, name: str, output: Any) -> None:
        """Announce the output of a completed tool call."""
        ...

    async def on_agent_updated(self, agent_name: str) -> None:
        """Announce that the active agent changed mid-run."""
        ...

    async def on_notice(self, message: str, *, level: str = "info") -> None:
        """Surface a recovery/error notice to the user."""
        ...

    async def run_text_bash_proposal(self, cmd: str, thought: str) -> dict[str, Any]:
        """Run a bash block the model emitted as text instead of as a tool call."""
        ...
