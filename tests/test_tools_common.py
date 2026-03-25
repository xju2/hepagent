"""Tests for hepagent.tools.common (update_memory,
load_skill_details, read_resource, ask_user_for_info)."""

import asyncio
import json

from agents.tool import ToolContext
from hepagent.agents.common import AgentContext
from hepagent.tools import common as tools_common


def _make_ctx(skill_name: str = "", active_skill: str | None = None) -> AgentContext:
    ctx = AgentContext(agent_name=skill_name)
    ctx.active_skill = active_skill
    return ctx


def _invoke(tool, context_val: AgentContext, **kwargs):
    """Helper to call a function_tool via its on_invoke_tool coroutine."""
    payload = json.dumps(kwargs)
    ctx = ToolContext(
        context=context_val,
        tool_name=getattr(tool, "name", ""),
        tool_call_id="test-id",
        tool_arguments=payload,
    )
    return asyncio.run(tool.on_invoke_tool(ctx, payload))


# ---------------------------------------------------------------------------
# _get_skill_dir / _get_memory_path
# ---------------------------------------------------------------------------


def test_get_skill_dir_returns_path(mock_agent_env):
    """_get_skill_dir returns a Path under agent_dir/skills/<name>."""
    path = tools_common._get_skill_dir("nyx")
    assert path.name == "nyx"
    assert "skills" in str(path)


def test_get_memory_path_returns_path(mock_agent_env):
    """_get_memory_path returns a Path to storage/MEMORY.md."""
    path = tools_common._get_memory_path()
    assert path.name == "MEMORY.md"
    assert "storage" in str(path)


# ---------------------------------------------------------------------------
# update_memory
# ---------------------------------------------------------------------------


def test_update_memory_appends_entry(mock_agent_env):
    """update_memory writes a bullet entry to MEMORY.md."""
    ctx_val = _make_ctx("test")
    result = _invoke(
        tools_common.update_memory,
        ctx_val,
        category="Preference",
        observation="Use tabs.",
        correction="Always use tabs.",
    )
    assert "Memory successfully updated" in result

    memory_file = mock_agent_env / "storage" / "MEMORY.md"
    content = memory_file.read_text()
    assert "Use tabs." in content
    assert "Always use tabs." in content


def test_update_memory_without_correction(mock_agent_env):
    """update_memory works without a correction field."""
    ctx_val = _make_ctx("test")
    result = _invoke(
        tools_common.update_memory,
        ctx_val,
        category="Corrective Insight",
        observation="Remember X.",
    )
    assert "Memory successfully updated" in result


# ---------------------------------------------------------------------------
# load_skill_details
# ---------------------------------------------------------------------------


def test_load_skill_details_existing_skill(mock_agent_env):
    """load_skill_details returns instructions for an existing skill."""
    ctx_val = _make_ctx("nyx")
    result = _invoke(tools_common.load_skill_details, ctx_val, skill_name="nyx")
    assert "nyx" in result.lower()
    assert ctx_val.active_skill == "nyx"


def test_load_skill_details_nonexistent_skill(mock_agent_env):
    """load_skill_details returns an error message for an unknown skill."""
    ctx_val = _make_ctx("test")
    result = _invoke(tools_common.load_skill_details, ctx_val, skill_name="nonexistent_xyz")
    assert "Error" in result
    assert ctx_val.active_skill is None


def test_load_skill_details_includes_logbook(mock_agent_env):
    """load_skill_details includes logbook content when present."""
    ctx_val = _make_ctx("nyx")
    result = _invoke(tools_common.load_skill_details, ctx_val, skill_name="nyx")
    # The mock sets up a LOGBOOK.md file
    assert "LOGBOOK" in result or "LESSONS" in result


def test_load_skill_details_with_resources(mock_agent_env, tmp_path):
    """load_skill_details lists resources when present."""
    # Create a resources directory with a markdown file
    resources_dir = mock_agent_env / "skills" / "nyx" / "resources"
    resources_dir.mkdir(exist_ok=True)
    (resources_dir / "TF.md").write_text("Transfer function info")

    ctx_val = _make_ctx("nyx")
    result = _invoke(tools_common.load_skill_details, ctx_val, skill_name="nyx")
    assert "TF" in result


# ---------------------------------------------------------------------------
# read_resource
# ---------------------------------------------------------------------------


def test_read_resource_no_active_skill(mock_agent_env):
    """read_resource returns an error when no skill is active."""
    ctx_val = _make_ctx("test", active_skill=None)
    result = _invoke(tools_common.read_resource, ctx_val, resource_name="TF")
    assert "Error" in result
    assert "load a skill" in result


def test_read_resource_existing_resource(mock_agent_env):
    """read_resource returns the content of an existing resource."""
    resources_dir = mock_agent_env / "skills" / "nyx" / "resources"
    resources_dir.mkdir(exist_ok=True)
    (resources_dir / "TF.md").write_text("Transfer function details.")

    ctx_val = _make_ctx("nyx", active_skill="nyx")
    result = _invoke(tools_common.read_resource, ctx_val, resource_name="TF")
    assert "Transfer function details." in result


def test_read_resource_missing_resource(mock_agent_env):
    """read_resource returns an error message when the resource file is absent."""
    ctx_val = _make_ctx("nyx", active_skill="nyx")
    result = _invoke(tools_common.read_resource, ctx_val, resource_name="NONEXISTENT")
    assert "not found" in result


# ---------------------------------------------------------------------------
# ask_user_for_info
# ---------------------------------------------------------------------------


def test_ask_user_for_info_returns_input(monkeypatch, mock_agent_env):
    """ask_user_for_info returns and strips user's response."""
    monkeypatch.setattr("builtins.input", lambda _: "  user_answer  ")

    ctx_val = _make_ctx("test")
    result = _invoke(tools_common.ask_user_for_info, ctx_val, prompt="Enter a value:")
    assert result == "user_answer"


def test_ask_user_for_info_with_thought(monkeypatch, mock_agent_env, capsys):
    """ask_user_for_info prints THOUGHT when provided."""
    monkeypatch.setattr("builtins.input", lambda _: "answer")

    ctx_val = _make_ctx("test")
    _result = _invoke(
        tools_common.ask_user_for_info, ctx_val, prompt="Enter value:", thought="My thought"
    )
    captured = capsys.readouterr()
    assert "THOUGHT" in captured.out
    assert "My thought" in captured.out


def test_ask_user_for_info_handles_eoferror(monkeypatch, mock_agent_env):
    """ask_user_for_info returns empty string on EOFError when /dev/tty unavailable."""

    def raise_eof(_):
        raise EOFError

    monkeypatch.setattr("builtins.input", raise_eof)
    # Also patch open to raise OSError so the /dev/tty fallback also fails
    original_open = open

    def patched_open(path, *args, **kwargs):
        if path == "/dev/tty":
            raise OSError("no tty")
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr("builtins.open", patched_open)

    ctx_val = _make_ctx("test")
    result = _invoke(tools_common.ask_user_for_info, ctx_val, prompt="Enter value:")
    assert result == ""


# ---------------------------------------------------------------------------
# make_run_sub_task
# ---------------------------------------------------------------------------


def test_make_run_sub_task_returns_function_tool(mock_agent_env):
    """make_run_sub_task returns a FunctionTool with the expected name."""
    tool = tools_common.make_run_sub_task()
    assert getattr(tool, "name", "") == "run_sub_task"


def test_make_run_sub_task_with_explicit_model(mock_agent_env):
    """make_run_sub_task accepts model_provider and model_name without error."""
    tool = tools_common.make_run_sub_task(model_provider="openai", model_name="gpt-4o")
    assert getattr(tool, "name", "") == "run_sub_task"


def test_make_run_sub_task_independent_tools(mock_agent_env):
    """Two calls to make_run_sub_task produce independent tool objects."""
    tool_a = tools_common.make_run_sub_task(model_provider="cborg")
    tool_b = tools_common.make_run_sub_task(model_provider="openai")
    assert tool_a is not tool_b


def test_run_sub_task_invokes_runner(monkeypatch, mock_agent_env):
    """run_sub_task calls Runner.run with a fresh AgentContext and returns final_output."""
    captured = {}

    class FakeResult:
        final_output = "sub-task done"

    async def fake_runner_run(agent, task, context, max_turns):
        captured["agent_name"] = agent.name
        captured["task"] = task
        captured["context_agent"] = context.agent_name
        captured["context_skill"] = context.active_skill
        captured["max_turns"] = max_turns
        return FakeResult()

    # Patch Runner.run inside the tools.common module's namespace
    import hepagent.tools.common as _tc

    monkeypatch.setattr("agents.Runner.run", fake_runner_run)

    tool = _tc.make_run_sub_task(model_provider="cborg", model_name=None, max_turns=5)

    ctx_val = _make_ctx("scientist", active_skill="nyx")
    result = _invoke(tool, ctx_val, task="Do the thing.", instructions="Step 1: do it.")

    assert result == "sub-task done"
    assert captured["task"] == "Do the thing."
    assert captured["context_agent"] == "scientist"
    assert captured["context_skill"] == "nyx"
    assert captured["max_turns"] == 5


def test_run_sub_task_propagates_active_skill(monkeypatch, mock_agent_env):
    """run_sub_task forwards the parent's active_skill to the sub-agent context."""
    received_skill = {}

    class FakeResult:
        final_output = "ok"

    async def fake_runner_run(agent, task, context, max_turns):
        received_skill["active_skill"] = context.active_skill
        return FakeResult()

    monkeypatch.setattr("agents.Runner.run", fake_runner_run)

    tool = tools_common.make_run_sub_task()
    ctx_val = _make_ctx("scientist", active_skill="nyx")
    _invoke(tool, ctx_val, task="A task.", instructions="Some instructions.")

    assert received_skill["active_skill"] == "nyx"


def test_run_sub_task_empty_final_output(monkeypatch, mock_agent_env):
    """run_sub_task returns empty string when final_output is None."""

    class FakeResult:
        final_output = None

    async def fake_runner_run(agent, task, context, max_turns):
        return FakeResult()

    monkeypatch.setattr("agents.Runner.run", fake_runner_run)

    tool = tools_common.make_run_sub_task()
    ctx_val = _make_ctx("test")
    result = _invoke(tool, ctx_val, task="A task.", instructions="Instructions.")
    assert result == ""
