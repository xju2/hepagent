import hepagent.agents.bash as bash


def test_create_uses_execute_bash_tool(monkeypatch):
    captured = {}

    class StubAgent:
        def __init__(self, name, instructions, model, tools):
            captured["name"] = name
            captured["instructions"] = instructions
            captured["model"] = model
            captured["tools"] = tools

    sentinel_model = object()
    monkeypatch.setattr(bash, "Agent", StubAgent)
    monkeypatch.setattr(bash, "get_cborg_model_provider", lambda: sentinel_model)

    agent = bash.create()

    assert isinstance(agent, StubAgent)
    assert captured["name"] == "Bash Agent"
    assert captured["model"] is sentinel_model
    assert bash.execute_bash_command in captured["tools"]
    assert "THOUGHT" in captured["instructions"]
