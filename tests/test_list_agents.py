"""Tests for list-agents command and agent catalog functionality."""

from unittest.mock import patch

import pytest

from hepagent.agent_helpers import AgentManifestLoader
from hepagent.agents.common import AgentContext


class MockWrapper:
    """Minimal stand-in for RunContextWrapper used in instruction tests."""

    def __init__(self, context):
        self.context = context


def test_get_agent_catalog_returns_agents(mock_agent_env):
    """Verify loader correctly scans the agents directory and parses YAML."""
    loader = AgentManifestLoader()
    agents = loader.get_agent_catalog()

    assert isinstance(agents, list)
    assert len(agents) == 3

    names = [a["name"] for a in agents]
    assert "research_scientist" in names
    assert "coder" in names
    assert "cosmologist" in names

    for agent in agents:
        assert "name" in agent
        assert "description" in agent
        assert agent["description"]  # non-empty


def test_get_agent_catalog_empty_when_no_agents_dir(tmp_path, monkeypatch):
    """Verify loader returns empty list when agents/ directory is absent."""
    empty_dir = tmp_path / "empty_registry"
    empty_dir.mkdir()

    with patch("hepagent.helpers.get_agent_dir", return_value=empty_dir):
        loader = AgentManifestLoader()
        agents = loader.get_agent_catalog()

    assert agents == []


def test_get_agent_instructions_returns_content(mock_agent_env):
    """Verify get_agent_instructions loads AGENT.md content for a known agent."""
    loader = AgentManifestLoader()
    instructions = loader.get_agent_instructions("research_scientist")

    # The conftest fixture writes the agent name into the AGENT.md body
    assert "research scientist" in instructions.lower()


def test_get_agent_instructions_missing_agent(mock_agent_env):
    """Verify get_agent_instructions returns empty string for unknown agent."""
    loader = AgentManifestLoader()
    instructions = loader.get_agent_instructions("nonexistent_agent")

    assert instructions == ""


def test_get_instructions_includes_agent_role(mock_agent_env):
    """Verify get_instructions includes agent-specific role when agent_name is set."""
    loader = AgentManifestLoader()
    context = AgentContext(agent_name="coder")
    wrapper = MockWrapper(context)
    instructions = loader.get_instructions(wrapper, None)  # type: ignore

    assert "# AGENT ROLE" in instructions


def test_get_instructions_no_agent_role_for_unknown(mock_agent_env):
    """Verify get_instructions omits AGENT ROLE section for unknown agent."""
    loader = AgentManifestLoader()
    context = AgentContext(agent_name="nonexistent_agent")
    wrapper = MockWrapper(context)
    instructions = loader.get_instructions(wrapper, None)  # type: ignore

    assert "# AGENT ROLE" not in instructions


def test_list_agents_command(mock_agent_env):
    """Verify list-agents command prints available agents."""
    import hepagent.main as main_module
    import typer as typer_mod

    output_parts = []

    def capture_echo(msg="", **kwargs):
        output_parts.append(str(msg))

    with patch.object(typer_mod, "echo", side_effect=capture_echo):
        main_module.list_agents()

    all_output = "\n".join(output_parts)
    assert "research_scientist" in all_output
    assert "coder" in all_output
    assert "cosmologist" in all_output


def test_list_agents_command_no_agents(tmp_path):
    """Verify list-agents command reports 'No agents found' when none exist."""
    import hepagent.main as main_module
    import typer as typer_mod

    empty_dir = tmp_path / "empty_registry"
    empty_dir.mkdir()

    output_parts = []

    def capture_echo(msg="", **kwargs):
        output_parts.append(str(msg))

    with (
        patch("hepagent.helpers.get_agent_dir", return_value=empty_dir),
        patch.object(typer_mod, "echo", side_effect=capture_echo),
    ):
        main_module.list_agents()

    assert any("No agents found" in s for s in output_parts)
