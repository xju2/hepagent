"""Tests for hepagent.agents.skilled module."""

import hepagent.agents.skilled as skilled_module


def _make_stub_agent_cls():
    """Create a stub Agent class that supports Agent[T] subscript syntax."""

    class StubAgent:
        def __init__(self, name, instructions, model, tools, **kwargs):
            self.name = name
            self.instructions = instructions
            self.model = model
            self.tools = list(tools)

        def __class_getitem__(cls, item):
            return cls

    return StubAgent


def test_create_returns_agent(monkeypatch):
    """skilled.create() returns an Agent with the expected tools."""
    StubAgent = _make_stub_agent_cls()
    monkeypatch.setattr(skilled_module, "Agent", StubAgent)
    monkeypatch.setattr(skilled_module, "get_model_provider", lambda **_: object())

    agent = skilled_module.create()

    assert isinstance(agent, StubAgent)
    assert agent.name == "Skilled Agent"
    tool_names = [getattr(t, "name", "") for t in agent.tools]
    assert any("execute_bash_command" in n for n in tool_names)
    assert any("load_skill_details" in n for n in tool_names)
    assert any("update_logbook" in n for n in tool_names)


def test_create_with_custom_model(monkeypatch):
    """skilled.create() forwards model_provider and model_name to get_model_provider."""
    StubAgent = _make_stub_agent_cls()
    sentinel_model = object()
    monkeypatch.setattr(skilled_module, "Agent", StubAgent)
    monkeypatch.setattr(
        skilled_module,
        "get_model_provider",
        lambda model_provider, model_name=None: sentinel_model,
    )

    agent = skilled_module.create(model_provider="openai", model_name="gpt-4")
    assert agent.model is sentinel_model
