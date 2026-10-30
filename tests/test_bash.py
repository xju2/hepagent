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
    monkeypatch.setattr(bash, "env_config", type("Cfg", (), {"output_word_limit": 20})())

    result = bash.execute_bash_command("for i in $(seq 50); do echo words; done", cwd="")
    assert result["returncode"] == 0
    assert "output truncated" in result["output"]


def test_execute_bash_command_timeout_kills_process_group():
    # The background child holds stdout open; without killpg this would hang.
    result = bash.execute_bash_command("echo start; sleep 30 & sleep 30", timeout=1)
    assert result["returncode"] == 124
    assert "start" in result["output"]
    assert "timeout" in result["output"]


def test_execute_bash_command_runs_bash_not_sh():
    result = bash.execute_bash_command("echo {0..2}")
    assert result["output"].strip() == "0 1 2"
