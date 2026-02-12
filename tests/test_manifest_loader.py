from hepagent.agent_helpers import AgentManifestLoader
from hepagent.agents.common import AgentContext


def test_get_skill_catalog(mock_agent_env):
    """Verify the loader correctly scans the skills directory and parses YAML."""
    loader = AgentManifestLoader()
    catalog = loader.get_skill_catalog()

    # Check if the nyx skill from our conftest.py is present
    assert "nyx" in catalog.lower()
    assert "Test Nyx simulation skill" in catalog
    # Verify it doesn't crash if a folder is empty (if you added one)


def test_get_instructions_assembly(mock_agent_env):
    """Verify the loader assembles the full system prompt string correctly."""
    loader = AgentManifestLoader()

    # We need a mock RunContextWrapper to pass to get_instructions
    # The SDK's AgentContext is what we defined earlier
    dummy_context = AgentContext(agent_name="nyx")

    # Mocking the RunContextWrapper (simple approach for unit testing)
    class MockWrapper:
        def __init__(self, context):
            self.context = context

    wrapper = MockWrapper(dummy_context)

    # Call the loader (passing None for agent as it's likely unused in the string build)
    instructions = loader.get_instructions(wrapper, None)  # type: ignore

    # Assertions on prompt structure
    assert "# IDENTITY" in instructions
    assert "# AVAILABLE SKILLS" in instructions
    assert "Global shared memory" in instructions
    assert "CRITICAL RULE: SELF-IMPROVEMENT" in instructions


def test_loader_missing_files(tmp_path, monkeypatch):
    """Verify the loader handles an empty registry without crashing."""
    # Create a completely empty directory
    empty_dir = tmp_path / "empty_registry"
    empty_dir.mkdir()
    (empty_dir / "storage").mkdir()
    (empty_dir / "storage" / "MEMORY.md").write_text("Empty")

    from unittest.mock import patch

    with patch("hepagent.helpers.get_agent_dir", return_value=empty_dir):
        loader = AgentManifestLoader()
        catalog = loader.get_skill_catalog()
        assert catalog == ""  # Should be empty string, not a crash
