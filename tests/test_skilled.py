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
    assert any("run_sub_task" in n for n in tool_names)


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


def test_create_run_sub_task_uses_same_model(monkeypatch):
    """skilled.create() passes model_provider and model_name to the run_sub_task factory."""
    StubAgent = _make_stub_agent_cls()
    received = {}

    def fake_make_run_sub_task(model_provider="cborg", model_name=None, max_turns=20):
        received["model_provider"] = model_provider
        received["model_name"] = model_name
        # Return a minimal stand-in with a .name attribute
        class _FakeTool:
            name = "run_sub_task"
        return _FakeTool()

    monkeypatch.setattr(skilled_module, "Agent", StubAgent)
    monkeypatch.setattr(skilled_module, "get_model_provider", lambda **_: object())
    monkeypatch.setattr(skilled_module, "make_run_sub_task", fake_make_run_sub_task)

    skilled_module.create(model_provider="openai", model_name="gpt-4o")

    assert received["model_provider"] == "openai"
    assert received["model_name"] == "gpt-4o"

