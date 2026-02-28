import hepagent.agents.bash as bash


def test_create_uses_expected_tools_and_instructions(monkeypatch):
    captured = {}

    class StubAgent:
        def __init__(self, name, instructions, model, tools):
            captured["name"] = name
            captured["instructions"] = instructions
            captured["model"] = model
            captured["tools"] = tools

    sentinel_model = object()
    monkeypatch.setattr(bash, "Agent", StubAgent)
    monkeypatch.setattr(bash, "get_model_provider", lambda **_kwargs: sentinel_model)

    agent = bash.create()

    assert isinstance(agent, StubAgent)
    assert captured["name"] == "Bash Agent"
    assert captured["model"] is sentinel_model
    assert len(captured["tools"]) == 1
    tool_names = [getattr(t, "name", "") for t in captured["tools"]]
    assert any("execute_bash_command" in n for n in tool_names)
    assert "THOUGHT" in captured["instructions"]
    assert "Read AGENTS.md early" in captured["instructions"]


def test_execute_bash_command_truncates_output(monkeypatch):
    class DummyProc:
        def __init__(self):
            self.stdout = "words " * 50
            self.returncode = 0

    monkeypatch.setenv("HEPAGENT_OUTPUT_WORD_LIMIT", "20")
    monkeypatch.setattr(bash.subprocess, "run", lambda *args, **kwargs: DummyProc())

    result = bash.execute_bash_command("echo hi", cwd="")
    assert result["returncode"] == 0
    assert "output truncated" in result["output"]
