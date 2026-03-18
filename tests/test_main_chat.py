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


def _setup_stubs(monkeypatch, session_sentinel):
    created_apps = []
    chat_calls = []

    monkeypatch.setattr(main_module, "create_skilled_agent", lambda **_: _DummyAgent())

    def _fake_textual_agent(*args, **kwargs):
        app = _FakeTextualAgent(*args, **kwargs)
        created_apps.append(app)
        return app

    monkeypatch.setattr(main_module, "TextualAgent", _fake_textual_agent)
    monkeypatch.setattr(main_module, "AgentAdapter", lambda *args, **kwargs: _FakeAdapter())

    def _fake_create_chat_session(conversation_id: str):
        chat_calls.append(conversation_id)
        return session_sentinel

    monkeypatch.setattr(main_module, "create_chat_session", _fake_create_chat_session)
    return created_apps, chat_calls


def test_run_command_uses_chat_session(monkeypatch):
    session_sentinel = object()
    created_apps, chat_calls = _setup_stubs(monkeypatch, session_sentinel)

    runner = CliRunner()
    result = runner.invoke(main_module.app, ["run", "--chat", "conv-1", "hello"])

    assert result.exit_code == 0
    assert chat_calls == ["conv-1"]
    assert len(created_apps) == 1
    _, kwargs = created_apps[0].run_task_calls[0]
    assert kwargs["session"] is session_sentinel


def test_default_to_run_path_uses_chat_session(monkeypatch):
    session_sentinel = object()
    created_apps, chat_calls = _setup_stubs(monkeypatch, session_sentinel)

    runner = CliRunner()
    result = runner.invoke(main_module.app, ["--chat", "conv-2", "hello"])

    assert result.exit_code == 0
    assert chat_calls == ["conv-2"]
    assert len(created_apps) == 1
    _, kwargs = created_apps[0].run_task_calls[0]
    assert kwargs["session"] is session_sentinel
