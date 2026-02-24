import asyncio
import json

import hepagent.agents.bash as bash
from agents.tool import ToolContext


def _invoke_tool(cmd: str, thought: str, call_id: str = "t1"):
    payload = json.dumps({"cmd": cmd, "cwd": ".", "thought": thought})
    ctx = ToolContext(
        tool_name=getattr(bash.execute_bash_command_with_confirmation, "name", ""),
        tool_call_id=call_id,
        tool_arguments=payload,
        context=None,
    )
    return asyncio.run(bash.execute_bash_command_with_confirmation.on_invoke_tool(ctx, payload))


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
    assert len(captured["tools"]) == 2
    tool_names = [getattr(t, "name", "") for t in captured["tools"]]
    assert any("execute_bash_command" in n for n in tool_names)
    assert any("get_execution_journal" in n for n in tool_names)
    assert "THOUGHT" in captured["instructions"]
    assert "Read AGENTS.md early" in captured["instructions"]


def test_execute_bash_command_truncates_output(monkeypatch):
    class DummyProc:
        def __init__(self):
            self.stdout = "x" * 50
            self.returncode = 0

    monkeypatch.setenv("HEPAGENT_OUTPUT_CHAR_LIMIT", "20")
    monkeypatch.setattr(bash.subprocess, "run", lambda *args, **kwargs: DummyProc())

    result = bash.execute_bash_command("echo hi", cwd="")
    assert result["returncode"] == 0
    assert "output truncated" in result["output"]


def test_normalize_escaped_heredoc_newlines():
    raw = r"cat > x.txt <<EOF\nhello\nEOF\n"
    norm = bash._normalize_escaped_heredoc_newlines(raw)
    assert "\\n" not in norm
    assert "\nhello\n" in norm


def test_execution_journal_appends_and_snapshots():
    bash._reset_execution_journal_for_tests()
    bash._append_execution_journal("ls -F", ".", 0, "executed")
    bash._append_execution_journal("cat x", ".", 1, "failed")

    entries = bash._snapshot_execution_journal(10)
    assert len(entries) == 2
    assert entries[0]["cmd"] == "ls -F"
    assert entries[1]["status"] == "failed"


def test_yolo_execute_tool_records_result(monkeypatch):
    bash._reset_execution_journal_for_tests()
    monkeypatch.setenv("HEPAGENT_YOLO", "1")
    monkeypatch.setattr(bash, "execute_bash_command", lambda cmd, cwd="": {"output": "ok", "returncode": 0})

    result = _invoke_tool("echo hi", "run")
    assert result["returncode"] == 0
    entries = bash._snapshot_execution_journal(5)
    assert len(entries) >= 1
    assert entries[-1]["status"] == "executed"
