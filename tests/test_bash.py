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
    # Check that we have a bash command execution tool
    assert len(captured["tools"]) == 1
    tool = captured["tools"][0]
    assert hasattr(tool, "name")
    assert "execute_bash_command" in tool.name
    assert "THOUGHT" in captured["instructions"]
