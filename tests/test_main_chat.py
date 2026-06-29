import asyncio
import inspect
import json
import re
from types import SimpleNamespace

from typer.testing import CliRunner

import hepagent.main as main_module
from agents.tool import ToolContext


def _normalize_cli_output(text: str) -> str:
    """Strip ANSI styling and normalize whitespace for robust assertions."""
    text = re.sub(r"\x1b\[[0-9;]*m", "", text)
    return " ".join(text.split())


def _invoke_tool(tool, **kwargs):
    payload = json.dumps(kwargs)
    ctx = ToolContext(
        tool_name=getattr(tool, "name", ""),
        tool_call_id="test-call",
        tool_arguments=payload,
        context=None,
    )
    result = tool.on_invoke_tool(ctx, payload)
    if inspect.isawaitable(result):
        return asyncio.run(result)
    return result


class _DummyAgent:
    def __init__(self):
        self.name = "dummy"
        self.instructions = "instructions"
        self.model = "test-model"
        self.tools = []

    def clone(self, **kwargs):
        agent = _DummyAgent()
        agent.tools = kwargs.get("tools", self.tools)
        return agent


class _FakeCliRepl:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.run_called = False

    def run(self):
        self.run_called = True


def _setup_stubs(monkeypatch, session_sentinel):
    created_repls = []
    run_calls = []
    chat_calls = []
    skilled_calls = []
    explorer_calls = []
    role_calls = []

    def _fake_create_skilled_agent(**kwargs):
        skilled_calls.append(kwargs)
        return _DummyAgent()

    def _fake_create_explorer_agent(**kwargs):
        explorer_calls.append(kwargs)
        return _DummyAgent()

    def _fake_create_role_agent(**kwargs):
        role_calls.append(kwargs)
        return _DummyAgent()

    monkeypatch.setattr(main_module, "create_skilled_agent", _fake_create_skilled_agent)
    monkeypatch.setattr(main_module, "create_explorer_agent", _fake_create_explorer_agent)
    monkeypatch.setattr(main_module, "create_role_agent", _fake_create_role_agent)

    def _fake_cli_repl(**kwargs):
        repl = _FakeCliRepl(**kwargs)
        created_repls.append(repl)
        return repl

    monkeypatch.setattr(main_module, "CliRepl", _fake_cli_repl)

    async def _fake_run_agent_task(**kwargs):
        run_calls.append(kwargs)
        return "ok"

    monkeypatch.setattr(main_module, "run_agent_task", _fake_run_agent_task)

    def _fake_create_chat_session(conversation_id: str):
        chat_calls.append(conversation_id)
        return session_sentinel

    monkeypatch.setattr(main_module, "create_chat_session", _fake_create_chat_session)
    return run_calls, created_repls, chat_calls, skilled_calls, explorer_calls, role_calls


def test_run_command_uses_chat_session(monkeypatch):
    session_sentinel = object()
    run_calls, _, chat_calls, _, _, _ = _setup_stubs(monkeypatch, session_sentinel)

    runner = CliRunner()
    result = runner.invoke(main_module.app, ["run", "--chat", "conv-1", "hello"])

    assert result.exit_code == 0
    assert chat_calls == ["conv-1"]
    assert len(run_calls) == 1
    assert run_calls[0]["session"] is session_sentinel


def test_default_to_run_path_uses_chat_session(monkeypatch):
    session_sentinel = object()
    run_calls, _, chat_calls, _, _, _ = _setup_stubs(monkeypatch, session_sentinel)

    runner = CliRunner()
    result = runner.invoke(main_module.app, ["--chat", "conv-2", "hello"])

    assert result.exit_code == 0
    assert chat_calls == ["conv-2"]
    assert len(run_calls) == 1
    assert run_calls[0]["session"] is session_sentinel


def test_run_command_propagates_non_interactive(monkeypatch):
    session_sentinel = object()
    run_calls, _, _, _, _, _ = _setup_stubs(monkeypatch, session_sentinel)

    runner = CliRunner()
    result = runner.invoke(main_module.app, ["run", "--non-interactive", "hello"])

    assert result.exit_code == 0
    assert run_calls[0]["non_interactive"] is True
    assert run_calls[0]["yolo"] is False


def test_default_to_run_path_propagates_non_interactive(monkeypatch):
    session_sentinel = object()
    run_calls, _, _, _, _, _ = _setup_stubs(monkeypatch, session_sentinel)

    runner = CliRunner()
    result = runner.invoke(main_module.app, ["--non-interactive", "hello"])

    assert result.exit_code == 0
    assert run_calls[0]["non_interactive"] is True


def test_run_shell_mode_uses_role_agent(monkeypatch):
    session_sentinel = object()
    run_calls, _, _, skilled_calls, _, role_calls = _setup_stubs(monkeypatch, session_sentinel)

    runner = CliRunner()
    result = runner.invoke(main_module.app, ["run", "-a", "shell", "check disk space"])

    assert result.exit_code == 0
    assert len(skilled_calls) == 0
    assert len(role_calls) == 1
    assert role_calls[0]["role_name"] == "shell"
    assert run_calls[0]["task_prompt"] == "check disk space"
    assert run_calls[0]["context"].agent_name == "shell"


def test_run_describe_mode_uses_role_agent(monkeypatch):
    session_sentinel = object()
    run_calls, _, _, skilled_calls, _, role_calls = _setup_stubs(monkeypatch, session_sentinel)

    runner = CliRunner()
    result = runner.invoke(main_module.app, ["run", "-a", "shell_describer", "ls -la | wc -l"])

    assert result.exit_code == 0
    assert len(skilled_calls) == 0
    assert len(role_calls) == 1
    assert role_calls[0]["role_name"] == "shell_describer"
    assert run_calls[0]["task_prompt"] == "ls -la | wc -l"
    assert run_calls[0]["context"].agent_name == "shell_describer"


def test_run_code_mode_uses_role_agent(monkeypatch):
    session_sentinel = object()
    run_calls, _, _, skilled_calls, _, role_calls = _setup_stubs(monkeypatch, session_sentinel)

    runner = CliRunner()
    result = runner.invoke(main_module.app, ["run", "-a", "coder", "write python to count xju"])

    assert result.exit_code == 0
    assert len(skilled_calls) == 0
    assert len(role_calls) == 1
    assert role_calls[0]["role_name"] == "coder"
    assert run_calls[0]["task_prompt"] == "write python to count xju"
    assert run_calls[0]["context"].agent_name == "coder"


def test_run_default_mode_uses_skilled_agent(monkeypatch):
    session_sentinel = object()
    run_calls, _, _, skilled_calls, _, role_calls = _setup_stubs(monkeypatch, session_sentinel)

    runner = CliRunner()
    result = runner.invoke(main_module.app, ["run", "run a nyx simulation"])

    assert result.exit_code == 0
    assert len(skilled_calls) == 1
    assert len(role_calls) == 0
    assert run_calls[0]["task_prompt"] == "run a nyx simulation"
    assert run_calls[0]["context"].agent_name == "scientist"


def test_run_explorer_mode_uses_explorer_agent(monkeypatch):
    session_sentinel = object()
    run_calls, _, _, skilled_calls, explorer_calls, role_calls = _setup_stubs(
        monkeypatch, session_sentinel
    )

    runner = CliRunner()
    result = runner.invoke(main_module.app, ["run", "-a", "explorer", "find axion ideas"])

    assert result.exit_code == 0
    assert len(skilled_calls) == 0
    assert len(explorer_calls) == 1
    assert len(role_calls) == 0
    assert run_calls[0]["task_prompt"] == "find axion ideas"
    assert run_calls[0]["context"].agent_name == "explorer"


def test_run_agent_task_executes_text_bash_block_and_continues(monkeypatch):
    calls = []
    executed = []

    class FakeResult:
        last_agent = _DummyAgent()

        def __init__(self, final_output):
            self.final_output = final_output

        def to_input_list(self):
            return [{"role": "assistant", "content": self.final_output}]

    class FakeRunner:
        @staticmethod
        async def run(agent, input, context=None, max_turns=None, session=None):
            calls.append(
                {
                    "input": input,
                    "context": context,
                    "max_turns": max_turns,
                    "session": session,
                }
            )
            if len(calls) == 1:
                return FakeResult("THOUGHT: inspect\n```bash\necho hi\n```")
            return FakeResult("Done.")

    def fake_execute_bash_command(cmd, cwd=""):
        executed.append({"cmd": cmd, "cwd": cwd})
        return {"output": "hi\n", "returncode": 0}

    monkeypatch.setattr(main_module, "Runner", FakeRunner)
    monkeypatch.setattr(main_module, "execute_bash_command", fake_execute_bash_command)

    context = main_module.AgentContext(agent_name="shell")
    output = asyncio.run(
        main_module.run_agent_task(
            agent=_DummyAgent(),
            task_prompt="start",
            context=context,
            max_turns=3,
            session=None,
            yolo=False,
            non_interactive=True,
        )
    )

    assert output == "Done."
    assert executed == [{"cmd": "echo hi", "cwd": ""}]
    assert calls[0] == {
        "input": "start",
        "context": context,
        "max_turns": 3,
        "session": None,
    }
    assert "The bash command proposed in your previous response has completed." in calls[1][
        "input"
    ][-1]["content"]


def test_non_interactive_ask_user_tool_returns_empty_without_reading(monkeypatch):
    wrapper = main_module.TerminalRunToolWrapper(non_interactive=True)

    async def fail_to_thread(*args, **kwargs):
        raise AssertionError("non-interactive ask_user_for_info must not read input")

    monkeypatch.setattr(main_module.asyncio, "to_thread", fail_to_thread)

    tool = wrapper._create_ask_user_tool()
    result = _invoke_tool(tool, prompt="Need input", thought="")

    assert result == ""


def test_list_agents_includes_roles(monkeypatch):
    monkeypatch.setattr(
        main_module,
        "create_role_cfg",
        lambda: {
            "shell": SimpleNamespace(description="shell role"),
            "coder": SimpleNamespace(description="coder role"),
        },
    )

    runner = CliRunner()
    result = runner.invoke(main_module.app, ["list-agents"])

    assert result.exit_code == 0
    assert "Built-in run modes" in result.stdout
    assert "shell" in result.stdout
    assert "coder" in result.stdout


def test_repl_command_uses_chat_session_and_runs(monkeypatch):
    session_sentinel = object()
    _, created_repls, chat_calls, _, _, _ = _setup_stubs(monkeypatch, session_sentinel)

    runner = CliRunner()
    result = runner.invoke(main_module.app, ["repl", "--chat", "conv-repl", "--yolo"])

    assert result.exit_code == 0
    assert chat_calls == ["conv-repl"]
    assert len(created_repls) == 1
    assert created_repls[0].kwargs["session"] is session_sentinel
    assert created_repls[0].kwargs["yolo"] is True
    assert created_repls[0].kwargs["model_platform"] == "cborg"
    assert (
        created_repls[0].kwargs["model_name"]
        == main_module.get_model_provider_settings("cborg").default_model
    )
    assert created_repls[0].run_called is True


def test_repl_command_creates_default_session(monkeypatch):
    session_sentinel = object()
    _, created_repls, chat_calls, _, _, _ = _setup_stubs(monkeypatch, session_sentinel)
    monkeypatch.setattr(main_module, "create_repl_session_id", lambda: "repl-random")

    runner = CliRunner()
    result = runner.invoke(main_module.app, ["repl", "--yolo"])

    assert result.exit_code == 0
    assert chat_calls == ["repl-random"]
    assert len(created_repls) == 1
    assert created_repls[0].kwargs["session"] is session_sentinel
    assert created_repls[0].kwargs["session_id"] == "repl-random"
    assert created_repls[0].kwargs["session_base_id"] == "repl-random"
    assert created_repls[0].run_called is True


def test_repl_command_disable_session_skips_chat_session(monkeypatch):
    session_sentinel = object()
    _, created_repls, chat_calls, _, _, _ = _setup_stubs(monkeypatch, session_sentinel)

    runner = CliRunner()
    result = runner.invoke(main_module.app, ["repl", "--disable-session", "--yolo"])

    assert result.exit_code == 0
    assert chat_calls == []
    assert len(created_repls) == 1
    assert created_repls[0].kwargs["session"] is None
    assert created_repls[0].kwargs["session_id"] is None
    assert created_repls[0].kwargs["session_base_id"] is None


def test_repl_command_rejects_chat_with_disable_session(monkeypatch):
    session_sentinel = object()
    _, created_repls, chat_calls, _, _, _ = _setup_stubs(monkeypatch, session_sentinel)

    runner = CliRunner()
    result = runner.invoke(
        main_module.app,
        ["repl", "--chat", "conv-repl", "--disable-session", "--yolo"],
    )

    assert result.exit_code != 0
    normalized_output = _normalize_cli_output(result.output)
    assert "Use either --chat or --disable-session, not both." in normalized_output
    assert chat_calls == []
    assert created_repls == []
