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
from hepagent.model_providers import (
    get_model_provider_settings,
    get_supported_model_providers,
    list_available_models,
)
from hepagent.token_costs import calculate_cost

OUTPUT_TRUNCATE_LENGTH = 1000

DEAD_AIR_RETRY_PROMPT = (
    "You produced no assistant text in the previous turn. "
    "Continue the current task now with exactly one next command or a final summary. "
    "Do not stay silent."
)


@dataclass
class ReplConfig:
    """Runtime configuration for the CLI REPL."""

    mode: str = "confirm"


@dataclass
class ReplModelState:
    """Minimal model state tracked in the REPL."""

    cost: float = 0.0
    platform: str = ""
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


def _build_help_text() -> Text:
    """Build richly formatted help text for the REPL panel."""
    text = Text()
    text.append("Slash Commands\n", style="bold")
    for command, description in (
        ("/help", "Show this help text"),
        ("/quit", "Exit the REPL"),
        ("/clear", "Reset the current REPL transcript"),
        ("/agents", "List available agents"),
        ("/agent <name>", "Switch the active agent"),
        ("/platforms", "List supported model platforms"),
        ("/platform <name>", "Switch the active model platform"),
        (
            "/models [platform]",
            "List available models for the current or given platform",
        ),
        ("/model <name>", "Switch the active model on the current platform"),
        ("/mode <confirm|yolo|human>", "Change command approval mode"),
    ):
        text.append("  ")
        text.append(command, style="bold cyan")
        text.append("  ")
        text.append(description)
        text.append("\n")

    text.append("\nModes\n", style="bold")
    for mode, description in (
        ("confirm", "Press Enter to approve a command, or type a reason to reject it"),
        ("yolo", "Auto-approve command execution"),
        ("human", 'Type "y" to allow each command explicitly'),
    ):
        text.append("  ")
        text.append(mode, style="bold green")
        text.append("  ")
        text.append(description)
        text.append("\n")
    return text


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
        async def execute_bash_command_with_repl_confirmation(
            cmd: str, cwd: str = "", thought: str = ""
        ) -> dict:
            repl.console.file.write("\n")
            repl.console.file.flush()
            repl.render_command_proposal(cmd=cmd, cwd=cwd, thought=thought)
            if not await repl.approve_command_async(cmd=cmd, cwd=cwd):
                reason = repl.last_rejection_reason or "No reason provided"
                return {"output": TOOL_CANCEL_MESSAGE.format(reason=reason), "returncode": 1}

            result = execute_bash_command(cmd, cwd=cwd)
            repl.render_tool_result("bash", result)
            return result

        return execute_bash_command_with_repl_confirmation

    def _create_ask_user_tool(self, repl: CliRepl):
        @function_tool
        async def ask_user_for_info(prompt: str, thought: str = "") -> str:
            repl.console.file.write("\n")
            repl.console.file.flush()
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
            result = await repl.prompt_session.prompt_async(
                f"{prompt}\n> ",
                completer=repl._build_completer(),
                complete_while_typing=False,
                bottom_toolbar=repl._bottom_toolbar,
            )
            return result.strip()

        return ask_user_for_info


class CliRepl:
    """Claude Code-inspired interactive REPL for hepagent."""

    def __init__(
        self,
        *,
        agent_name: str,
        agent_factory: Callable[[str, str, str | None], Agent],
        available_agents: dict[str, str],
        context: Any,
        max_turns: int,
        yolo: bool = False,
        session: Any | None = None,
        session_factory: Callable[[str], Any] | None = None,
        chat_base_id: str | None = None,
        prompt_session: PromptSession[str] | None = None,
        console: Console | None = None,
        model_platform: str = "",
        model_name: str = "",
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
        self.model = ReplModelState(platform=model_platform, name=model_name)
        self._tool_wrapper = ReplToolWrapper()
        self.current_agent = self._build_wrapped_agent(agent_name)

    def _render_status_panel(self, message: str, *, title: str, border_style: str) -> None:
        """Render a compact status panel for local REPL events."""
        self.console.print(
            Panel.fit(
                Text.from_markup(message),
                title=title,
                border_style=border_style,
            )
        )

    def _build_wrapped_agent(self, agent_name: str) -> Agent:
        base_agent = self.agent_factory(
            agent_name,
            self._current_platform(),
            self.model.name or None,
        )
        model_name = (
            base_agent.model
            if isinstance(base_agent.model, str)
            else getattr(base_agent.model, "model", "")
        )
        if model_name and not self.model.name:
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

    async def prompt_inline_async(self, message: str) -> str:
        """Async variant of prompt_inline for use inside async tools."""
        return await self.prompt_session.prompt_async(
            message,
            completer=self._build_completer(),
            complete_while_typing=False,
            bottom_toolbar=self._bottom_toolbar,
        )

    def handle_command(self, command: SlashCommand) -> CommandResult:
        """Execute a slash command locally."""
        if command.name == "quit":
            self._render_status_panel("Bye.", title="session", border_style="cyan")
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
        if command.name == "platforms":
            self.render_platforms()
            return CommandResult(handled=True)
        if command.name == "platform":
            self.set_platform(command.args[0] if command.args else "")
            return CommandResult(handled=True)
        if command.name == "models":
            self.render_models(*command.args)
            return CommandResult(handled=True)
        if command.name == "model":
            self.set_model(command.args[0] if command.args else "")
            return CommandResult(handled=True)
        if command.name == "mode":
            self.set_mode(command.args[0] if command.args else "")
            return CommandResult(handled=True)

        self._render_status_panel(
            (
                f"Unknown command: [bold]/{command.name}[/bold]\n"
                "Try [bold]/help[/bold] for available commands."
            ),
            title="command error",
            border_style="red",
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

                self._render_status_panel(
                    (
                        f"[bold]{self.agent_name}[/bold]\n"
                        f"platform={self._current_platform()}  model={self._current_model()}\n"
                        f"mode={self.config.mode}  cost=${self.model.cost:.6f}\n"
                        "Streaming response"
                    ),
                    title="assistant",
                    border_style="cyan",
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
                        self.console.print(f"\n[dim]Agent updated: {event.new_agent.name}[/dim]")

                full_text = "".join(deltas)
                if saw_text:
                    self.console.file.write("\n")
                    self.console.file.flush()
                    self.render_assistant_output(full_text, streamed=True)

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
                        self.render_assistant_output(str(forced.final_output), streamed=False)
                    result = forced
                    break

                if not saw_text:
                    if not dead_air_retry_used:
                        self._render_status_panel(
                            "No assistant output detected. Retrying once automatically.",
                            title="recovery",
                            border_style="yellow",
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
                    self._render_status_panel(
                        "Still no assistant text after retry. Continuing to the next prompt.",
                        title="recovery",
                        border_style="yellow",
                    )
                break
            except MaxTurnsExceeded:
                self._render_status_panel(
                    f"Max turns reached: {self.max_turns}. Narrow the task or raise the limit.",
                    title="run error",
                    border_style="red",
                )
                return
            except Exception as exc:
                self._render_status_panel(str(exc), title="run error", border_style="red")
                return

        if result is None:
            return
        self.current_agent = result.last_agent
        self.input_items = result.to_input_list()

    async def approve_command_async(self, *, cmd: str, cwd: str = "") -> bool:
        """Check whether the current bash command is approved."""
        del cmd, cwd
        self.last_rejection_reason = None
        if self.config.mode == "yolo":
            self._render_status_panel(
                "Auto-approved in [bold]yolo[/bold] mode.",
                title="approval",
                border_style="green",
            )
            return True
        if self.config.mode == "confirm":
            response = await self.prompt_inline_async(
                "Approve command? Press Enter to allow, or type a reason to reject: "
            )
            if response.strip():
                self.last_rejection_reason = response.strip()
                self._render_status_panel(
                    f"Rejected.\nReason: {self.last_rejection_reason}",
                    title="approval",
                    border_style="red",
                )
                return False
            self._render_status_panel(
                "Approved.",
                title="approval",
                border_style="green",
            )
            return True

        response = await self.prompt_inline_async(
            "Approve command? Type 'y' to allow, or enter a reason to reject: "
        )
        if response.strip().lower() == "y":
            self._render_status_panel(
                "Approved.",
                title="approval",
                border_style="green",
            )
            return True
        self.last_rejection_reason = response.strip() or "Rejected in human mode"
        self._render_status_panel(
            f"Rejected.\nReason: {self.last_rejection_reason}",
            title="approval",
            border_style="red",
        )
        return False

    def render_startup(self) -> None:
        """Render the initial REPL banner."""
        self.console.print(
            Panel(
                (
                    f"Agent: [bold]{self.agent_name}[/bold]\n"
                    f"Mode: [bold]{self.config.mode}[/bold]\n"
                    f"Platform: [bold]{self._current_platform()}[/bold]\n"
                    f"Model: [bold]{self._current_model()}[/bold]\n"
                    "Use /help for slash commands. Enter a task to start."
                ),
                title="hepagent repl",
                border_style="cyan",
            )
        )

    def render_help(self) -> None:
        """Render help text."""
        self.console.print(Panel(_build_help_text(), title="help", border_style="cyan"))

    def render_agents(self) -> None:
        """Render available agent names."""
        table = Table(title="Available agents")
        table.add_column("Agent", style="bold")
        table.add_column("Description")
        for name, description in sorted(self.available_agents.items()):
            label = Text(name, style="bold cyan" if name == self.agent_name else "")
            table.add_row(label, description)
        self.console.print(table)

    def render_platforms(self) -> None:
        """Render configured model platforms."""
        table = Table(title="Supported model platforms")
        table.add_column("Platform", style="bold")
        for platform in sorted(get_supported_model_providers()):
            label = Text(
                platform,
                style="bold cyan" if platform == self._current_platform() else "",
            )
            table.add_row(label)
        self.console.print(table)

    def render_models(self, *args: str) -> None:
        """Render available models for the current or selected platform."""
        if len(args) > 1:
            self._render_status_panel(
                "Usage: [bold]/models [platform][/bold]",
                title="models error",
                border_style="red",
            )
            return

        platform = args[0].strip().lower() if args else self._current_platform()
        supported = set(get_supported_model_providers())
        if platform not in supported:
            available = ", ".join(sorted(supported))
            self._render_status_panel(
                (f"Unknown platform: [bold]{platform}[/bold]\nSupported platforms: {available}"),
                title="models error",
                border_style="red",
            )
            return

        try:
            settings = get_model_provider_settings(platform)
            models = list_available_models(platform, settings=settings)
        except ValueError as exc:
            self._render_status_panel(str(exc), title="models error", border_style="red")
            return
        except Exception as exc:
            self._render_status_panel(str(exc), title="models error", border_style="red")
            return

        table = Table(title=f"Available models for {platform}")
        table.add_column("Model", style="bold")
        current_model = self._current_model()
        for name in models:
            is_current_model = platform == self._current_platform() and name == current_model
            label = Text(name, style="bold cyan" if is_current_model else "")
            table.add_row(label)
        self.console.print(table)

    def set_platform(self, platform: str) -> None:
        """Set the active model platform and reset to its default model."""
        normalized = platform.strip().lower()
        if not normalized:
            self._render_status_panel(
                "Usage: [bold]/platform <name>[/bold]",
                title="platform error",
                border_style="red",
            )
            return

        supported = set(get_supported_model_providers())
        if normalized not in supported:
            available = ", ".join(sorted(supported))
            self._render_status_panel(
                (f"Unknown platform: [bold]{normalized}[/bold]\nSupported platforms: {available}"),
                title="platform error",
                border_style="red",
            )
            return

        try:
            settings = get_model_provider_settings(normalized)
        except ValueError as exc:
            self._render_status_panel(str(exc), title="platform error", border_style="red")
            return
        except Exception as exc:
            self._render_status_panel(str(exc), title="platform error", border_style="red")
            return

        self.model.platform = normalized
        self.model.name = settings.default_model
        self.current_agent = self._build_wrapped_agent(self.agent_name)
        self._render_status_panel(
            (
                f"Platform set to [bold]{normalized}[/bold].\n"
                f"Model set to default [bold]{self._current_model()}[/bold]."
            ),
            title="platform",
            border_style="green",
        )

    def set_model(self, model_name: str) -> None:
        """Set the active model on the current platform."""
        normalized = model_name.strip()
        if not normalized:
            self._render_status_panel(
                "Usage: [bold]/model <name>[/bold]",
                title="model error",
                border_style="red",
            )
            return

        platform = self._current_platform()
        try:
            settings = get_model_provider_settings(platform)
            available_models = list_available_models(platform, settings=settings)
        except ValueError as exc:
            self._render_status_panel(str(exc), title="model error", border_style="red")
            return
        except Exception as exc:
            self._render_status_panel(str(exc), title="model error", border_style="red")
            return

        if normalized not in available_models:
            preview = ", ".join(available_models[:8])
            suffix = "..." if len(available_models) > 8 else ""
            self._render_status_panel(
                (
                    f"Model [bold]{normalized}[/bold] is not on [bold]{platform}[/bold].\n"
                    f"Try [bold]/models[/bold] to inspect the full list. "
                    f"Known models: {preview}{suffix}"
                ),
                title="model error",
                border_style="red",
            )
            return

        self.model.name = normalized
        self.current_agent = self._build_wrapped_agent(self.agent_name)
        self._render_status_panel(
            (f"Model set to [bold]{normalized}[/bold]\nPlatform: [bold]{platform}[/bold]"),
            title="model",
            border_style="green",
        )

    def render_command_proposal(self, *, cmd: str, cwd: str = "", thought: str = "") -> None:
        """Render a proposed bash command before execution."""
        if thought:
            self.console.print(Panel(thought, title="assistant thought", border_style="yellow"))
        body = Text()
        body.append("Command\n", style="bold")
        body.append(cmd, style="cyan")
        body.append("\n\nWorking directory\n", style="bold")
        body.append(cwd or "current", style="green")
        body.append("\n\nApproval mode\n", style="bold")
        body.append(self.config.mode)
        self.console.print(Panel(body, title="bash tool", border_style="magenta"))

    def render_tool_result(self, tool_name: str, result: Any) -> None:
        """Render tool output in a dedicated block."""
        border_style = "green" if result.get("returncode", 1) == 0 else "red"
        self.console.print(
            Panel(
                _format_tool_output(result),
                title=f"{tool_name} result",
                subtitle=f"exit={result.get('returncode', 'n/a')}",
                border_style=border_style,
            )
        )

    def render_assistant_output(self, text: str, *, streamed: bool) -> None:
        """Render assistant output when a final block is still needed."""
        stripped = text.strip()
        if not stripped:
            return
        if streamed:
            return
        if "```" in stripped or "\n" in stripped:
            self.console.print(Panel(Markdown(stripped), title="assistant", border_style="cyan"))
            return
        self.console.print(Panel(stripped, title="assistant", border_style="cyan"))

    def clear_session_state(self) -> None:
        """Clear in-memory transcript state and rotate the persistent chat session."""
        self.input_items = []
        self.model.cost = 0.0
        self.console.clear()
        self.render_startup()
        if self.chat_base_id and self.session_factory is not None:
            new_id = f"{self.chat_base_id}-{uuid.uuid4().hex[:8]}"
            self.session = self.session_factory(new_id)
            self._render_status_panel(
                f"Started a fresh chat session: [bold]{new_id}[/bold]",
                title="session",
                border_style="cyan",
            )
        else:
            self._render_status_panel(
                "Cleared the current REPL session state.",
                title="session",
                border_style="cyan",
            )

    def set_mode(self, mode: str) -> None:
        """Set the command approval mode."""
        normalized = mode.lower()
        if normalized not in {"confirm", "yolo", "human"}:
            self._render_status_panel(
                "Usage: [bold]/mode <confirm|yolo|human>[/bold]",
                title="mode error",
                border_style="red",
            )
            return
        self.config.mode = normalized
        self._render_status_panel(
            f"Mode set to [bold]{normalized}[/bold].",
            title="mode",
            border_style="green",
        )

    def switch_agent(self, agent_name: str) -> None:
        """Switch the active agent for future turns."""
        normalized = agent_name.strip().lower()
        if not normalized:
            self._render_status_panel(
                "Usage: [bold]/agent <agent_name>[/bold]",
                title="agent error",
                border_style="red",
            )
            return
        if normalized not in self.available_agents:
            self._render_status_panel(
                (
                    f"Unknown agent: [bold]{normalized}[/bold]\n"
                    "Try [bold]/agents[/bold] to see available names."
                ),
                title="agent error",
                border_style="red",
            )
            return
        self.agent_name = normalized
        if hasattr(self.context, "agent_name"):
            self.context.agent_name = normalized
        self.current_agent = self._build_wrapped_agent(normalized)
        self._render_status_panel(
            f"Switched active agent to [bold]{normalized}[/bold].",
            title="agent",
            border_style="green",
        )

    def _build_completer(self) -> NestedCompleter:
        return NestedCompleter.from_nested_dict(
            {
                "/help": None,
                "/quit": None,
                "/clear": None,
                "/agents": None,
                "/agent": dict.fromkeys(self.available_agents, None),
                "/platforms": None,
                "/platform": dict.fromkeys(sorted(get_supported_model_providers()), None),
                "/models": dict.fromkeys(sorted(get_supported_model_providers()), None),
                "/model": None,
                "/mode": {"confirm": None, "yolo": None, "human": None},
            }
        )

    def _bottom_toolbar(self) -> str:
        return (
            f" agent={self.agent_name} | platform={self._current_platform()} | "
            f"model={self._current_model()} | mode={self.config.mode} | "
            f"cost=${self.model.cost:.6f} | /help "
        )

    def _prompt_message(self) -> str:
        return f"{self.agent_name} [{self.config.mode}] > "

    def _current_platform(self) -> str:
        return self.model.platform or "unknown"

    def _current_model(self) -> str:
        return self.model.name or "default"
