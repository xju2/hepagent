"""Additional tests for hepagent.agent_helpers module (print_usage, update_logbook)."""

import asyncio
import json

import pytest

from agents import Usage
from agents.tool import ToolContext
from hepagent.agent_helpers import AgentManifestLoader, print_usage
from hepagent.agents.common import AgentContext


# ---------------------------------------------------------------------------
# print_usage
# ---------------------------------------------------------------------------

def test_print_usage_without_model_name(capsys):
    """print_usage prints token counts and omits cost section when no model specified."""
    usage = Usage(input_tokens=100, output_tokens=50, total_tokens=150)
    print_usage(usage)
    captured = capsys.readouterr()
    assert "Input tokens: 100" in captured.out
    assert "Output tokens: 50" in captured.out
    assert "Total tokens: 150" in captured.out
    assert "model_name" in captured.out or "Provide model_name" in captured.out


def test_print_usage_with_model_name(capsys):
    """print_usage includes cost section when model_name is provided."""
    usage = Usage(input_tokens=1_000_000, output_tokens=500_000, total_tokens=1_500_000)
    print_usage(usage, model_name="openai/gpt-5")
    captured = capsys.readouterr()
    assert "Cost" in captured.out
    assert "openai/gpt-5" in captured.out


# ---------------------------------------------------------------------------
# update_logbook
# ---------------------------------------------------------------------------

def _invoke_logbook(category: str, observation: str, correction: str = "", active_skill: str | None = None):
    """Helper to call update_logbook via its on_invoke_tool coroutine."""
    from hepagent.agent_helpers import update_logbook

    ctx_val = AgentContext(agent_name="test")
    ctx_val.active_skill = active_skill

    payload = json.dumps({"category": category, "observation": observation, "correction": correction})
    ctx = ToolContext(
        context=ctx_val,
        tool_name="update_logbook",
        tool_call_id="test-id",
        tool_arguments=payload,
    )
    return asyncio.run(update_logbook.on_invoke_tool(ctx, payload))


def test_update_logbook_with_active_skill(mock_agent_env):
    """update_logbook writes to the active skill's LOGBOOK.md."""
    result = _invoke_logbook(
        category="Corrective Insight",
        observation="Never delete files.",
        correction="Always ask first.",
        active_skill="nyx",
    )
    assert "nyx" in result
    logbook_path = mock_agent_env / "skills" / "nyx" / "LOGBOOK.md"
    content = logbook_path.read_text()
    assert "Never delete files." in content


def test_update_logbook_without_active_skill(mock_agent_env):
    """update_logbook falls back to 'general' skill (common path) when no skill is active."""
    result = _invoke_logbook(
        category="Technical Error",
        observation="Observed an issue.",
        active_skill=None,
    )
    # Should return a message mentioning either 'general' or 'common'
    assert "general" in result or "common" in result


def test_update_logbook_with_correction(mock_agent_env):
    """update_logbook includes correction in the log entry."""
    _invoke_logbook(
        category="Preference",
        observation="User prefers Python.",
        correction="Use Python scripts.",
        active_skill="nyx",
    )
    logbook_path = mock_agent_env / "skills" / "nyx" / "LOGBOOK.md"
    content = logbook_path.read_text()
    assert "Use Python scripts." in content


# ---------------------------------------------------------------------------
# AgentManifestLoader
# ---------------------------------------------------------------------------

def test_manifest_loader_get_skill_catalog_nonempty(mock_agent_env):
    """get_skill_catalog returns a non-empty string when skills exist."""
    loader = AgentManifestLoader()
    catalog = loader.get_skill_catalog()
    assert isinstance(catalog, str)
    assert len(catalog) > 0


def test_manifest_loader_extract_yaml(mock_agent_env):
    """_extract_yaml parses frontmatter from a SKILL.md file."""
    loader = AgentManifestLoader()
    skill_file = mock_agent_env / "skills" / "nyx" / "SKILL.md"
    meta = loader._extract_yaml(skill_file)
    assert meta.get("name") == "nyx"
