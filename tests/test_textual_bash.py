import asyncio
import inspect
import json
from types import SimpleNamespace

import hepagent.agents.textual_bash as textual_bash
from agents.tool import ToolContext


class DummyInputContainer:
    def __init__(self, responses):
        self._responses = list(responses)
        self.prompts = []

    def request_input(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return self._responses.pop(0) if self._responses else ""


class DummyTextualApp:
    def __init__(self, responses):
        self.input_container = DummyInputContainer(responses)
        self.on_message_added = object()
        self.call_log = []

    def call_from_thread(self, callback):
        self.call_log.append(callback)


class DummyAdapter:
    def __init__(self, mode: str, responses):
        self.config = SimpleNamespace(mode=mode)
        self.textual_app = DummyTextualApp(responses)
        self.messages = []

    def add_message(self, role: str, content: str):
        self.messages.append({"role": role, "content": content})


def _invoke_tool(tool, **kwargs):
    if callable(tool):
        return tool(**kwargs)
    on_invoke = getattr(tool, "on_invoke_tool", None)
    if callable(on_invoke):
        payload = json.dumps(kwargs)
        ctx = ToolContext(
            tool_name=getattr(tool, "name", ""),
            tool_call_id="test-call",
            tool_arguments=payload,
            context=None,
        )
        result = on_invoke(ctx, payload)
        if inspect.isawaitable(result):
            return asyncio.run(result)
        return result
    for attr in ("function", "fn", "func"):
        fn = getattr(tool, attr, None)
        if callable(fn):
            return fn(**kwargs)
    raise AssertionError("Tool is not callable")


def test_wrap_tools_replaces_bash_tool():
    wrapper = textual_bash.BashToolWrapper()
    bash_tool = SimpleNamespace(name="execute_bash_command")
    other_tool = SimpleNamespace(name="other_tool")
    adapter = DummyAdapter("confirm", [""])

    wrapped = wrapper.wrap_tools([bash_tool, other_tool], adapter)

    assert wrapped[1] is other_tool
    assert wrapped[0] is not bash_tool
    assert "execute_bash_command" in getattr(wrapped[0], "name", "")


def test_confirm_rejection_returns_cancel_message():
    wrapper = textual_bash.BashToolWrapper()
    adapter = DummyAdapter("confirm", ["nope"])
    tool = wrapper._create_tool(adapter)

    result = _invoke_tool(tool, cmd="echo hi", cwd="", thought="")

    assert result == {"output": textual_bash.TOOL_CANCEL_MESSAGE, "returncode": 1}
    assert any(
        msg["role"] == "user" and "Rejected: nope" in msg["content"] for msg in adapter.messages
    )
    assert adapter.textual_app.input_container.prompts


def test_confirm_approval_executes_and_logs(monkeypatch):
    wrapper = textual_bash.BashToolWrapper()
    adapter = DummyAdapter("confirm", [""])
    tool = wrapper._create_tool(adapter)
    captured = {}

    def fake_execute(cmd, cwd=""):
        captured["cmd"] = cmd
        captured["cwd"] = cwd
        return {"output": "x" * (textual_bash.OUTPUT_TRUNCATE_LENGTH + 5), "returncode": 0}

    monkeypatch.setattr(textual_bash, "execute_bash_command", fake_execute)

    result = _invoke_tool(tool, cmd="echo hi", cwd="/tmp", thought="do it")

    assert result["returncode"] == 0
    assert captured == {"cmd": "echo hi", "cwd": "/tmp"}
    assert any(msg["role"] == "user" and "Approved" in msg["content"] for msg in adapter.messages)
    truncated = "x" * textual_bash.OUTPUT_TRUNCATE_LENGTH + "..."
    assert any(
        msg["role"] == "system"
        and "Truncated Output:" in msg["content"]
        and truncated in msg["content"]
        for msg in adapter.messages
    )
