import threading

from textual.app import App

from hepagent.agents.textual import TextualAgent


def test_run_task_sets_task_and_kwargs(monkeypatch):
    called = {}

    def fake_run(self):
        called["run"] = True

    monkeypatch.setattr(App, "run", fake_run)

    app = TextualAgent(model="gpt-4", env={})
    status, result = app.run_task("demo task", context="ctx", foo=1)

    assert called.get("run") is True
    assert app._task == "demo task"
    assert app._task_kwargs == {"context": "ctx", "foo": 1}
    assert (status, result) == (app.exit_status, app.result)


def test_config_proxies_agent_config():
    app = TextualAgent(model="gpt-4", env={})
    assert app.config is app.agent.config


def test_start_new_task_resets_state(monkeypatch):
    """_start_new_task resets the agent state and starts a new thread."""
    threads_started = []

    class FakeThread:
        def __init__(self, target, daemon=False):
            self.target = target
            self.daemon = daemon

        def start(self):
            threads_started.append(self)

    monkeypatch.setattr(threading, "Thread", FakeThread)

    app = TextualAgent(model="gpt-4", env={})
    app._task_kwargs = {}
    app.agent_state = "STOPPED"
    app._i_step = 5
    app.n_steps = 6
    # Avoid UI operations
    app._ui_ready = False

    app._start_new_task("new task")

    assert app._task == "new task"
    assert app._i_step == 0
    assert app.n_steps == 1
    assert app.agent_state == "RUNNING"
    assert len(threads_started) == 1
