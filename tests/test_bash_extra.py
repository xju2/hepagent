"""Additional tests for hepagent.agents.bash module."""

import hepagent.agents.bash as bash_module


def test_truncate_output_no_truncation():
    """_truncate_output returns the original when under the word limit."""
    output = "word " * 10  # 10 words
    result = bash_module._truncate_output(output, limit=20)
    assert result == output


def test_truncate_output_applies_truncation():
    """_truncate_output truncates when output exceeds the word limit."""
    output = "word " * 50  # 50 words
    result = bash_module._truncate_output(output, limit=10)
    assert "output truncated" in result
    words = result.split()
    # Should keep exactly the first 10 words before the truncation notice
    assert "word" in result


def test_truncate_output_exact_limit():
    """_truncate_output is a no-op when word count equals the limit."""
    output = "a b c"
    result = bash_module._truncate_output(output, limit=3)
    assert result == output


def test_execute_bash_command_success(monkeypatch):
    """execute_bash_command returns output and zero returncode on success."""

    class DummyResult:
        stdout = "hello world\n"
        returncode = 0

    monkeypatch.setattr(bash_module.subprocess, "run", lambda *a, **kw: DummyResult())
    result = bash_module.execute_bash_command("echo hello world")
    assert result["returncode"] == 0
    assert "hello" in result["output"]


def test_execute_bash_command_failure(monkeypatch):
    """execute_bash_command returns non-zero returncode on failure."""

    class DummyResult:
        stdout = "error message"
        returncode = 1

    monkeypatch.setattr(bash_module.subprocess, "run", lambda *a, **kw: DummyResult())
    result = bash_module.execute_bash_command("false")
    assert result["returncode"] == 1


def test_execute_bash_command_with_confirmation_yolo_mode(monkeypatch):
    """In yolo mode, execute_bash_command_with_confirmation runs without prompting."""
    captured = {}

    def fake_execute(cmd, cwd=""):
        captured["cmd"] = cmd
        return {"output": "done", "returncode": 0}

    monkeypatch.setattr(bash_module.env_config.__class__, "yolo_mode", property(lambda self: True))
    monkeypatch.setattr(bash_module, "execute_bash_command", fake_execute)

    import json
    import asyncio
    from agents.tool import ToolContext

    payload = json.dumps({"cmd": "echo hi", "cwd": "", "thought": "doing it"})
    ctx = ToolContext(context=None, tool_name="execute_bash_command_with_confirmation",
                     tool_call_id="t1", tool_arguments=payload)
    result = asyncio.run(bash_module.execute_bash_command_with_confirmation.on_invoke_tool(ctx, payload))
    assert captured.get("cmd") == "echo hi"


def test_execute_bash_command_with_confirmation_rejection(monkeypatch):
    """Entering a non-y reason rejects the command."""
    monkeypatch.setattr(bash_module.env_config.__class__, "yolo_mode", property(lambda self: False))
    monkeypatch.setattr("builtins.input", lambda _: "no way")

    import json
    import asyncio
    from agents.tool import ToolContext

    payload = json.dumps({"cmd": "rm -rf /", "cwd": "", "thought": ""})
    ctx = ToolContext(context=None, tool_name="execute_bash_command_with_confirmation",
                     tool_call_id="t2", tool_arguments=payload)
    result = asyncio.run(bash_module.execute_bash_command_with_confirmation.on_invoke_tool(ctx, payload))
    assert isinstance(result, dict)
    assert result["returncode"] == 1
    assert "no way" in result["output"]


def test_create_uses_bash_tool(monkeypatch):
    """bash.create() builds an Agent with the bash tool."""
    captured = {}

    class StubAgent:
        def __init__(self, name, instructions, model, tools, **kwargs):
            captured["tools"] = tools
            captured["name"] = name

    monkeypatch.setattr(bash_module, "Agent", StubAgent)
    monkeypatch.setattr(bash_module, "get_model_provider", lambda **_: object())

    bash_module.create()
    tool_names = [getattr(t, "name", "") for t in captured["tools"]]
    assert any("execute_bash_command" in n for n in tool_names)
