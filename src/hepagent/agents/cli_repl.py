"""Prompt-toolkit based CLI REPL for hepagent."""

from __future__ import annotations

import asyncio
import json
import shlex
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from openai.types.responses.response_text_delta_event import ResponseTextDeltaEvent
from prompt_toolkit import PromptSession
from prompt_toolkit.completion import NestedCompleter
from prompt_toolkit.history import InMemoryHistory
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from agents import Agent, AgentHooks, Runner, function_tool
from agents.exceptions import MaxTurnsExceeded
from agents.items import TResponseInputItem
from agents.result import RunResultBase
from agents.run_context import RunContextWrapper
from agents.stream_events import (
    AgentUpdatedStreamEvent,
    RawResponsesStreamEvent,
    RunItemStreamEvent,
)
from hepagent.agents.bash import TOOL_CANCEL_MESSAGE, execute_bash_command
from hepagent.token_costs import calculate_cost

OUTPUT_TRUNCATE_LENGTH = 1000

DEAD_AIR_RETRY_PROMPT = (
    "You produced no assistant text in the previous turn. "
    "Continue the current task now with exactly one next command or a final summary. "
    "Do not stay silent."
)

HELP_TEXT = """\
[bold]Slash commands[/bold]
/help - Show this help text
/quit - Exit the REPL
/clear - Reset the current REPL transcript
/agents - List available agents
/agent <name> - Switch the active agent
/mode <confirm|yolo|human> - Change command approval mode

[bold]Modes[/bold]
confirm: Press Enter to approve a command, or type a reason to reject it
yolo: Auto-approve command execution
human: Type "y" to allow each command explicitly
"""


@dataclass
class ReplConfig:
    """Runtime configuration for the CLI REPL."""

    mode: str = "confirm"


@dataclass
class ReplModelState:
    """Minimal model state tracked in the REPL."""

    cost: float = 0.0
    name: str = ""


@dataclass(frozen=True)
class SlashCommand:
    """Parsed slash command."""

    name: str
    args: tuple[str, ...]


@dataclass
class CommandResult:
    """Outcome of a slash command."""

    handled: bool
    should_exit: bool = False


def _format_tool_output(output: Any) -> str:
    """Bound tool-output display so the transcript stays readable."""
    try:
        text = json.dumps(output, ensure_ascii=False)
    except Exception:
        text = str(output)
    if len(text) <= OUTPUT_TRUNCATE_LENGTH:
        return text
    omitted = len(text) - OUTPUT_TRUNCATE_LENGTH
    return text[:OUTPUT_TRUNCATE_LENGTH] + f"... [tool output truncated: omitted {omitted} chars]"


def _tool_output_contains_finalize_signal(output: Any) -> bool:
    """Detect explicit finalize markers emitted by guardrails in tool output."""
    try:
        text = json.dumps(output, ensure_ascii=False)
    except Exception:
        text = str(output)
    return "FINALIZE_NOW" in text


def parse_slash_command(text: str) -> SlashCommand | None:
    """Parse a slash command line into a structured command."""
    stripped = text.strip()
    if not stripped.startswith("/"):
        return None
    body = stripped[1:].strip()
    if not body:
        return SlashCommand(name="", args=())
    try:
        parts = shlex.split(body)
    except ValueError:
        parts = body.split()
    if not parts:
        return SlashCommand(name="", args=())
    return SlashCommand(name=parts[0].lower(), args=tuple(parts[1:]))


class ReplAgentHooks(AgentHooks):
    """Track model cost during REPL runs."""

    def __init__(self, model_state: ReplModelState):
        super().__init__()
        self._model_state = model_state

    async def on_llm_end(self, context: RunContextWrapper, agent: Agent, response: Any) -> None:
        if hasattr(context, "usage") and context.usage:
            self._model_state.cost += calculate_cost(context.usage, self._model_state.name)


class ReplToolWrapper:
    """Wrap existing tools with REPL-specific interaction behavior."""

    def wrap_tools(self, tools: list[Any], repl: CliRepl) -> list[Any]:
        wrapped = []
        for tool in tools:
            name = getattr(tool, "name", "")
            if "execute_bash_command" in name:
                wrapped.append(self._create_bash_tool(repl))
            elif "ask_user_for_info" in name:
                wrapped.append(self._create_ask_user_tool(repl))
            else:
                wrapped.append(tool)
        return wrapped

    def _create_bash_tool(self, repl: CliRepl):
        @function_tool
        def execute_bash_command_with_repl_confirmation(
            cmd: str, cwd: str = "", thought: str = ""
        ) -> dict:
            repl.render_command_proposal(cmd=cmd, cwd=cwd, thought=thought)
            if not repl.approve_command(cmd=cmd, cwd=cwd):
                reason = repl.last_rejection_reason or "No reason provided"
                return {"output": TOOL_CANCEL_MESSAGE.format(reason=reason), "returncode": 1}

            result = execute_bash_command(cmd, cwd=cwd)
            repl.render_tool_result("bash", result)
            return result

        return execute_bash_command_with_repl_confirmation

    def _create_ask_user_tool(self, repl: CliRepl):
        @function_tool
        def ask_user_for_info(prompt: str, thought: str = "") -> str:
            if thought:
                repl.console.print(
                    Panel(
                        thought,
                        title="assistant thought",
                        border_style="yellow",
                    )
                )
            repl.console.print(
                Panel(
                    prompt,
                    title="assistant needs input",
                    border_style="magenta",
                )
            )
            return repl.prompt_inline(f"{prompt}\n> ").strip()

        return ask_user_for_info


class CliRepl:
    """Claude Code-inspired interactive REPL for hepagent."""

    def __init__(
        self,
        *,
        agent_name: str,
        agent_factory: Callable[[str], Agent],
        available_agents: dict[str, str],
        context: Any,
        max_turns: int,
        yolo: bool = False,
        session: Any | None = None,
        session_factory: Callable[[str], Any] | None = None,
        chat_base_id: str | None = None,
        prompt_session: PromptSession[str] | None = None,
        console: Console | None = None,
    ):
        self.agent_name = agent_name
        self.agent_factory = agent_factory
        self.available_agents = available_agents
        self.context = context
        self.max_turns = max_turns
        self.console = console or Console(highlight=True)
        self.prompt_session = prompt_session or PromptSession(history=InMemoryHistory())
        self.session_factory = session_factory
        self.chat_base_id = chat_base_id
        self.session = session
        self.input_items: list[TResponseInputItem] = []
        self.config = ReplConfig(mode="yolo" if yolo else "confirm")
        self.last_rejection_reason: str | None = None
        self.model = ReplModelState()
        self._tool_wrapper = ReplToolWrapper()
        self.current_agent = self._build_wrapped_agent(agent_name)

    def _build_wrapped_agent(self, agent_name: str) -> Agent:
        base_agent = self.agent_factory(agent_name)
        model_name = (
            base_agent.model
            if isinstance(base_agent.model, str)
            else getattr(base_agent.model, "model", "")
        )
        self.model.name = model_name
        wrapped_tools = self._tool_wrapper.wrap_tools(base_agent.tools, self)
        return Agent(
            name=base_agent.name,
            instructions=base_agent.instructions,
            model=base_agent.model,
            tools=wrapped_tools,
            hooks=ReplAgentHooks(self.model),
        )

    def run(self) -> None:
        """Start the REPL loop."""
        self.render_startup()
        while True:
            try:
                user_input = self.prompt_inline(self._prompt_message())
            except (EOFError, KeyboardInterrupt):
                self.console.print()
                break

            if not user_input.strip():
                continue

            cmd = parse_slash_command(user_input)
            if cmd is not None:
                result = self.handle_command(cmd)
                if result.should_exit:
                    break
                if result.handled:
                    continue

            asyncio.run(self._run_turn(user_input))

    def prompt_inline(self, message: str) -> str:
        """Prompt the user for a single line of input."""
        return self.prompt_session.prompt(
            message,
            completer=self._build_completer(),
            complete_while_typing=False,
            bottom_toolbar=self._bottom_toolbar,
        )

    def handle_command(self, command: SlashCommand) -> CommandResult:
        """Execute a slash command locally."""
        if command.name == "quit":
            self.console.print("[bold]Bye.[/bold]")
            return CommandResult(handled=True, should_exit=True)
        if command.name == "help":
            self.render_help()
            return CommandResult(handled=True)
        if command.name == "clear":
            self.clear_session_state()
            return CommandResult(handled=True)
        if command.name == "agents":
            self.render_agents()
            return CommandResult(handled=True)
        if command.name == "agent":
            self.switch_agent(command.args[0] if command.args else "")
            return CommandResult(handled=True)
        if command.name == "mode":
            self.set_mode(command.args[0] if command.args else "")
            return CommandResult(handled=True)

        self.console.print(
            Panel(
                f"Unknown command: /{command.name}\nTry /help for available commands.",
                title="command error",
                border_style="red",
            )
        )
        return CommandResult(handled=True)

    async def _run_turn(self, user_input: str) -> None:
        turn_input = list(self.input_items)
        turn_input.append({"role": "user", "content": user_input})
        self.console.print(
            Panel(
                user_input,
                title=f"{self.agent_name} prompt",
                border_style="blue",
            )
        )

        result: RunResultBase | None = None
        dead_air_retry_used = False
        while True:
            try:
                result = Runner.run_streamed(
                    self.current_agent,
                    input=turn_input,
                    context=self.context,
                    max_turns=self.max_turns,
                    session=self.session,
                )
                saw_text = False
                saw_finalize_signal = False
                deltas: list[str] = []

                self.console.print(
                    f"[bold green]{self.agent_name}[/bold green] "
                    f"[dim](mode={self.config.mode}, cost=${self.model.cost:.6f})[/dim]"
                )
                async for event in result.stream_events():
                    if isinstance(event, RawResponsesStreamEvent):
                        if isinstance(event.data, ResponseTextDeltaEvent):
                            saw_text = True
                            deltas.append(event.data.delta)
                            self.console.file.write(event.data.delta)
                            self.console.file.flush()
                    elif isinstance(event, RunItemStreamEvent):
                        if event.item.type == "tool_call_output_item":
                            if _tool_output_contains_finalize_signal(event.item.output):
                                saw_finalize_signal = True
                    elif isinstance(event, AgentUpdatedStreamEvent):
                        self.console.print(
                            f"\n[dim]Agent updated: {event.new_agent.name}[/dim]"
                        )

                full_text = "".join(deltas)
                if saw_text:
                    self.console.file.write("\n")
                    self.console.file.flush()
                    self.render_assistant_output(full_text)

                if saw_finalize_signal and not saw_text:
                    followup_input = result.to_input_list()
                    followup_input.append(
                        {
                            "role": "user",
                            "content": (
                                "FINALIZE_NOW received. Provide the final summary only; "
                                "do not call tools."
                            ),
                        }
                    )
                    forced = await Runner.run(
                        result.last_agent,
                        input=followup_input,
                        context=self.context,
                        max_turns=self.max_turns,
                        session=self.session,
                    )
                    if forced.final_output is not None:
                        self.render_assistant_output(str(forced.final_output))
                    result = forced
                    break

                if not saw_text:
                    if not dead_air_retry_used:
                        self.console.print(
                            "[yellow][recovery: no assistant output; "
                            "retrying once automatically][/yellow]"
                        )
                        dead_air_retry_used = True
                        turn_input = result.to_input_list()
                        turn_input.append(
                            {
                                "role": "user",
                                "content": DEAD_AIR_RETRY_PROMPT,
                            }
                        )
                        self.current_agent = result.last_agent
                        continue
                    self.console.print(
                        "[yellow][no assistant text output after retry; "
                        "continuing to next prompt][/yellow]"
                    )
                break
            except MaxTurnsExceeded:
                self.console.print(
                    f"[red][max turns: {self.max_turns}. Narrow the task or raise the limit.][/red]"
                )
                return
            except Exception as exc:
                self.console.print(
                    Panel(
                        str(exc),
                        title="run error",
                        border_style="red",
                    )
                )
                return

        if result is None:
            return
        self.current_agent = result.last_agent
        self.input_items = result.to_input_list()

    def approve_command(self, *, cmd: str, cwd: str = "") -> bool:
        """Check whether the current bash command is approved."""
        del cmd, cwd
        self.last_rejection_reason = None
        if self.config.mode == "yolo":
            self.console.print("[green]Auto-approved (YOLO mode).[/green]")
            return True
        if self.config.mode == "confirm":
            response = self.prompt_inline(
                "Approve command? Press Enter to allow, or type a reason to reject: "
            )
            if response.strip():
                self.last_rejection_reason = response.strip()
                self.console.print(f"[red]Rejected:[/red] {self.last_rejection_reason}")
                return False
            self.console.print("[green]Approved.[/green]")
            return True

        response = self.prompt_inline(
            "Approve command? Type 'y' to allow, or enter a reason to reject: "
        )
        if response.strip().lower() == "y":
            self.console.print("[green]Approved.[/green]")
            return True
        self.last_rejection_reason = response.strip() or "Rejected in human mode"
        self.console.print(f"[red]Rejected:[/red] {self.last_rejection_reason}")
        return False

    def render_startup(self) -> None:
        """Render the initial REPL banner."""
        self.console.print(
            Panel(
                (
                    f"Agent: [bold]{self.agent_name}[/bold]\n"
                    f"Mode: [bold]{self.config.mode}[/bold]\n"
                    "Use /help for slash commands. Enter a task to start."
                ),
                title="hepagent repl",
                border_style="cyan",
            )
        )

    def render_help(self) -> None:
        """Render help text."""
        self.console.print(Panel(Markdown(HELP_TEXT), title="help", border_style="cyan"))

    def render_agents(self) -> None:
        """Render available agent names."""
        table = Table(title="Available agents")
        table.add_column("Agent", style="bold")
        table.add_column("Description")
        for name, description in sorted(self.available_agents.items()):
            table.add_row(name, description)
        self.console.print(table)

    def render_command_proposal(self, *, cmd: str, cwd: str = "", thought: str = "") -> None:
        """Render a proposed bash command before execution."""
        if thought:
            self.console.print(Panel(thought, title="assistant thought", border_style="yellow"))
        body = Text()
        body.append("Command:\n", style="bold")
        body.append(cmd)
        body.append("\n\nWorking directory:\n", style="bold")
        body.append(cwd or "current")
        self.console.print(Panel(body, title="bash tool", border_style="magenta"))

    def render_tool_result(self, tool_name: str, result: Any) -> None:
        """Render tool output in a dedicated block."""
        self.console.print(
            Panel(
                _format_tool_output(result),
                title=f"{tool_name} result",
                border_style="green" if result.get("returncode", 1) == 0 else "red",
            )
        )

    def render_assistant_output(self, text: str) -> None:
        """Render finalized assistant output as markdown when it is structured."""
        stripped = text.strip()
        if not stripped:
            return
        if "```" in stripped or "\n" in stripped:
            self.console.print(Panel(Markdown(stripped), title="assistant", border_style="cyan"))

    def clear_session_state(self) -> None:
        """Clear in-memory transcript state and rotate the persistent chat session."""
        self.input_items = []
        self.model.cost = 0.0
        self.console.clear()
        self.render_startup()
        if self.chat_base_id and self.session_factory is not None:
            new_id = f"{self.chat_base_id}-{uuid.uuid4().hex[:8]}"
            self.session = self.session_factory(new_id)
            self.console.print(f"[dim]Started a fresh chat session: {new_id}[/dim]")
        else:
            self.console.print("[dim]Cleared current REPL session state.[/dim]")

    def set_mode(self, mode: str) -> None:
        """Set the command approval mode."""
        normalized = mode.lower()
        if normalized not in {"confirm", "yolo", "human"}:
            self.console.print(
                Panel(
                    "Usage: /mode <confirm|yolo|human>",
                    title="mode error",
                    border_style="red",
                )
            )
            return
        self.config.mode = normalized
        self.console.print(f"[green]Mode set to {normalized}.[/green]")

    def switch_agent(self, agent_name: str) -> None:
        """Switch the active agent for future turns."""
        normalized = agent_name.strip().lower()
        if not normalized:
            self.console.print(
                Panel("Usage: /agent <agent_name>", title="agent error", border_style="red")
            )
            return
        if normalized not in self.available_agents:
            self.console.print(
                Panel(
                    f"Unknown agent: {normalized}\nTry /agents to see available names.",
                    title="agent error",
                    border_style="red",
                )
            )
            return
        self.agent_name = normalized
        if hasattr(self.context, "agent_name"):
            self.context.agent_name = normalized
        self.current_agent = self._build_wrapped_agent(normalized)
        self.console.print(f"[green]Switched active agent to {normalized}.[/green]")

    def _build_completer(self) -> NestedCompleter:
        return NestedCompleter.from_nested_dict(
            {
                "/help": None,
                "/quit": None,
                "/clear": None,
                "/agents": None,
                "/agent": dict.fromkeys(self.available_agents, None),
                "/mode": {"confirm": None, "yolo": None, "human": None},
            }
        )

    def _bottom_toolbar(self) -> str:
        return (
            f" agent={self.agent_name} | mode={self.config.mode} | "
            f"cost=${self.model.cost:.6f} | /help "
        )

    def _prompt_message(self) -> str:
        return f"{self.agent_name} [{self.config.mode}] > "
