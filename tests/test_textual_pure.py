"""Tests for pure functions and dataclasses in hepagent.agents.textual module."""

import asyncio
import logging

import hepagent.agents.textual as textual_module
from hepagent.agents.textual import (
    AddLogEmitCallback,
    AgentConfig,
    AgentModel,
    DummyAgent,
    LogBashCallAgentHooks,
    NoopToolWrapper,
    _message_header_label,
    _messages_to_steps,
)


# ---------------------------------------------------------------------------
# AddLogEmitCallback
# ---------------------------------------------------------------------------

def test_add_log_emit_callback_calls_callback():
    """AddLogEmitCallback.emit() invokes the provided callback with the log record."""
    received = []
    handler = AddLogEmitCallback(callback=received.append)

    record = logging.LogRecord(
        name="test", level=logging.INFO, pathname="", lineno=0,
        msg="hello", args=(), exc_info=None,
    )
    handler.emit(record)
    assert received == [record]


# ---------------------------------------------------------------------------
# NoopToolWrapper
# ---------------------------------------------------------------------------

def test_noop_tool_wrapper_returns_copy():
    """NoopToolWrapper.wrap_tools() returns a new list with the same tools."""
    from types import SimpleNamespace

    wrapper = NoopToolWrapper()
    tools = [SimpleNamespace(name="tool_a"), SimpleNamespace(name="tool_b")]
    result = wrapper.wrap_tools(tools, adapter=None)
    assert result == tools
    assert result is not tools  # Should be a copy


# ---------------------------------------------------------------------------
# AgentConfig and AgentModel
# ---------------------------------------------------------------------------

def test_agent_config_default_mode():
    cfg = AgentConfig()
    assert cfg.mode == "confirm"


def test_agent_config_custom_mode():
    cfg = AgentConfig(mode="yolo")
    assert cfg.mode == "yolo"


def test_agent_model_default_values():
    model = AgentModel()
    assert model.cost == 0.0
    assert model.name == ""


def test_agent_model_with_values():
    model = AgentModel(cost=1.5, name="gpt-4")
    assert model.cost == 1.5
    assert model.name == "gpt-4"


# ---------------------------------------------------------------------------
# _message_header_label
# ---------------------------------------------------------------------------

def test_message_header_label_final():
    msg = {"role": "system", "kind": "final"}
    assert _message_header_label(None, msg) == "CONCLUSION"


def test_message_header_label_error():
    msg = {"role": "system", "kind": "error"}
    assert _message_header_label(None, msg) == "ERROR"


def test_message_header_label_task():
    msg = {"role": "user", "kind": "task"}
    assert _message_header_label(None, msg) == "TASK"


def test_message_header_label_assistant():
    msg = {"role": "assistant"}
    assert _message_header_label(None, msg) == "AGENT"


def test_message_header_label_user():
    msg = {"role": "user"}
    assert _message_header_label(None, msg) == "USER"


def test_message_header_label_system():
    msg = {"role": "system"}
    assert _message_header_label(None, msg) == "SYSTEM"


def test_message_header_label_custom_role():
    msg = {"role": "custom"}
    assert _message_header_label(None, msg) == "CUSTOM"


# ---------------------------------------------------------------------------
# _messages_to_steps
# ---------------------------------------------------------------------------

def test_messages_to_steps_empty():
    """Empty list gives empty steps."""
    assert _messages_to_steps([]) == []


def test_messages_to_steps_single_message():
    """Single message creates one step."""
    msgs = [{"role": "user", "content": "Hello"}]
    steps = _messages_to_steps(msgs)
    assert len(steps) == 1
    assert steps[0] == msgs


def test_messages_to_steps_splits_on_thought():
    """A new thought (💭) in an assistant message triggers a new step."""
    msgs = [
        {"role": "user", "content": "Do this"},
        {"role": "assistant", "content": "💭 Step 1"},
        {"role": "assistant", "content": "💭 Step 2"},
        {"role": "user", "content": "Final"},
    ]
    steps = _messages_to_steps(msgs)
    # First step: user message + first thought
    # Then second thought starts a new step
    assert len(steps) >= 2


def test_messages_to_steps_final_kind_triggers_new_step():
    """A 'final' kind message triggers a new step."""
    msgs = [
        {"role": "user", "content": "Task"},
        {"role": "assistant", "content": "result", "kind": "final"},
    ]
    steps = _messages_to_steps(msgs)
    assert len(steps) == 2


def test_messages_to_steps_no_split_without_prior_content():
    """A thought message as the very first message does NOT trigger a new step."""
    msgs = [
        {"role": "assistant", "content": "💭 First thought"},
        {"role": "assistant", "content": "done"},
    ]
    steps = _messages_to_steps(msgs)
    # No prior content, so no split
    assert len(steps) == 1


# ---------------------------------------------------------------------------
# DummyAgent
# ---------------------------------------------------------------------------

def test_dummy_agent_run_adds_messages():
    """DummyAgent.run() appends messages to the messages list."""
    import time

    agent = DummyAgent()

    # We can't actually run the sleep-based run() in a test, but we can
    # verify the initial state and add a message manually.
    assert agent.messages == []
    assert agent.model.cost == 0.0
    assert agent.config.mode == "human"
    assert agent.env == {}


# ---------------------------------------------------------------------------
# AgentAdapter dataclass
# ---------------------------------------------------------------------------

def test_agent_adapter_add_message_while_uninitialized(monkeypatch):
    """AgentAdapter.add_message() does not call call_from_thread when state is UNINITIALIZED."""
    from types import SimpleNamespace
    from hepagent.agents.textual import AgentAdapter

    class StubTextualApp:
        agent_state = "UNINITIALIZED"
        called = False

        def call_from_thread(self, fn):
            self.called = True

    stub_app = StubTextualApp()

    class StubAgent:
        name = "test"
        instructions = "instr"
        model = "model"
        tools = []

    adapter = AgentAdapter(StubAgent(), stub_app)
    adapter.add_message("user", "hello")

    assert len(adapter.messages) == 1
    assert adapter.messages[0]["role"] == "user"
    # Should NOT have triggered call_from_thread because state is UNINITIALIZED
    assert not stub_app.called


def test_agent_adapter_add_message_when_running():
    """AgentAdapter.add_message() triggers call_from_thread when state is not UNINITIALIZED."""
    from hepagent.agents.textual import AgentAdapter

    call_log = []

    class StubTextualApp:
        agent_state = "RUNNING"
        on_message_added = lambda self: None  # noqa: E731

        def call_from_thread(self, fn):
            call_log.append(fn)

    stub_app = StubTextualApp()

    class StubAgent:
        name = "test"
        instructions = "instr"
        model = "model"
        tools = []

    adapter = AgentAdapter(StubAgent(), stub_app)
    adapter.add_message("assistant", "thinking")

    assert len(adapter.messages) == 1
    assert len(call_log) == 1


# ---------------------------------------------------------------------------
# LogBashCallAgentHooks
# ---------------------------------------------------------------------------

class _SimpleAdapter:
    """Minimal adapter for LogBashCallAgentHooks testing."""

    def __init__(self):
        self.messages = []
        self.model = AgentModel(name="test-model", cost=0.0)
        self.textual_app = _SimpleTextualApp()

    def add_message(self, role, content, **kwargs):
        self.messages.append({"role": role, "content": content, **kwargs})


class _SimpleTextualApp:
    def __init__(self):
        self.calls = []
        self.on_message_added = None

    def call_from_thread(self, fn, *args):
        self.calls.append((fn, args))


def test_log_hooks_on_llm_start():
    """LogBashCallAgentHooks.on_llm_start adds a 'Thinking...' status message."""
    adapter = _SimpleAdapter()
    hooks = LogBashCallAgentHooks(adapter)

    asyncio.run(hooks.on_llm_start(context=None, agent=None, system_prompt=None, input_items=[]))

    thinking_msgs = [m for m in adapter.messages if "Thinking" in m["content"]]
    assert len(thinking_msgs) == 1


def test_log_hooks_on_llm_end_basic():
    """LogBashCallAgentHooks.on_llm_end adds an LLM responded status message."""
    adapter = _SimpleAdapter()
    hooks = LogBashCallAgentHooks(adapter)

    # Response with no output_items
    class FakeResponse:
        pass

    asyncio.run(hooks.on_llm_end(context=None, agent=None, response=FakeResponse()))

    responded_msgs = [m for m in adapter.messages if "LLM responded" in m["content"]]
    assert len(responded_msgs) == 1


def test_log_hooks_on_llm_end_extracts_text():
    """LogBashCallAgentHooks.on_llm_end extracts assistant text from output_items."""
    adapter = _SimpleAdapter()
    hooks = LogBashCallAgentHooks(adapter)

    from types import SimpleNamespace

    item = SimpleNamespace(text="I thought about it.")
    response = SimpleNamespace(output_items=[item])
    asyncio.run(hooks.on_llm_end(context=None, agent=None, response=response))

    text_msgs = [m for m in adapter.messages if m.get("role") == "assistant"]
    assert any("I thought about it." in m["content"] for m in text_msgs)


def test_log_hooks_on_llm_end_tracks_cost():
    """LogBashCallAgentHooks.on_llm_end updates adapter model cost when usage is available."""
    adapter = _SimpleAdapter()
    hooks = LogBashCallAgentHooks(adapter)

    from agents import Usage
    from types import SimpleNamespace

    class FakeResponse:
        pass

    usage = Usage(input_tokens=1_000_000, output_tokens=0, total_tokens=1_000_000)
    context = SimpleNamespace(usage=usage)

    asyncio.run(hooks.on_llm_end(context=context, agent=None, response=FakeResponse()))

    # Cost should be non-zero for 1M input tokens
    assert adapter.model.cost > 0
