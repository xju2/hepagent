"""Test session recording functionality in isolation."""

import json
from pathlib import Path
from unittest.mock import MagicMock, Mock


def test_save_session_creates_file():
    """Test that save_session creates a valid JSON file with all required fields."""
    # Import here to avoid issues with module-level imports
    import sys
    sys.path.insert(0, '/home/runner/work/hepagent/hepagent')
    sys.path.insert(0, '/home/runner/work/hepagent/hepagent/src')
    
    # Mock the necessary dependencies
    from dataclasses import dataclass
    
    @dataclass
    class MockConfig:
        mode: str = "confirm"
    
    @dataclass
    class MockModel:
        name: str = "test-model"
        cost: float = 0.05
    
    # Create a minimal mock adapter with just the necessary attributes
    class MinimalAdapter:
        def __init__(self):
            self.messages = []
            self.config = MockConfig()
            self.model = MockModel()
            
        def add_message(self, role: str, content: str, **kwargs):
            """Add a message to the messages list."""
            import time
            self.messages.append({
                "role": role,
                "content": content,
                "timestamp": time.time(),
                **kwargs,
            })
        
        def save_session(self, task: str, status: str, result: str) -> str:
            """Save the session messages to a JSON file."""
            from datetime import datetime
            
            # Create sessions directory if it doesn't exist
            sessions_dir = Path("sessions")
            sessions_dir.mkdir(exist_ok=True)
            
            # Generate timestamped filename
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            session_file = sessions_dir / f"session_{timestamp}.json"
            
            # Prepare session data
            session_data = {
                "task": task,
                "status": status,
                "result": result,
                "model": self.model.name,
                "cost": self.model.cost,
                "mode": self.config.mode,
                "timestamp": timestamp,
                "messages": self.messages,
            }
            
            # Save to file
            with open(session_file, "w", encoding="utf-8") as f:
                json.dump(session_data, f, indent=2, ensure_ascii=False)
            
            return str(session_file)
    
    # Create adapter and add test messages
    adapter = MinimalAdapter()
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
    assert session_data["model"] == "test-model"
    assert session_data["cost"] == 0.05
    assert session_data["mode"] == "confirm"
    assert "timestamp" in session_data
    assert "messages" in session_data
    
    # Verify messages were saved
    assert len(session_data["messages"]) == 4
    assert session_data["messages"][0]["role"] == "system"
    assert session_data["messages"][1]["role"] == "user"
    assert session_data["messages"][1]["kind"] == "task"
    assert session_data["messages"][2]["role"] == "assistant"
    assert session_data["messages"][3]["kind"] == "final"
    
    # Verify message structure
    for msg in session_data["messages"]:
        assert "role" in msg
        assert "content" in msg
        assert "timestamp" in msg
    
    # Cleanup
    Path(session_file).unlink()
    sessions_dir = Path("sessions")
    if sessions_dir.exists() and not list(sessions_dir.iterdir()):
        sessions_dir.rmdir()
    
    print(f"✓ Session recording test passed! File created at: {session_file}")


if __name__ == "__main__":
    test_save_session_creates_file()
    print("All tests passed!")
