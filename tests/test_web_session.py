"""Tests for per-chat web session state and slash commands."""

from __future__ import annotations

from typing import Any

import pytest

from agents import Agent
from hepagent.agents.common import AgentContext
from hepagent.web.bridge import ApprovalDecision
from hepagent.web.session import WebSessionState, create_web_session_id


class FakeBridge:
    """Inert bridge; session state never drives human-in-the-loop itself."""

    async def on_command_proposed(self, cmd: str, cwd: str, thought: str) -> None: ...

    async def request_approval(self, cmd: str, cwd: str, mode: str) -> ApprovalDecision:
        return ApprovalDecision(approved=True)

    async def on_command_result(self, cmd: str, result: dict[str, Any]) -> None: ...

    async def ask_user(self, prompt: str, thought: str) -> str:
        return ""


def _make_state(**overrides: Any) -> WebSessionState:
    built: list[tuple[str, str, str | None]] = []

    def agent_factory(name: str, platform: str, model: str | None) -> Agent:
        built.append((name, platform, model))
        return Agent(name=f"{name}-agent", instructions="be useful", model="fake-model", tools=[])

    kwargs: dict[str, Any] = {
        "agent_name": "scientist",
        "available_agents": {"scientist": "Skilled Agent", "shell": "Shell agent"},
        "context": AgentContext(agent_name="scientist"),
        "bridge": FakeBridge(),
        "max_turns": 40,
        "session_id": "web-test",
        "agent_factory": agent_factory,
    }
    kwargs.update(overrides)
    state = WebSessionState(**kwargs)
    state.model.platform = "cborg"
    state.model.name = "lbl/gemma-4"
    state.built = built  # type: ignore[attr-defined]
    return state


def test_create_web_session_id_is_prefixed_and_unique():
    first, second = create_web_session_id(), create_web_session_id()
    assert first.startswith("web-")
    assert first != second


def test_build_agent_wraps_tools_and_attaches_cost_hooks():
    from hepagent.agents.bash import execute_bash_command_with_confirmation

    def agent_factory(name: str, platform: str, model: str | None) -> Agent:
        return Agent(
            name=name,
            instructions="x",
            model="fake",
            tools=[execute_bash_command_with_confirmation],
        )

    state = _make_state(agent_factory=agent_factory)
    agent = state.build_agent()

    assert state.current_agent is agent
    assert agent.hooks is not None
    assert agent.tools[0] is not execute_bash_command_with_confirmation


async def test_plain_prompt_is_not_treated_as_a_command():
    state = _make_state()
    outcome = await state.handle_command("run the nyx simulation")
    assert outcome.handled is False


async def test_help_lists_commands_and_modes():
    state = _make_state()
    outcome = await state.handle_command("/help")
    assert outcome.handled is True
    assert "/mode" in outcome.message
    assert "yolo" in outcome.message


async def test_status_reports_current_settings():
    state = _make_state()
    outcome = await state.handle_command("/status")
    assert "scientist" in outcome.message
    assert "cborg" in outcome.message


async def test_switch_agent_updates_context_and_rebuilds():
    state = _make_state()
    state.build_agent()
    outcome = await state.handle_command("/agent shell")

    assert outcome.handled is True
    assert outcome.settings_changed is True
    assert state.agent_name == "shell"
    assert state.context.agent_name == "shell"
    assert state.built[-1][0] == "shell"  # type: ignore[attr-defined]


async def test_switch_agent_rejects_unknown_name():
    state = _make_state()
    outcome = await state.handle_command("/agent nope")
    assert "Unknown agent" in outcome.message
    assert state.agent_name == "scientist"


@pytest.mark.parametrize("mode", ["confirm", "yolo", "human"])
async def test_mode_switch_is_shared_with_the_tool_wrapper(mode: str):
    state = _make_state()
    outcome = await state.handle_command(f"/mode {mode}")

    assert outcome.handled is True
    assert state.config.mode == mode
    # The wrapper holds the same config object, so approval semantics follow.
    assert state.tool_wrapper._config.mode == mode


async def test_mode_switch_rejects_unknown_mode():
    state = _make_state()
    outcome = await state.handle_command("/mode reckless")
    assert "Usage" in outcome.message
    assert state.config.mode == "confirm"


async def test_max_turns_is_increase_only():
    state = _make_state()
    assert (await state.handle_command("/max-turn 10")).message.startswith(
        "Max turns can only be increased"
    )
    assert state.max_turns == 40

    outcome = await state.handle_command("/max-turns 80")
    assert state.max_turns == 80
    assert outcome.settings_changed is True


async def test_max_turns_rejects_non_integer():
    state = _make_state()
    outcome = await state.handle_command("/max-turn soon")
    assert "not an integer" in outcome.message


async def test_clear_rotates_the_session_and_resets_cost():
    created: list[str] = []

    def session_factory(session_id: str) -> str:
        created.append(session_id)
        return session_id

    state = _make_state(session_factory=session_factory)
    state.input_items = [{"role": "user", "content": "hi"}]
    state.model.cost = 1.5

    outcome = await state.handle_command("/clear")

    assert outcome.handled is True
    assert state.input_items == []
    assert state.model.cost == 0.0
    assert state.session_id != "web-test"
    assert state.session_id.startswith("web-test-")
    assert created == [state.session_id]


async def test_unknown_command_is_reported_not_forwarded():
    state = _make_state()
    outcome = await state.handle_command("/frobnicate")
    assert outcome.handled is True
    assert "Unknown command" in outcome.message


async def test_models_command_surfaces_listing_errors(monkeypatch):
    def boom(platform: str) -> tuple[str, ...]:
        raise RuntimeError("no credentials")

    monkeypatch.setattr("hepagent.web.session.list_available_models", boom)
    state = _make_state()
    outcome = await state.handle_command("/models")

    assert outcome.handled is True
    assert "Could not list models" in outcome.message


async def test_model_switch_validates_against_the_platform(monkeypatch):
    monkeypatch.setattr(
        "hepagent.web.session.list_available_models", lambda platform: ("a-model", "b-model")
    )
    state = _make_state()

    assert "Unknown model" in (await state.handle_command("/model zzz")).message
    assert state.model.name == "lbl/gemma-4"

    outcome = await state.handle_command("/model b-model")
    assert outcome.settings_changed is True
    assert state.model.name == "b-model"
