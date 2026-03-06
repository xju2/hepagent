"""Tests for hepagent.agents.textual_common module (AskUserToolWrapper, CompositeToolWrapper)."""

from types import SimpleNamespace

import hepagent.agents.textual_common as textual_common
from hepagent.agents.textual_common import AskUserToolWrapper, CompositeToolWrapper


class _DummyInputContainer:
    def __init__(self, response: str):
        self._response = response
        self.prompts: list[str] = []

    def request_input(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return self._response


class _DummyTextualApp:
    def __init__(self, response: str):
        self.input_container = _DummyInputContainer(response)
        self.on_message_added = object()
        self._calls: list = []

    def call_from_thread(self, cb):
        self._calls.append(cb)


class _DummyAdapter:
    def __init__(self, response: str = "test response"):
        self.textual_app = _DummyTextualApp(response)
        self.messages: list[dict] = []

    def add_message(self, role: str, content: str):
        self.messages.append({"role": role, "content": content})


# ---------------------------------------------------------------------------
# AskUserToolWrapper
# ---------------------------------------------------------------------------

def test_is_ask_user_tool_true():
    wrapper = AskUserToolWrapper()
    tool = SimpleNamespace(name="ask_user_for_info")
    assert wrapper._is_ask_user_tool(tool) is True


def test_is_ask_user_tool_false():
    wrapper = AskUserToolWrapper()
    tool = SimpleNamespace(name="execute_bash_command")
    assert wrapper._is_ask_user_tool(tool) is False


def test_wrap_tools_replaces_ask_user_tool():
    wrapper = AskUserToolWrapper()
    ask_tool = SimpleNamespace(name="ask_user_for_info")
    other_tool = SimpleNamespace(name="something_else")
    adapter = _DummyAdapter()

    wrapped = wrapper.wrap_tools([ask_tool, other_tool], adapter)

    assert len(wrapped) == 2
    assert wrapped[1] is other_tool
    # The ask_user_for_info tool must have been replaced
    assert wrapped[0] is not ask_tool
    assert "ask_user_for_info" in getattr(wrapped[0], "name", "")


def test_wrap_tools_unchanged_when_no_ask_user_tool():
    wrapper = AskUserToolWrapper()
    tool1 = SimpleNamespace(name="bash_tool")
    tool2 = SimpleNamespace(name="other_tool")
    adapter = _DummyAdapter()

    wrapped = wrapper.wrap_tools([tool1, tool2], adapter)

    assert wrapped == [tool1, tool2]


def test_ask_user_tool_calls_adapter(monkeypatch):
    """The created ask_user tool calls adapter.add_message and returns user input."""
    import asyncio
    import json
    from agents.tool import ToolContext

    adapter = _DummyAdapter(response="  my answer  ")
    wrapper = AskUserToolWrapper()
    tool = wrapper._create_tool(adapter)

    payload = json.dumps({"prompt": "What is your name?", "thought": ""})
    ctx = ToolContext(context=None, tool_name="ask_user_for_info",
                     tool_call_id="tc1", tool_arguments=payload)
    result = asyncio.run(tool.on_invoke_tool(ctx, payload))

    # The response should be stripped
    assert "my answer" in result
    # adapter should have got at least one message with the prompt
    assert any("What is your name?" in m["content"] for m in adapter.messages)


def test_ask_user_tool_with_thought(monkeypatch):
    """When thought is non-empty, it is added as a message."""
    import asyncio
    import json
    from agents.tool import ToolContext

    adapter = _DummyAdapter(response="answer")
    wrapper = AskUserToolWrapper()
    tool = wrapper._create_tool(adapter)

    payload = json.dumps({"prompt": "Question?", "thought": "My reasoning"})
    ctx = ToolContext(context=None, tool_name="ask_user_for_info",
                     tool_call_id="tc2", tool_arguments=payload)
    asyncio.run(tool.on_invoke_tool(ctx, payload))

    thought_messages = [m for m in adapter.messages if "My reasoning" in m["content"]]
    assert len(thought_messages) > 0


# ---------------------------------------------------------------------------
# CompositeToolWrapper
# ---------------------------------------------------------------------------

def test_composite_wrapper_applies_wrappers_in_order():
    """CompositeToolWrapper applies each wrapper to the list in sequence."""

    class TagWrapper:
        def __init__(self, tag: str):
            self.tag = tag

        def wrap_tools(self, tools, adapter):
            return [SimpleNamespace(name=getattr(t, "name", "") + f"_{self.tag}") for t in tools]

    composite = CompositeToolWrapper(TagWrapper("first"), TagWrapper("second"))
    tools = [SimpleNamespace(name="tool")]
    result = composite.wrap_tools(tools, adapter=None)

    assert result[0].name == "tool_first_second"


def test_composite_wrapper_empty_wrappers():
    """CompositeToolWrapper with no wrappers returns tools unchanged."""
    composite = CompositeToolWrapper()
    tools = [SimpleNamespace(name="tool_a")]
    result = composite.wrap_tools(tools, adapter=None)
    assert result[0].name == "tool_a"
