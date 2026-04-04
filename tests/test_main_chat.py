from types import SimpleNamespace

from typer.testing import CliRunner

import hepagent.main as main_module


class _DummyAgent:
    def __init__(self):
        self.name = "dummy"
        self.instructions = "instructions"
        self.model = "test-model"
        self.tools = []


class _FakeAdapter:
    def __init__(self):
        self.config = SimpleNamespace(mode="confirm")


class _FakeTextualAgent:
    def __init__(self, model, env):
        self.model = model
        self.env = env
        self.agent = _FakeAdapter()
        self.run_task_calls = []

    def run_task(self, task: str, **kwargs):
        self.run_task_calls.append((task, kwargs))
        return "success", "ok"


class _FakeCliRepl:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.run_called = False

    def run(self):
        self.run_called = True


def _setup_stubs(monkeypatch, session_sentinel):
    created_apps = []
    created_repls = []
    chat_calls = []
    skilled_calls = []
    role_calls = []

    def _fake_create_skilled_agent(**kwargs):
        skilled_calls.append(kwargs)
        return _DummyAgent()

    def _fake_create_role_agent(**kwargs):
        role_calls.append(kwargs)
        return _DummyAgent()

    monkeypatch.setattr(main_module, "create_skilled_agent", _fake_create_skilled_agent)
    monkeypatch.setattr(main_module, "create_role_agent", _fake_create_role_agent)

    def _fake_textual_agent(*args, **kwargs):
        app = _FakeTextualAgent(*args, **kwargs)
        created_apps.append(app)
        return app

    def _fake_cli_repl(**kwargs):
        repl = _FakeCliRepl(**kwargs)
        created_repls.append(repl)
        return repl

    monkeypatch.setattr(main_module, "TextualAgent", _fake_textual_agent)
    monkeypatch.setattr(main_module, "CliRepl", _fake_cli_repl)
    monkeypatch.setattr(main_module, "AgentAdapter", lambda *args, **kwargs: _FakeAdapter())

    def _fake_create_chat_session(conversation_id: str):
        chat_calls.append(conversation_id)
        return session_sentinel

    monkeypatch.setattr(main_module, "create_chat_session", _fake_create_chat_session)
    return created_apps, created_repls, chat_calls, skilled_calls, role_calls


def test_run_command_uses_chat_session(monkeypatch):
    session_sentinel = object()
    created_apps, _, chat_calls, _, _ = _setup_stubs(monkeypatch, session_sentinel)

    runner = CliRunner()
    result = runner.invoke(main_module.app, ["run", "--chat", "conv-1", "hello"])

    assert result.exit_code == 0
    assert chat_calls == ["conv-1"]
    assert len(created_apps) == 1
    _, kwargs = created_apps[0].run_task_calls[0]
    assert kwargs["session"] is session_sentinel


def test_default_to_run_path_uses_chat_session(monkeypatch):
    session_sentinel = object()
    created_apps, _, chat_calls, _, _ = _setup_stubs(monkeypatch, session_sentinel)

    runner = CliRunner()
    result = runner.invoke(main_module.app, ["--chat", "conv-2", "hello"])

    assert result.exit_code == 0
    assert chat_calls == ["conv-2"]
    assert len(created_apps) == 1
    _, kwargs = created_apps[0].run_task_calls[0]
    assert kwargs["session"] is session_sentinel


def test_run_shell_mode_uses_role_agent(monkeypatch):
    session_sentinel = object()
    created_apps, _, _, skilled_calls, role_calls = _setup_stubs(monkeypatch, session_sentinel)

    runner = CliRunner()
    result = runner.invoke(main_module.app, ["run", "-a", "shell", "check disk space"])

    assert result.exit_code == 0
    assert len(skilled_calls) == 0
    assert len(role_calls) == 1
    assert role_calls[0]["role_name"] == "shell"
    task, kwargs = created_apps[0].run_task_calls[0]
    assert task == "check disk space"
    assert kwargs["context"].agent_name == "shell"


def test_run_describe_mode_uses_role_agent(monkeypatch):
    session_sentinel = object()
    created_apps, _, _, skilled_calls, role_calls = _setup_stubs(monkeypatch, session_sentinel)

    runner = CliRunner()
    result = runner.invoke(main_module.app, ["run", "-a", "shell_describer", "ls -la | wc -l"])

    assert result.exit_code == 0
    assert len(skilled_calls) == 0
    assert len(role_calls) == 1
    assert role_calls[0]["role_name"] == "shell_describer"
    task, kwargs = created_apps[0].run_task_calls[0]
    assert task == "ls -la | wc -l"
    assert kwargs["context"].agent_name == "shell_describer"


def test_run_code_mode_uses_role_agent(monkeypatch):
    session_sentinel = object()
    created_apps, _, _, skilled_calls, role_calls = _setup_stubs(monkeypatch, session_sentinel)

    runner = CliRunner()
    result = runner.invoke(main_module.app, ["run", "-a", "coder", "write python to count xju"])

    assert result.exit_code == 0
    assert len(skilled_calls) == 0
    assert len(role_calls) == 1
    assert role_calls[0]["role_name"] == "coder"
    task, kwargs = created_apps[0].run_task_calls[0]
    assert task == "write python to count xju"
    assert kwargs["context"].agent_name == "coder"


def test_run_default_mode_uses_skilled_agent(monkeypatch):
    session_sentinel = object()
    created_apps, _, _, skilled_calls, role_calls = _setup_stubs(monkeypatch, session_sentinel)

    runner = CliRunner()
    result = runner.invoke(main_module.app, ["run", "run a nyx simulation"])

    assert result.exit_code == 0
    assert len(skilled_calls) == 1
    assert len(role_calls) == 0
    task, kwargs = created_apps[0].run_task_calls[0]
    assert task == "run a nyx simulation"
    assert kwargs["context"].agent_name == "scientist"


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
    _, created_repls, chat_calls, _, _ = _setup_stubs(monkeypatch, session_sentinel)

    runner = CliRunner()
    result = runner.invoke(main_module.app, ["repl", "--chat", "conv-repl", "--yolo"])

    assert result.exit_code == 0
    assert chat_calls == ["conv-repl"]
    assert len(created_repls) == 1
    assert created_repls[0].kwargs["session"] is session_sentinel
    assert created_repls[0].kwargs["yolo"] is True
    assert created_repls[0].run_called is True
