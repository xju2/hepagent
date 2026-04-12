"""Additional tests for hepagent.agent_helpers module helpers and tools."""

import asyncio
import json
from unittest.mock import patch

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


def _invoke_logbook(
    category: str, observation: str, correction: str = "", active_skill: str | None = None
):
    """Helper to call update_logbook via its on_invoke_tool coroutine."""
    from hepagent.agent_helpers import update_logbook

    ctx_val = AgentContext(agent_name="test")
    ctx_val.active_skill = active_skill

    payload = json.dumps(
        {"category": category, "observation": observation, "correction": correction}
    )
    ctx = ToolContext(
        context=ctx_val,
        tool_name="update_logbook",
        tool_call_id="test-id",
        tool_arguments=payload,
    )
    return asyncio.run(update_logbook.on_invoke_tool(ctx, payload))


def _invoke_user_profile(category: str, observation: str):
    """Helper to call update_user_profile via its on_invoke_tool coroutine."""
    from hepagent.agent_helpers import update_user_profile

    ctx_val = AgentContext(agent_name="test")
    payload = json.dumps({"category": category, "observation": observation})
    ctx = ToolContext(
        context=ctx_val,
        tool_name="update_user_profile",
        tool_call_id="test-id",
        tool_arguments=payload,
    )
    return asyncio.run(update_user_profile.on_invoke_tool(ctx, payload))


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
    """get_skill_catalog includes skill metadata parsed from SKILL.md frontmatter."""
    loader = AgentManifestLoader()
    catalog = loader.get_skill_catalog()
    assert "**nyx**" in catalog


def test_update_user_profile_appends_new_entry(tmp_path):
    """update_user_profile appends a new item under the mapped section."""
    profile = tmp_path / "USER.md"
    profile.write_text("# USER PROFILE\n\n## Preferences\n- \n\n## Goals\n- \n", encoding="utf-8")

    with (
        patch("hepagent.agent_helpers.ensure_user_profile_file"),
        patch("hepagent.agent_helpers.get_user_profile_path", return_value=profile),
    ):
        result = _invoke_user_profile("Goal", "Finish v1 release")

    assert "updated" in result.lower()
    content = profile.read_text(encoding="utf-8")
    assert "- Finish v1 release" in content


def test_update_user_profile_removes_placeholder_in_target_section(tmp_path):
    """update_user_profile removes template placeholder bullets in the target section."""
    profile = tmp_path / "USER.md"
    profile.write_text(
        "# USER PROFILE\n\n## Preferences\n-\n\n## Goals\n-\n",
        encoding="utf-8",
    )

    with (
        patch("hepagent.agent_helpers.ensure_user_profile_file"),
        patch("hepagent.agent_helpers.get_user_profile_path", return_value=profile),
    ):
        result = _invoke_user_profile("Goal", "Ship stable release")

    assert "updated" in result.lower()
    content = profile.read_text(encoding="utf-8")
    assert "## Goals\n- Ship stable release" in content
    assert "## Goals\n-\n" not in content
    # Placeholders in non-target sections should be untouched.
    assert "## Preferences\n-\n" in content


def test_update_user_profile_removes_punctuation_placeholders_only(tmp_path):
    """Punctuation-only bullets in target section are cleaned when adding a real note."""
    profile = tmp_path / "USER.md"
    profile.write_text(
        "# USER PROFILE\n\n## Preferences\n- Expert in C++\n\n## Goals\n- / /\n- ...\n",
        encoding="utf-8",
    )

    with (
        patch("hepagent.agent_helpers.ensure_user_profile_file"),
        patch("hepagent.agent_helpers.get_user_profile_path", return_value=profile),
    ):
        _invoke_user_profile("Goal", "Publish benchmark")

    content = profile.read_text(encoding="utf-8")
    assert "## Goals\n- Publish benchmark" in content
    assert "- / /" not in content
    assert "- ..." not in content
    # Substantive bullets in other sections should remain.
    assert "- Expert in C++" in content


def test_update_user_profile_skips_duplicate_entry(tmp_path):
    """update_user_profile does not duplicate an already-captured note."""
    profile = tmp_path / "USER.md"
    profile.write_text(
        "# USER PROFILE\n\n## Preferences\n- Prefer concise answers\n", encoding="utf-8"
    )

    with (
        patch("hepagent.agent_helpers.ensure_user_profile_file"),
        patch("hepagent.agent_helpers.get_user_profile_path", return_value=profile),
    ):
        first = _invoke_user_profile("Preference", "Prefer concise answers")
        second = _invoke_user_profile("Preference", "Prefer concise answers")

    assert "no update needed" in first.lower() or "updated" in first.lower()
    assert "no update needed" in second.lower()
    content = profile.read_text(encoding="utf-8")
    assert content.count("- Prefer concise answers") == 1
