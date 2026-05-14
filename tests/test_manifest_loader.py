from unittest.mock import patch

from hepagent.agent_helpers import AgentManifestLoader
from hepagent.agents.common import AgentContext


def test_get_skill_catalog(mock_agent_env):
    """Verify the loader correctly scans the skills directory and parses YAML."""
    loader = AgentManifestLoader()
    catalog = loader.get_skill_catalog()
    print(f"\n[DEBUG] Skill Catalog:\n{catalog}")

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
    print(f"\n[DEBUG] Assembled Instructions:\n{instructions}")

    # Assertions on prompt structure
    assert "# IDENTITY" in instructions
    assert "# AVAILABLE SKILLS" in instructions
    assert "# SHARED MEMORY" in instructions
    assert "# CRITICAL RULE: SELF-IMPROVEMENT" in instructions


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


def test_get_instructions_includes_user_profile(mock_agent_env, tmp_path):
    """Loader includes USER.md content in the assembled instructions."""
    user_profile = tmp_path / "USER.md"
    user_profile.write_text("# USER PROFILE\n\n## Goals\n- Publish results\n", encoding="utf-8")

    loader = AgentManifestLoader()
    dummy_context = AgentContext(agent_name="nyx")

    class MockWrapper:
        def __init__(self, context):
            self.context = context

    wrapper = MockWrapper(dummy_context)

    with (
        patch("hepagent.agent_helpers.ensure_user_profile_file"),
        patch("hepagent.agent_helpers.get_user_profile_path", return_value=user_profile),
    ):
        instructions = loader.get_instructions(wrapper, None)  # type: ignore[arg-type]

    assert "# USER PROFILE" in instructions
    assert "Publish results" in instructions


def test_get_instructions_creates_missing_user_profile_when_writable(mock_agent_env, tmp_path):
    """Loader creates USER.md on-demand when it is missing and writable."""
    user_profile = tmp_path / "USER.md"
    assert not user_profile.exists()

    loader = AgentManifestLoader()
    dummy_context = AgentContext(agent_name="nyx")

    class MockWrapper:
        def __init__(self, context):
            self.context = context

    wrapper = MockWrapper(dummy_context)

    def _create_profile():
        user_profile.write_text("# USER PROFILE\n\n## Goals\n- Publish results\n", encoding="utf-8")
        return user_profile

    with (
        patch("hepagent.agent_helpers.get_user_profile_path", return_value=user_profile),
        patch("hepagent.agent_helpers.ensure_user_profile_file", side_effect=_create_profile),
    ):
        instructions = loader.get_instructions(wrapper, None)  # type: ignore[arg-type]

    assert user_profile.exists()
    assert "# USER PROFILE" in instructions
    assert "Publish results" in instructions


def test_get_instructions_skips_user_profile_on_permission_error(mock_agent_env, tmp_path):
    """Loader stays read-only-safe when USER.md access raises PermissionError."""
    loader = AgentManifestLoader()
    dummy_context = AgentContext(agent_name="nyx")

    class MockWrapper:
        def __init__(self, context):
            self.context = context

    wrapper = MockWrapper(dummy_context)
    missing_profile = tmp_path / "missing" / "USER.md"

    with (
        patch("hepagent.agent_helpers.get_user_profile_path", return_value=missing_profile),
        patch(
            "hepagent.agent_helpers.ensure_user_profile_file", side_effect=PermissionError("denied")
        ),
    ):
        instructions = loader.get_instructions(wrapper, None)  # type: ignore[arg-type]

    assert "# IDENTITY" in instructions
    assert "# USER PROFILE" not in instructions
