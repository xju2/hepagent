"""Test TextualAgent integration with bash agent adapter."""

from types import SimpleNamespace

import hepagent.agents.textual as textual
from hepagent.agents.textual import AgentAdapter, TextualAgent
from hepagent.utils.model_providers import get_model_provider_settings


def test_agent_adapter_integration(monkeypatch):
    """Test that AgentAdapter properly wraps an agent for TextualAgent."""

    class StubAgent:
        def __init__(self, name, instructions, model, tools, hooks=None, **kwargs):
            self.name = name
            self.instructions = instructions
            self.model = model
            self.tools = tools
            self.hooks = hooks

    monkeypatch.setattr(textual, "Agent", StubAgent)
    # Create a mock agent similar to bash agent
    default_model = get_model_provider_settings("cborg").default_model
    mock_agent = SimpleNamespace(
        name="Test Bash Agent",
        instructions="You are a helpful assistant. Include THOUGHT in your responses.",
        model=default_model,
        tools=[],
    )

    # Create TextualAgent
    textual_app = TextualAgent(model=default_model, env={})

    # Create adapter
    adapter = AgentAdapter(mock_agent, textual_app)

    # Verify adapter has all required attributes for TextualAgent
    assert hasattr(adapter, "messages")
    assert hasattr(adapter, "config")
    assert hasattr(adapter, "model")
    assert hasattr(adapter, "env")
    assert hasattr(adapter, "run")

    # Verify config has mode
    assert hasattr(adapter.config, "mode")
    assert adapter.config.mode in ["yolo", "confirm", "human"]

    # Verify model has cost
    assert hasattr(adapter.model, "cost")
    assert isinstance(adapter.model.cost, (int, float))

    # Verify model has name
    assert hasattr(adapter.model, "name")
    assert isinstance(adapter.model.name, str)

    # Verify the wrapped agent exists and has the right name
    assert adapter.agent.name == "Test Bash Agent"

    print("✓ All integration tests passed!")
