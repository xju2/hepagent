"""The narration channel: what an agent is doing, reported while it does it.

The end-to-end test drives a real `Runner.run` against a scripted model, because
the value of this feature rests entirely on the SDK actually calling the hooks —
a unit test of `ActivityHooks` alone would pass just as happily if nothing ever
invoked it.
"""

from __future__ import annotations

import json

import pytest

from agents import Agent, Runner, function_tool
from agents.items import ModelResponse
from agents.models.interface import Model
from agents.usage import Usage
from hepagent import activity
from hepagent.activity import ActivityEvent, bound_sink, report


class Collector:
    """An `ActivitySink` that keeps everything."""

    def __init__(self) -> None:
        self.events: list[ActivityEvent] = []

    def activity(self, event: ActivityEvent) -> None:
        self.events.append(event)

    def of(self, kind: str) -> list[ActivityEvent]:
        return [e for e in self.events if e.kind == kind]


# ------------------------------------------------------------------ the channel


def test_report_without_a_sink_is_a_no_op():
    """Every CLI path runs with no sink bound; narration must simply vanish."""
    assert activity.current_sink() is None
    report("tool", "Agent", "something happened")  # must not raise


def test_a_sink_only_lives_for_its_block():
    collector = Collector()
    with bound_sink(collector):
        report("tool", "Agent", "inside")
    report("tool", "Agent", "outside")

    assert [e.message for e in collector.events] == ["inside"]
    assert activity.current_sink() is None


def test_a_sink_that_raises_cannot_fail_the_run():
    """Narration is a courtesy. A broken page must not take the analysis down."""

    class Broken:
        def activity(self, event):
            raise RuntimeError("the page exploded")

    with bound_sink(Broken()):
        report("tool", "Agent", "still fine")


def test_long_output_is_clipped_at_the_source():
    """The sink is a progress view held in memory; the archive is on disk."""
    collector = Collector()
    with bound_sink(collector):
        report("result", "Agent", "x" * 5_000, detail="y" * 50_000)

    event = collector.events[0]
    assert len(event.message) < 5_000
    assert len(event.detail) < 50_000
    assert "more characters" in event.detail


def test_a_body_that_only_repeats_the_headline_is_dropped():
    """Otherwise every short answer becomes a fold that tells you what you read."""
    collector = Collector()
    with bound_sink(collector):
        report("message", "Agent", "all done", detail="all done")
        report("message", "Agent", "line one\nline two", detail="line one\nline two")

    assert collector.events[0].detail == ""
    # Two lines are not one line: the fold is where the shape survives.
    assert collector.events[1].detail == "line one\nline two"


def test_a_headline_is_one_line():
    collector = Collector()
    with bound_sink(collector):
        report("message", "Agent", "first line\n\nsecond    line")

    assert collector.events[0].message == "first line second line"


# ------------------------------------------------------- describing a tool call


def test_a_tool_call_headline_names_the_command_it_is_about_to_run():
    from hepagent.agents.activity_hooks import _describe_call

    headline, detail = _describe_call(
        "execute_bash_command_with_confirmation",
        json.dumps({"cmd": "python fit.py", "cwd": "/a", "thought": "fit the model"}),
    )

    assert "python fit.py" in headline
    assert "thought: fit the model" in detail
    assert "/a" in detail


def test_a_tool_call_headline_falls_back_to_any_string_argument():
    """Every tool gets a useful headline, not only the handful named up front."""
    from hepagent.agents.activity_hooks import _describe_call

    headline, _ = _describe_call("shout", json.dumps({"text": "hello"}))

    assert headline == "shout → hello"


def test_a_tool_call_with_no_string_arguments_falls_back_to_its_argument_names():
    from hepagent.agents.activity_hooks import _describe_call

    headline, _ = _describe_call("record_process_inventory", json.dumps({"zzz": 1, "aaa": 2}))

    assert "aaa, zzz" in headline


def test_arguments_that_are_not_json_still_produce_a_line():
    """A provider that hands back malformed arguments must not lose the event."""
    from hepagent.agents.activity_hooks import _describe_call

    headline, detail = _describe_call("some_tool", "{not json")

    assert headline == "some_tool"
    assert detail == "{not json"


# ----------------------------------------------------------------- end to end


@function_tool
def shout(text: str) -> dict:
    """Echo `text` back, loudly."""
    return {"output": text.upper(), "returncode": 0}


class ScriptedModel(Model):
    """Replays a fixed list of `ModelResponse`s, one per turn."""

    def __init__(self, responses: list[ModelResponse]):
        self._responses = list(responses)

    async def get_response(self, *args, **kwargs) -> ModelResponse:
        return self._responses.pop(0)

    def stream_response(self, *args, **kwargs):  # pragma: no cover - unused
        raise NotImplementedError


def _tool_call(name: str, arguments: dict) -> object:
    from openai.types.responses import ResponseFunctionToolCall

    return ResponseFunctionToolCall(
        id="call-1",
        call_id="call-1",
        name=name,
        arguments=json.dumps(arguments),
        type="function_call",
    )


def _message(text: str) -> object:
    from openai.types.responses import ResponseOutputMessage, ResponseOutputText

    return ResponseOutputMessage(
        id="msg-1",
        role="assistant",
        status="completed",
        type="message",
        content=[ResponseOutputText(text=text, type="output_text", annotations=[])],
    )


@pytest.mark.asyncio
async def test_a_run_narrates_its_agent_its_tool_calls_and_their_results():
    """The whole point: a supervising page sees what the terminal used to see."""
    from hepagent.agents.activity_hooks import ACTIVITY_HOOKS

    model = ScriptedModel(
        [
            ModelResponse(
                output=[_tool_call("shout", {"text": "hello"})], usage=Usage(), response_id=None
            ),
            ModelResponse(output=[_message("all done")], usage=Usage(), response_id=None),
        ]
    )
    agent = Agent(name="Test Executor", instructions="do it", model=model, tools=[shout])

    collector = Collector()
    with bound_sink(collector):
        await Runner.run(agent, "go", hooks=ACTIVITY_HOOKS, max_turns=5)

    assert [e.message for e in collector.of("agent")] == ["started", "finished"]
    assert {e.agent for e in collector.events} == {"Test Executor"}

    (call,) = collector.of("tool")
    assert call.message == "shout → hello"

    (result,) = collector.of("result")
    assert "HELLO" in result.detail
    assert result.level == "info"

    assert [e.message for e in collector.of("message")] == ["all done"]


def test_a_dict_returning_tool_is_read_back_out_of_its_repr():
    """The bash tool returns a mapping, and a hook is handed that mapping's repr.

    Left alone the command's output arrives with its newlines spelled `\\n`,
    which is unreadable in exactly the place a reader needs to read it.
    """
    from hepagent.agents.activity_hooks import _as_mapping, _readable_result

    text = str({"output": "line one\nline two\n", "returncode": 0})
    payload = _as_mapping(text)

    assert payload == {"output": "line one\nline two\n", "returncode": 0}
    assert _readable_result(text, payload) == "line one\nline two\n"


def test_a_result_that_is_not_a_mapping_is_left_alone():
    from hepagent.agents.activity_hooks import _as_mapping, _readable_result

    assert _as_mapping("just a string") is None
    assert _readable_result("just a string", None) == "just a string"


@pytest.mark.asyncio
async def test_a_failing_tool_result_is_reported_as_a_warning():
    """A command that returned non-zero is what a physicist most needs to spot.

    The bash tool returns a dict, so the exit code has to be found in a Python
    repr rather than in JSON — matching on the JSON spelling silently passed
    every failed command off as a success.
    """
    from hepagent.agents.activity_hooks import ACTIVITY_HOOKS

    @function_tool
    def failing() -> dict:
        """Always fails."""
        return {"output": "boom", "returncode": 1}

    model = ScriptedModel(
        [
            ModelResponse(output=[_tool_call("failing", {})], usage=Usage(), response_id=None),
            ModelResponse(output=[_message("gave up")], usage=Usage(), response_id=None),
        ]
    )
    agent = Agent(name="Test Executor", instructions="do it", model=model, tools=[failing])

    collector = Collector()
    with bound_sink(collector):
        await Runner.run(agent, "go", hooks=ACTIVITY_HOOKS, max_turns=5)

    (result,) = collector.of("result")
    assert result.level == "warning"
    assert "exit 1" in result.message
    assert result.detail == "boom"


@pytest.mark.asyncio
async def test_a_run_with_no_sink_bound_behaves_exactly_as_before():
    """The CLI cost of the hooks is a thread-local read per turn."""
    from hepagent.agents.activity_hooks import ACTIVITY_HOOKS

    model = ScriptedModel(
        [ModelResponse(output=[_message("done")], usage=Usage(), response_id=None)]
    )
    agent = Agent(name="Test Executor", instructions="do it", model=model)

    result = await Runner.run(agent, "go", hooks=ACTIVITY_HOOKS, max_turns=5)

    assert result.final_output == "done"
