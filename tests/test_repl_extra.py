"""Additional tests for hepagent.agents.repl module."""

import hepagent.agents.repl as repl
from hepagent.agents.repl import (
    RawResponsesStreamEvent,
    RunItemStreamEvent,
    AgentUpdatedStreamEvent,
)


def test_configure_readline_does_not_raise():
    """_configure_readline should silently succeed or fail without raising."""
    repl._configure_readline()  # Must not raise


def test_read_user_input(monkeypatch):
    """_read_user_input delegates to input() and returns its value."""
    monkeypatch.setattr("builtins.input", lambda prompt: "hello")
    result = repl._read_user_input(" > ")
    assert result == "hello"


def test_format_tool_output_short_dict():
    """_format_tool_output serialises short dicts to JSON."""
    output = {"returncode": 0, "output": "ok"}
    result = repl._format_tool_output(output)
    assert "returncode" in result
    assert "ok" in result


def test_format_tool_output_short_string():
    """_format_tool_output returns short strings as JSON-encoded."""
    result = repl._format_tool_output("short")
    assert "short" in result


def test_format_tool_output_truncates_long_output():
    """_format_tool_output truncates output longer than OUTPUT_TRUNCATE_LENGTH."""
    long_str = "x" * (repl.OUTPUT_TRUNCATE_LENGTH + 500)
    result = repl._format_tool_output(long_str)
    assert "tool output truncated" in result
    assert len(result) < len(long_str)


def test_format_tool_output_non_serialisable():
    """_format_tool_output falls back to str() for non-JSON-serialisable objects."""

    class Unserializable:
        def __repr__(self):
            return "Unserializable()"

    result = repl._format_tool_output(Unserializable())
    assert "Unserializable" in result


def test_run_demo_loop_non_streaming(monkeypatch):
    """run_demo_loop works in non-streaming mode (stream=False)."""
    import asyncio

    class FakeResult:
        final_output = "done"

        def to_input_list(self):
            return [{"role": "assistant", "content": "done"}]

        @property
        def last_agent(self):
            return FakeAgent()

    class FakeAgent:
        name = "Test"

    class FakeRunner:
        @staticmethod
        async def run(agent, input, context=None, max_turns=None):
            return FakeResult()

    prompts = iter(["do something", "/exit"])
    monkeypatch.setattr(repl, "_read_user_input", lambda _: next(prompts))
    monkeypatch.setattr(repl, "Runner", FakeRunner)

    asyncio.run(repl.run_demo_loop(FakeAgent(), stream=False, context=None, max_turns=5))


def test_run_demo_loop_exits_on_quit(monkeypatch):
    """run_demo_loop exits when user types /quit."""
    import asyncio

    class FakeAgent:
        name = "Agent"

    prompts = iter(["/quit"])
    monkeypatch.setattr(repl, "_read_user_input", lambda _: next(prompts))

    asyncio.run(repl.run_demo_loop(FakeAgent(), stream=False))


def test_run_demo_loop_skips_empty_input(monkeypatch):
    """run_demo_loop ignores empty input lines."""
    import asyncio

    class FakeAgent:
        name = "Agent"

    prompts = iter(["", "/exit"])
    monkeypatch.setattr(repl, "_read_user_input", lambda _: next(prompts))

    asyncio.run(repl.run_demo_loop(FakeAgent(), stream=False))


def test_run_demo_loop_handles_eof(monkeypatch):
    """run_demo_loop exits cleanly on EOFError."""
    import asyncio

    class FakeAgent:
        name = "Agent"

    def raise_eof(_):
        raise EOFError

    monkeypatch.setattr(repl, "_read_user_input", raise_eof)

    asyncio.run(repl.run_demo_loop(FakeAgent(), stream=False))


def test_run_demo_loop_handles_max_turns_exceeded(monkeypatch):
    """run_demo_loop handles MaxTurnsExceeded gracefully."""
    import asyncio

    from agents.exceptions import MaxTurnsExceeded

    class FakeAgent:
        name = "Agent"

    class FakeRunner:
        @staticmethod
        async def run(agent, input, context=None, max_turns=None):
            raise MaxTurnsExceeded(max_turns or 0)

    prompts = iter(["do something", "/exit"])
    monkeypatch.setattr(repl, "_read_user_input", lambda _: next(prompts))
    monkeypatch.setattr(repl, "Runner", FakeRunner)

    asyncio.run(repl.run_demo_loop(FakeAgent(), stream=False, max_turns=1))


def test_tool_output_contains_finalize_signal_non_serialisable():
    """_tool_output_contains_finalize_signal handles non-JSON-serialisable objects."""

    class Unserializable:
        def __str__(self):
            return "FINALIZE_NOW signal in string representation"

    result = repl._tool_output_contains_finalize_signal(Unserializable())
    assert result is True


def test_tool_output_not_finalize_non_serialisable():
    """_tool_output_contains_finalize_signal returns False for non-signal unserializable output."""

    class Unserializable:
        def __str__(self):
            return "regular output"

    result = repl._tool_output_contains_finalize_signal(Unserializable())
    assert result is False


def test_run_demo_loop_streaming_with_finalize_signal(monkeypatch):
    """Streaming loop triggers forced final summary when FINALIZE_NOW is detected."""
    import asyncio

    from agents.items import ToolCallOutputItem
    from agents.stream_events import RawResponsesStreamEvent, RunItemStreamEvent
    from unittest.mock import MagicMock

    from agents import Agent, OpenAIChatCompletionsModel

    client = MagicMock()
    model = OpenAIChatCompletionsModel(model="test-model", openai_client=client)
    test_agent = Agent(name="test", model=model, instructions="test")

    raw_item = MagicMock()
    tool_output_item = ToolCallOutputItem(
        agent=test_agent,
        raw_item=raw_item,
        output={"output": "FINALIZE_NOW: done"},
    )
    tool_output_evt = RunItemStreamEvent(name="tool_output", item=tool_output_item)

    class FakeAgent:
        name = "Agent"

    class FakeFinalResult:
        final_output = "Final summary."

        def to_input_list(self):
            return [{"role": "assistant", "content": "Final summary."}]

        @property
        def last_agent(self):
            return FakeAgent()

    class FakeStreamResult:
        last_agent = FakeAgent()

        async def stream_events(self):
            yield tool_output_evt
            # No text delta - triggers finalize path

        def to_input_list(self):
            return [{"role": "assistant", "content": ""}]

    class FakeRunner:
        @staticmethod
        def run_streamed(agent, input=None, context=None, max_turns=None):
            return FakeStreamResult()

        @staticmethod
        async def run(agent, input, context=None, max_turns=None):
            return FakeFinalResult()

    prompts = iter(["finalize task", "/exit"])
    monkeypatch.setattr(repl, "_read_user_input", lambda _: next(prompts))
    monkeypatch.setattr(repl, "Runner", FakeRunner)

    asyncio.run(repl.run_demo_loop(FakeAgent(), stream=True))


def _make_agent_and_model():
    """Create a minimal Agent for use in stream event construction."""
    from unittest.mock import MagicMock

    from agents import Agent, OpenAIChatCompletionsModel

    client = MagicMock()
    model = OpenAIChatCompletionsModel(model="test-model", openai_client=client)
    return Agent(name="test", model=model, instructions="test")


def test_run_demo_loop_streaming_with_text_output(monkeypatch):
    """Streaming loop processes text events correctly."""
    import asyncio

    from agents.stream_events import RawResponsesStreamEvent
    from openai.types.responses.response_text_delta_event import ResponseTextDeltaEvent

    class FakeAgent:
        name = "Agent"

    text_delta_evt = ResponseTextDeltaEvent(
        delta="Hello ",
        type="response.output_text.delta",
        event_id="e1",
        item_id="i1",
        output_index=0,
        content_index=0,
        logprobs=[],
        sequence_number=0,
    )
    raw_stream_evt = RawResponsesStreamEvent(data=text_delta_evt)

    class FakeResult:
        last_agent = FakeAgent()

        async def stream_events(self):
            yield raw_stream_evt

        def to_input_list(self):
            return [{"role": "assistant", "content": "Hello "}]

    class FakeRunner:
        @staticmethod
        def run_streamed(agent, input=None, context=None, max_turns=None):
            return FakeResult()

    prompts = iter(["hello", "/exit"])
    monkeypatch.setattr(repl, "_read_user_input", lambda _: next(prompts))
    monkeypatch.setattr(repl, "Runner", FakeRunner)

    asyncio.run(repl.run_demo_loop(FakeAgent(), stream=True, context=None))


def test_run_demo_loop_streaming_with_tool_events(monkeypatch):
    """Streaming loop processes tool_call and tool_call_output events."""
    import asyncio

    from agents.items import ToolCallItem, ToolCallOutputItem
    from agents.stream_events import RawResponsesStreamEvent, RunItemStreamEvent
    from openai.types.responses.response_text_delta_event import ResponseTextDeltaEvent
    from unittest.mock import MagicMock

    test_agent = _make_agent_and_model()
    raw_item = MagicMock()

    tool_call_item = ToolCallItem(agent=test_agent, raw_item=raw_item)
    tool_call_evt = RunItemStreamEvent(name="tool_called", item=tool_call_item)

    tool_output_item = ToolCallOutputItem(
        agent=test_agent, raw_item=raw_item, output={"output": "done"}
    )
    tool_output_evt = RunItemStreamEvent(name="tool_output", item=tool_output_item)

    # Also add a text delta so saw_text=True and no retry loop
    text_delta_evt = ResponseTextDeltaEvent(
        delta="ok",
        type="response.output_text.delta",
        event_id="e2",
        item_id="i2",
        output_index=0,
        content_index=0,
        logprobs=[],
        sequence_number=0,
    )
    raw_text_evt = RawResponsesStreamEvent(data=text_delta_evt)

    class FakeAgent:
        name = "Agent"

    class FakeResult:
        last_agent = FakeAgent()

        async def stream_events(self):
            yield tool_call_evt
            yield tool_output_evt
            yield raw_text_evt

        def to_input_list(self):
            return [{"role": "assistant", "content": "ok"}]

    class FakeRunner:
        @staticmethod
        def run_streamed(agent, input=None, context=None, max_turns=None):
            return FakeResult()

    prompts = iter(["run tool", "/exit"])
    monkeypatch.setattr(repl, "_read_user_input", lambda _: next(prompts))
    monkeypatch.setattr(repl, "Runner", FakeRunner)

    asyncio.run(repl.run_demo_loop(FakeAgent(), stream=True))


def test_run_demo_loop_streaming_with_agent_updated(monkeypatch):
    """Streaming loop handles AgentUpdatedStreamEvent."""
    import asyncio

    from agents.stream_events import AgentUpdatedStreamEvent, RawResponsesStreamEvent
    from openai.types.responses.response_text_delta_event import ResponseTextDeltaEvent

    new_agent_obj = _make_agent_and_model()
    updated_evt = AgentUpdatedStreamEvent(new_agent=new_agent_obj)

    text_delta_evt = ResponseTextDeltaEvent(
        delta="done",
        type="response.output_text.delta",
        event_id="e3",
        item_id="i3",
        output_index=0,
        content_index=0,
        logprobs=[],
        sequence_number=0,
    )
    raw_text_evt = RawResponsesStreamEvent(data=text_delta_evt)

    class FakeAgent:
        name = "Agent"

    class FakeResult:
        last_agent = FakeAgent()

        async def stream_events(self):
            yield updated_evt
            yield raw_text_evt

        def to_input_list(self):
            return [{"role": "assistant", "content": "done"}]

    class FakeRunner:
        @staticmethod
        def run_streamed(agent, input=None, context=None, max_turns=None):
            return FakeResult()

    prompts = iter(["do task", "/exit"])
    monkeypatch.setattr(repl, "_read_user_input", lambda _: next(prompts))
    monkeypatch.setattr(repl, "Runner", FakeRunner)

    asyncio.run(repl.run_demo_loop(FakeAgent(), stream=True))
