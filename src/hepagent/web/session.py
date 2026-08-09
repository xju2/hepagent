"""Per-chat runtime state for the web UI.

One :class:`WebSessionState` exists per browser chat. Everything mutable lives
on it — agent, model, approval mode, cost, conversation history — so nothing
leaks between concurrent chats the way the process-wide ``HEPAGENT_YOLO`` env
var would.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from typing import Any

from agents import Agent
from agents.items import TResponseInputItem

# ReplConfig/ReplModelState/ReplAgentHooks carry no prompt_toolkit or Rich state;
# they are the frontend-agnostic pieces of the REPL and are reused as-is.
from hepagent.agents.cli_repl import ReplAgentHooks, ReplConfig, ReplModelState
from hepagent.model_providers import (
    get_model_provider_settings,
    get_supported_model_providers,
    list_available_models,
)
from hepagent.web.bridge import WebBridge
from hepagent.web.tools import WebToolWrapper

APPROVAL_MODES = ("confirm", "yolo", "human")


def create_web_session_id() -> str:
    """Create a short, random web session id."""
    return f"web-{uuid.uuid4().hex[:12]}"


@dataclass
class CommandOutcome:
    """Outcome of a slash command issued from the browser."""

    handled: bool
    message: str = ""
    settings_changed: bool = False


@dataclass
class WebSessionState:
    """Mutable state backing a single browser chat."""

    agent_name: str
    available_agents: dict[str, str]
    context: Any
    bridge: WebBridge
    max_turns: int
    session: Any | None = None
    session_id: str | None = None
    session_base_id: str | None = None
    session_factory: Any | None = None
    config: ReplConfig = field(default_factory=ReplConfig)
    model: ReplModelState = field(default_factory=ReplModelState)
    input_items: list[TResponseInputItem] = field(default_factory=list)
    agent_factory: Any = None
    current_agent: Agent | None = None
    _tool_wrapper: WebToolWrapper | None = None

    def __post_init__(self) -> None:
        self.session_base_id = self.session_base_id or self.session_id
        self._tool_wrapper = WebToolWrapper(self.bridge, self.config)

    @property
    def tool_wrapper(self) -> WebToolWrapper:
        """The tool wrapper bound to this chat's bridge and approval mode."""
        assert self._tool_wrapper is not None
        return self._tool_wrapper

    def current_platform(self) -> str:
        """Return the active model platform."""
        return self.model.platform

    def build_agent(self, agent_name: str | None = None) -> Agent:
        """Build the wrapped agent for the current agent/platform/model."""
        name = agent_name or self.agent_name
        base_agent = self.agent_factory(name, self.model.platform, self.model.name or None)
        model_name = (
            base_agent.model
            if isinstance(base_agent.model, str)
            else getattr(base_agent.model, "model", "")
        )
        if model_name and not self.model.name:
            self.model.name = model_name
        agent = Agent(
            name=base_agent.name,
            instructions=base_agent.instructions,
            model=base_agent.model,
            tools=self.tool_wrapper.wrap_tools(base_agent.tools),
            hooks=ReplAgentHooks(self.model),
        )
        self.current_agent = agent
        return agent

    def status_line(self) -> str:
        """Compact status summary shown in the browser."""
        skill = getattr(self.context, "active_skill", None) or "none"
        return (
            f"agent=`{self.agent_name}` · platform=`{self.model.platform}` · "
            f"model=`{self.model.name}` · mode=`{self.config.mode}` · "
            f"max-turns=`{self.max_turns}` · skill=`{skill}` · "
            f"cost=`${self.model.cost:.6f}`"
        )

    def clear(self) -> None:
        """Rotate to a fresh conversation, mirroring the REPL's ``/clear``."""
        self.input_items = []
        self.model.cost = 0.0
        if self.session_factory is not None and self.session_base_id:
            self.session_id = f"{self.session_base_id}-{uuid.uuid4().hex[:8]}"
            self.session = self.session_factory(self.session_id)

    async def handle_command(self, text: str) -> CommandOutcome:
        """Handle a slash command. Returns ``handled=False`` for plain prompts."""
        from hepagent.agents.cli_repl import parse_slash_command

        command = parse_slash_command(text)
        if command is None:
            return CommandOutcome(handled=False)

        name, args = command.name, command.args
        if name == "help":
            return CommandOutcome(handled=True, message=_help_text())
        if name == "status":
            return CommandOutcome(handled=True, message=self.status_line())
        if name == "clear":
            self.clear()
            return CommandOutcome(
                handled=True,
                message=f"Started a new conversation (session `{self.session_id}`).",
                settings_changed=True,
            )
        if name == "agents":
            rows = "\n".join(f"- `{key}` — {desc}" for key, desc in self.available_agents.items())
            return CommandOutcome(handled=True, message=f"**Available agents**\n{rows}")
        if name == "agent":
            return self._switch_agent(args)
        if name == "platforms":
            rows = "\n".join(f"- `{p}`" for p in get_supported_model_providers())
            return CommandOutcome(handled=True, message=f"**Supported platforms**\n{rows}")
        if name == "platform":
            return self._switch_platform(args)
        if name == "models":
            return await self._list_models(args)
        if name == "model":
            return await self._switch_model(args)
        if name == "mode":
            return self._switch_mode(args)
        if name in {"max-turn", "max-turns"}:
            return self._set_max_turns(args)
        return CommandOutcome(handled=True, message=f"Unknown command `/{name}`. Try `/help`.")

    def _switch_agent(self, args: tuple[str, ...]) -> CommandOutcome:
        if not args:
            return CommandOutcome(handled=True, message="Usage: `/agent <name>`")
        target = args[0].lower()
        if target not in self.available_agents:
            return CommandOutcome(handled=True, message=f"Unknown agent `{target}`. Try `/agents`.")
        self.agent_name = target
        self.context.agent_name = target
        self.build_agent()
        return CommandOutcome(
            handled=True, message=f"Switched to agent `{target}`.", settings_changed=True
        )

    def _switch_platform(self, args: tuple[str, ...]) -> CommandOutcome:
        if not args:
            return CommandOutcome(handled=True, message="Usage: `/platform <name>`")
        target = args[0].lower()
        if target not in get_supported_model_providers():
            return CommandOutcome(
                handled=True, message=f"Unknown platform `{target}`. Try `/platforms`."
            )
        self.model.platform = target
        self.model.name = get_model_provider_settings(target).default_model
        self.build_agent()
        return CommandOutcome(
            handled=True,
            message=f"Switched to platform `{target}` (model `{self.model.name}`).",
            settings_changed=True,
        )

    async def _list_models(self, args: tuple[str, ...]) -> CommandOutcome:
        platform = args[0].lower() if args else self.model.platform
        try:
            models = await asyncio.to_thread(list_available_models, platform)
        except Exception as exc:  # noqa: BLE001 - network/credential failures are user-facing
            return CommandOutcome(handled=True, message=f"Could not list models: {exc}")
        rows = "\n".join(f"- `{m}`" for m in models) or "_no models reported_"
        return CommandOutcome(handled=True, message=f"**Models on `{platform}`**\n{rows}")

    async def _switch_model(self, args: tuple[str, ...]) -> CommandOutcome:
        if not args:
            return CommandOutcome(handled=True, message="Usage: `/model <name>`")
        target = args[0]
        try:
            models = await asyncio.to_thread(list_available_models, self.model.platform)
        except Exception:  # noqa: BLE001 - fall back to accepting the name unvalidated
            models = ()
        if models and target not in models:
            return CommandOutcome(
                handled=True,
                message=f"Unknown model `{target}` on `{self.model.platform}`. Try `/models`.",
            )
        self.model.name = target
        self.build_agent()
        return CommandOutcome(
            handled=True, message=f"Switched to model `{target}`.", settings_changed=True
        )

    def _switch_mode(self, args: tuple[str, ...]) -> CommandOutcome:
        if not args or args[0].lower() not in APPROVAL_MODES:
            return CommandOutcome(
                handled=True, message=f"Usage: `/mode <{'|'.join(APPROVAL_MODES)}>`"
            )
        self.config.mode = args[0].lower()
        return CommandOutcome(
            handled=True,
            message=f"Approval mode set to `{self.config.mode}`.",
            settings_changed=True,
        )

    def _set_max_turns(self, args: tuple[str, ...]) -> CommandOutcome:
        if not args:
            return CommandOutcome(handled=True, message="Usage: `/max-turn <turns>`")
        try:
            value = int(args[0])
        except ValueError:
            return CommandOutcome(handled=True, message=f"`{args[0]}` is not an integer.")
        if value <= self.max_turns:
            return CommandOutcome(
                handled=True,
                message=f"Max turns can only be increased (currently {self.max_turns}).",
            )
        self.max_turns = value
        return CommandOutcome(
            handled=True, message=f"Max turns raised to {value}.", settings_changed=True
        )


def _help_text() -> str:
    """Markdown help shown by ``/help``."""
    commands = (
        ("/help", "Show this help"),
        ("/status", "Show the current agent, model, mode and cost"),
        ("/clear", "Start a new conversation"),
        ("/agents", "List available agents"),
        ("/agent <name>", "Switch the active agent"),
        ("/platforms", "List supported model platforms"),
        ("/platform <name>", "Switch the active model platform"),
        ("/models [platform]", "List available models"),
        ("/model <name>", "Switch the active model"),
        ("/mode <confirm|yolo|human>", "Change command approval mode"),
        ("/max-turn <turns>", "Raise the max-turns limit"),
    )
    lines = ["**Slash commands**", "", "| Command | Description |", "| --- | --- |"]
    lines += [f"| `{cmd}` | {desc} |" for cmd, desc in commands]
    lines += [
        "",
        "**Approval modes**",
        "",
        "| Mode | Behaviour |",
        "| --- | --- |",
        "| `confirm` | Approve each command with a button, or reject with a reason |",
        "| `yolo` | Auto-approve every command |",
        "| `human` | Same as confirm, but rejection is the default |",
        "",
        "Settings can also be changed from the ⚙ panel next to the chat input.",
    ]
    return "\n".join(lines)
