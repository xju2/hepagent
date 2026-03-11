"""Tests for hepagent.agents.iri module."""

import hepagent.agents.iri as iri_module


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


def test_create_returns_agent_with_iri_tools(monkeypatch):
    """iri.create() returns an Agent with submit_job and get_job_status tools."""
    StubAgent = _make_stub_agent_cls()
    sentinel_model = object()
    monkeypatch.setattr(iri_module, "Agent", StubAgent)
    monkeypatch.setattr(iri_module, "get_model_provider", lambda **_: sentinel_model)

    agent = iri_module.create()

    assert isinstance(agent, StubAgent)
    assert agent.name == "IRI Agent"
    tool_names = [getattr(t, "name", "") for t in agent.tools]
    assert any("submit_job" in n for n in tool_names)
    assert any("get_job_status" in n for n in tool_names)
    assert any("ask_user_for_info" in n for n in tool_names)


def test_create_with_custom_model(monkeypatch):
    """iri.create() uses the specified model_provider and model_name."""
    StubAgent = _make_stub_agent_cls()
    sentinel_model = object()
    monkeypatch.setattr(iri_module, "Agent", StubAgent)
    monkeypatch.setattr(
        iri_module,
        "get_model_provider",
        lambda model_provider, model_name=None: sentinel_model,
    )

    agent = iri_module.create(model_provider="openai", model_name="gpt-4")
    assert agent.model is sentinel_model
