"""Chainlit frontend for hepagent.

Launched by ``hepagent web``, which points Chainlit at this module. It is the
only module in :mod:`hepagent.web` that imports Chainlit; the session state,
tool wrapping and turn loop live in sibling modules and are testable without it.
"""

from __future__ import annotations

import json
import os
from typing import Any

import chainlit as cl
from chainlit.input_widget import Select, Slider

from hepagent.agents.common import AgentContext
from hepagent.helpers import bootstrap_hepagent_home
from hepagent.model_providers import get_model_provider_settings, get_supported_model_providers
from hepagent.web.bridge import ApprovalDecision
from hepagent.web.session import APPROVAL_MODES, WebSessionState, create_web_session_id
from hepagent.web.turn import run_turn

# Humans take their time; the Chainlit defaults (60-90s) are far too short for a
# command approval on an HPC workflow.
ASK_TIMEOUT_SECONDS = 60 * 60
TOOL_OUTPUT_PREVIEW = 4000

_STATE_KEY = "hepagent_state"


def _env(name: str, default: str) -> str:
    value = os.environ.get(name)
    return value if value else default


def _state() -> WebSessionState:
    state = cl.user_session.get(_STATE_KEY)
    if state is None:
        raise RuntimeError("hepagent web session is not initialised")
    return state


def _truncate(text: str, limit: int = TOOL_OUTPUT_PREVIEW) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n... [truncated: omitted {len(text) - limit} chars]"


def _as_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, indent=2, ensure_ascii=False)
    except Exception:  # noqa: BLE001 - fall back to repr for exotic objects
        return str(value)


class ChainlitBridge:
    """Human-in-the-loop over the Chainlit websocket."""

    async def on_command_proposed(self, cmd: str, cwd: str, thought: str) -> None:
        body = []
        if thought:
            body.append(f"**Why:** {thought}")
        if cwd:
            body.append(f"**Working directory:** `{cwd}`")
        body.append(f"```bash\n{cmd}\n```")
        await cl.Message(content="\n\n".join(body), author="bash").send()

    async def request_approval(self, cmd: str, cwd: str, mode: str) -> ApprovalDecision:
        del cwd
        actions = [
            cl.Action(name="approve", payload={"value": "approve"}, label="✅ Approve"),
            cl.Action(name="reject", payload={"value": "reject"}, label="✋ Reject"),
        ]
        response = await cl.AskActionMessage(
            content=f"Run this command? (mode: **{mode}**)",
            actions=actions,
            timeout=ASK_TIMEOUT_SECONDS,
            raise_on_timeout=False,
        ).send()

        if not response:
            return ApprovalDecision(approved=False, reason="Approval timed out")
        if (response.get("payload") or {}).get("value") == "approve":
            return ApprovalDecision(approved=True)

        reason_message = await cl.AskUserMessage(
            content="Why are you rejecting it? (this is sent back to the agent)",
            timeout=ASK_TIMEOUT_SECONDS,
            raise_on_timeout=False,
        ).send()
        reason = (reason_message or {}).get("output", "") if reason_message else ""
        return ApprovalDecision(approved=False, reason=reason)

    async def on_command_result(self, cmd: str, result: dict[str, Any]) -> None:
        del cmd
        output = _truncate(_as_text(result.get("output", "")))
        returncode = result.get("returncode", 0)
        async with cl.Step(name=f"bash (exit {returncode})", type="tool") as step:
            step.output = f"```\n{output}\n```"

    async def ask_user(self, prompt: str, thought: str) -> str:
        if thought:
            await cl.Message(content=f"**Thinking:** {thought}", author="agent").send()
        answer = await cl.AskUserMessage(
            content=prompt,
            timeout=ASK_TIMEOUT_SECONDS,
            raise_on_timeout=False,
        ).send()
        if not answer:
            return ""
        return answer.get("output", "")


class ChainlitTurnUI:
    """Render a streaming turn into the Chainlit transcript."""

    def __init__(self, state: WebSessionState):
        self._state = state
        self._message: cl.Message | None = None

    async def _ensure_message(self) -> cl.Message:
        if self._message is None:
            self._message = cl.Message(content="", author="agent")
            await self._message.send()
        return self._message

    async def on_text_delta(self, delta: str) -> None:
        message = await self._ensure_message()
        await message.stream_token(delta)

    async def on_text_done(self, text: str) -> None:
        if self._message is None:
            await cl.Message(content=text, author="agent").send()
        else:
            await self._message.update()
        self._message = None

    async def on_tool_call(self, name: str, arguments: str) -> None:
        # The bash and ask-user tools render their own richer UI.
        if "execute_bash_command" in name or "ask_user_for_info" in name:
            return
        async with cl.Step(name=name, type="tool") as step:
            step.input = _truncate(_as_text(arguments))

    async def on_tool_output(self, name: str, output: Any) -> None:
        if "execute_bash_command" in name or "ask_user_for_info" in name:
            return
        async with cl.Step(name=f"{name} → result", type="tool") as step:
            step.output = _truncate(_as_text(output))

    async def on_agent_updated(self, agent_name: str) -> None:
        await cl.Message(content=f"_Agent switched to **{agent_name}**._").send()

    async def on_notice(self, message: str, *, level: str = "info") -> None:
        icon = {"error": "🛑", "warning": "⚠️"}.get(level, "ℹ️")
        await cl.Message(content=f"{icon} {message}", author="system").send()

    async def run_text_bash_proposal(self, cmd: str, thought: str) -> dict[str, Any]:
        return await self._state.tool_wrapper.run_bash(cmd=cmd, cwd="", thought=thought)


def _settings_widgets(state: WebSessionState) -> list[Any]:
    agents = list(state.available_agents)
    platforms = list(get_supported_model_providers())
    return [
        Select(
            id="agent",
            label="Agent",
            values=agents,
            initial_index=agents.index(state.agent_name) if state.agent_name in agents else 0,
        ),
        Select(
            id="platform",
            label="Model platform",
            values=platforms,
            initial_index=(
                platforms.index(state.model.platform) if state.model.platform in platforms else 0
            ),
        ),
        Select(
            id="mode",
            label="Approval mode",
            values=list(APPROVAL_MODES),
            initial_index=APPROVAL_MODES.index(state.config.mode),
        ),
        Slider(id="max_turns", label="Max turns", initial=state.max_turns, min=5, max=200, step=5),
    ]


@cl.on_chat_start
async def on_chat_start() -> None:
    """Build a fresh agent runtime for this browser chat."""
    from hepagent.main import build_runtime, create_app_agent, create_chat_session

    bootstrap_hepagent_home()

    agent_name = _env("HEPAGENT_WEB_AGENT", "scientist")
    model_spec = os.environ.get("HEPAGENT_WEB_MODEL") or None
    max_turns = int(_env("HEPAGENT_WEB_MAX_TURNS", "40"))
    mode = _env("HEPAGENT_WEB_MODE", "confirm")
    session_id = _env("HEPAGENT_WEB_CHAT", "") or create_web_session_id()

    runtime = build_runtime(agent_name=agent_name, model=model_spec, chat=session_id)
    context: AgentContext = runtime["context"]

    def agent_factory(name: str, platform: str, model: str | None):
        return create_app_agent(name, model_provider=platform, model_name=model)

    from hepagent.main import get_available_agents

    state = WebSessionState(
        agent_name=agent_name,
        available_agents=get_available_agents(),
        context=context,
        bridge=ChainlitBridge(),
        max_turns=max_turns,
        session=runtime["session"],
        session_id=session_id,
        session_base_id=session_id,
        session_factory=create_chat_session,
        agent_factory=agent_factory,
    )
    state.config.mode = mode if mode in APPROVAL_MODES else "confirm"
    state.model.platform = runtime["model_provider"]
    state.model.name = runtime["display_model"]
    state.build_agent()
    cl.user_session.set(_STATE_KEY, state)

    await cl.ChatSettings(_settings_widgets(state)).send()
    await cl.Message(
        content=(
            f"### hepagent\n{state.status_line()}\n\n"
            f"Session `{state.session_id}` — resume it later with "
            f"`hepagent repl --chat {state.session_id}`.\n\n"
            "Type `/help` for slash commands, or use the ⚙ panel to change settings."
        ),
        author="system",
    ).send()


@cl.on_settings_update
async def on_settings_update(settings: dict[str, Any]) -> None:
    """Apply changes made in the ⚙ settings panel."""
    state = _state()
    rebuild = False

    agent_name = settings.get("agent")
    if agent_name and agent_name != state.agent_name:
        state.agent_name = agent_name
        state.context.agent_name = agent_name
        rebuild = True

    platform = settings.get("platform")
    if platform and platform != state.model.platform:
        state.model.platform = platform
        state.model.name = get_model_provider_settings(platform).default_model
        rebuild = True

    mode = settings.get("mode")
    if mode in APPROVAL_MODES:
        state.config.mode = mode

    max_turns = settings.get("max_turns")
    if max_turns:
        state.max_turns = int(max_turns)

    if rebuild:
        state.build_agent()
    await cl.Message(content=state.status_line(), author="system").send()


@cl.on_message
async def on_message(message: cl.Message) -> None:
    """Handle one user prompt or slash command."""
    state = _state()
    text = (message.content or "").strip()
    if not text:
        return

    outcome = await state.handle_command(text)
    if outcome.handled:
        await cl.Message(content=outcome.message, author="system").send()
        if outcome.settings_changed:
            await cl.ChatSettings(_settings_widgets(state)).send()
        return

    ui = ChainlitTurnUI(state)
    result = await run_turn(
        agent=state.current_agent,
        user_input=text,
        context=state.context,
        max_turns=state.max_turns,
        session=state.session,
        ui=ui,
        input_items=state.input_items,
    )
    if result.result is not None:
        state.input_items = result.input_items
        if result.last_agent is not None:
            state.current_agent = result.last_agent
