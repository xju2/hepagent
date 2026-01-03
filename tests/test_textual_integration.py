"""Test TextualAgent integration with bash agent adapter."""

import json
from pathlib import Path
from scripts.bash_textual import TextualAgent, AgentAdapter
from agents import Agent


def test_agent_adapter_integration():
    """Test that AgentAdapter properly wraps an agent for TextualAgent."""
    # Create a mock agent similar to bash agent
    mock_agent = Agent(
        name="Test Bash Agent",
        instructions="You are a helpful assistant. Include THOUGHT in your responses.",
        model="gpt-4",
        tools=[]
    )
    
    # Create TextualAgent
    textual_app = TextualAgent(model="gpt-4", env={})
    
    # Create adapter
    adapter = AgentAdapter(mock_agent, textual_app)
    
    # Verify adapter has all required attributes for TextualAgent
    assert hasattr(adapter, 'messages')
    assert hasattr(adapter, 'config')
    assert hasattr(adapter, 'model')
    assert hasattr(adapter, 'env')
    assert hasattr(adapter, 'run')
    
    # Verify config has mode
    assert hasattr(adapter.config, 'mode')
    assert adapter.config.mode in ['yolo', 'confirm', 'human']
    
    # Verify model has cost
    assert hasattr(adapter.model, 'cost')
    assert isinstance(adapter.model.cost, (int, float))
    
    # Verify model has name
    assert hasattr(adapter.model, 'name')
    assert isinstance(adapter.model.name, str)
    
    # Verify the wrapped agent exists and has the right name
    assert adapter.agent.name == "Test Bash Agent"
    
    print("✓ All integration tests passed!")


def test_session_recording():
    """Test that session recording creates proper JSON files."""
    # Create a mock agent
    mock_agent = Agent(
        name="Test Agent",
        instructions="Test instructions",
        model="gpt-4",
        tools=[]
    )
    
    # Create TextualAgent and adapter
    textual_app = TextualAgent(model="gpt-4", env={})
    adapter = AgentAdapter(mock_agent, textual_app)
    
    # Add some test messages
    adapter.add_message("system", "Test system message")
    adapter.add_message("user", "Test user message", kind="task")
    adapter.add_message("assistant", "Test assistant response")
    adapter.add_message("system", "Test completion", kind="final")
    
    # Save the session
    test_task = "Test task description"
    test_status = "success"
    test_result = "Test result"
    session_file = adapter.save_session(test_task, test_status, test_result)
    
    # Verify file was created
    assert Path(session_file).exists(), f"Session file {session_file} was not created"
    
    # Load and verify the content
    with open(session_file, 'r') as f:
        session_data = json.load(f)
    
    # Verify all required fields exist
    assert session_data["task"] == test_task
    assert session_data["status"] == test_status
    assert session_data["result"] == test_result
    assert "model" in session_data
    assert "cost" in session_data
    assert "mode" in session_data
    assert "timestamp" in session_data
    assert "messages" in session_data
    
    # Verify messages were saved
    assert len(session_data["messages"]) == 4
    assert session_data["messages"][0]["role"] == "system"
    assert session_data["messages"][1]["role"] == "user"
    assert session_data["messages"][2]["role"] == "assistant"
    assert session_data["messages"][3]["kind"] == "final"
    
    # Cleanup
    Path(session_file).unlink()
    sessions_dir = Path("sessions")
    if sessions_dir.exists() and not list(sessions_dir.iterdir()):
        sessions_dir.rmdir()
    
    print(f"✓ Session recording test passed! File created at: {session_file}")


if __name__ == "__main__":
    test_agent_adapter_integration()
    test_session_recording()
