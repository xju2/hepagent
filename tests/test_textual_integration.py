"""Test TextualAgent integration with bash agent adapter."""

from agents import Agent
from scripts.bash_textual import AgentAdapter, TextualAgent


def test_agent_adapter_integration():
    """Test that AgentAdapter properly wraps an agent for TextualAgent."""
    # Create a mock agent similar to bash agent
    mock_agent = Agent(
        name="Test Bash Agent",
        instructions="You are a helpful assistant. Include THOUGHT in your responses.",
        model="gpt-4",
        tools=[],
    )

    # Create TextualAgent
    textual_app = TextualAgent(model="gpt-4", env={})

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


if __name__ == "__main__":
    test_agent_adapter_integration()
