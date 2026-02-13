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
