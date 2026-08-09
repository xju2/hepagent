"""Tests for the transport-agnostic streaming turn loop."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from openai.types.responses.response_text_delta_event import ResponseTextDeltaEvent

from agents.exceptions import MaxTurnsExceeded
from agents.stream_events import RawResponsesStreamEvent, RunItemStreamEvent
from hepagent.web.turn import run_turn


class RecordingUI:
    """Collects everything the turn loop renders."""

    def __init__(self, bash_result: dict[str, Any] | None = None):
        self.deltas: list[str] = []
        self.texts: list[str] = []
        self.tool_calls: list[tuple[str, str]] = []
        self.tool_outputs: list[tuple[str, Any]] = []
        self.agents: list[str] = []
        self.notices: list[tuple[str, str]] = []
        self.bash_proposals: list[tuple[str, str]] = []
        self._bash_result = bash_result or {"output": "ok", "returncode": 0}

    async def on_text_delta(self, delta: str) -> None:
        self.deltas.append(delta)

    async def on_text_done(self, text: str) -> None:
        self.texts.append(text)

    async def on_tool_call(self, name: str, arguments: str) -> None:
        self.tool_calls.append((name, arguments))

    async def on_tool_output(self, name: str, output: Any) -> None:
        self.tool_outputs.append((name, output))

    async def on_agent_updated(self, agent_name: str) -> None:
        self.agents.append(agent_name)

    async def on_notice(self, message: str, *, level: str = "info") -> None:
        self.notices.append((level, message))

    async def run_text_bash_proposal(self, cmd: str, thought: str) -> dict[str, Any]:
        self.bash_proposals.append((cmd, thought))
        return self._bash_result


def _text_event(delta: str) -> RawResponsesStreamEvent:
    return RawResponsesStreamEvent(
        data=ResponseTextDeltaEvent(
            type="response.output_text.delta",
            delta=delta,
            content_index=0,
            item_id="item",
            output_index=0,
            sequence_number=0,
            logprobs=[],
        )
    )


@dataclass
class FakeToolOutputItem:
    output: Any
    type: str = "tool_call_output_item"
    # Mirrors the SDK's function_call_output: a call_id, and no tool name.
    raw_item: dict[str, Any] = field(
        default_factory=lambda: {"call_id": "call-1", "type": "function_call_output"}
    )


@dataclass
class FakeResult:
    """Stands in for the SDK's streaming result object."""

    events: list[Any]
    final_output: Any = None
    agent: Any = "agent"

    @property
    def last_agent(self) -> Any:
        return self.agent

    def to_input_list(self) -> list[dict[str, Any]]:
        return [{"role": "assistant", "content": "prior"}]

    async def stream_events(self):
        for event in self.events:
            yield event


class FakeRunner:
    """Replays a scripted sequence of streamed runs."""

    def __init__(self, results: list[Any], forced: Any = None):
        self.results = list(results)
        self.forced = forced
        self.inputs: list[Any] = []
        self.run_calls = 0

    def run_streamed(self, agent, *, input, context, max_turns, session):
        self.inputs.append(input)
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    async def run(self, agent, *, input, context, max_turns, session):
        self.run_calls += 1
        self.inputs.append(input)
        return self.forced


async def _run(monkeypatch, runner: FakeRunner, ui: RecordingUI, **kwargs: Any):
    monkeypatch.setattr("hepagent.web.turn.Runner", runner)
    defaults: dict[str, Any] = {
        "agent": "agent",
        "user_input": "hello",
        "context": None,
        "max_turns": 40,
        "session": object(),
        "ui": ui,
    }
    defaults.update(kwargs)
    return await run_turn(**defaults)


async def test_streams_tokens_and_reports_final_text(monkeypatch):
    runner = FakeRunner([FakeResult([_text_event("Hel"), _text_event("lo")])])
    ui = RecordingUI()

    outcome = await _run(monkeypatch, runner, ui)

    assert ui.deltas == ["Hel", "lo"]
    assert ui.texts == ["Hello"]
    assert outcome.text == "Hello"
    assert outcome.error is None


@dataclass
class RawCall:
    name: str = "load_skill_details"
    arguments: str = '{"skill_name": "nyx"}'
    call_id: str = "call-1"


@dataclass
class FakeToolCallItem:
    type: str = "tool_call_item"
    raw_item: RawCall = field(default_factory=RawCall)


async def test_tool_calls_and_outputs_are_surfaced(monkeypatch):
    events = [
        RunItemStreamEvent(name="tool_called", item=FakeToolCallItem()),
        RunItemStreamEvent(name="tool_output", item=FakeToolOutputItem(output="skill loaded")),
        _text_event("done"),
    ]
    runner = FakeRunner([FakeResult(events)])
    ui = RecordingUI()

    await _run(monkeypatch, runner, ui)

    assert ui.tool_calls == [("load_skill_details", '{"skill_name": "nyx"}')]
    # The name is recovered from the call_id, since the output item has none.
    assert ui.tool_outputs == [("load_skill_details", "skill loaded")]


async def test_tool_output_without_a_matching_call_falls_back(monkeypatch):
    events = [
        RunItemStreamEvent(
            name="tool_output",
            item=FakeToolOutputItem(output="orphan", raw_item={"call_id": "unknown"}),
        ),
        _text_event("done"),
    ]
    runner = FakeRunner([FakeResult(events)])
    ui = RecordingUI()

    await _run(monkeypatch, runner, ui)

    assert ui.tool_outputs == [("tool", "orphan")]


async def test_bash_tool_output_is_named_so_the_ui_can_deduplicate(monkeypatch):
    """The bridge already renders bash results; the generic renderer must be able
    to recognise and skip them."""
    events = [
        RunItemStreamEvent(
            name="tool_called",
            item=FakeToolCallItem(
                raw_item=RawCall(name="execute_bash_command_with_confirmation", call_id="c9")
            ),
        ),
        RunItemStreamEvent(
            name="tool_output",
            item=FakeToolOutputItem(
                output={"output": "hi", "returncode": 0}, raw_item={"call_id": "c9"}
            ),
        ),
    ]
    runner = FakeRunner([FakeResult(events), FakeResult([_text_event("ok")])])
    ui = RecordingUI()

    await _run(monkeypatch, runner, ui)

    assert ui.tool_outputs[0][0] == "execute_bash_command_with_confirmation"


async def test_session_mode_sends_only_the_raw_prompt(monkeypatch):
    runner = FakeRunner([FakeResult([_text_event("hi")])])
    await _run(monkeypatch, runner, RecordingUI(), session=object())
    assert runner.inputs == ["hello"]


async def test_sessionless_mode_threads_history(monkeypatch):
    runner = FakeRunner([FakeResult([_text_event("hi")])])
    history = [{"role": "user", "content": "earlier"}]

    outcome = await _run(monkeypatch, runner, RecordingUI(), session=None, input_items=history)

    assert runner.inputs[0] == history + [{"role": "user", "content": "hello"}]
    assert outcome.input_items == [{"role": "assistant", "content": "prior"}]


async def test_text_emitted_bash_block_is_executed_and_fed_back(monkeypatch):
    first = FakeResult([_text_event("Run this:\n```bash\nls -la\n```")])
    second = FakeResult([_text_event("All done.")])
    runner = FakeRunner([first, second])
    ui = RecordingUI(bash_result={"output": "file.txt", "returncode": 0})

    outcome = await _run(monkeypatch, runner, ui)

    assert ui.bash_proposals == [("ls -la", "Run this:")]
    assert "The bash command proposed in your previous response has completed." in runner.inputs[1]
    assert "file.txt" in runner.inputs[1]
    assert outcome.text == "All done."


async def test_bash_block_is_ignored_when_a_real_tool_call_happened(monkeypatch):
    events = [
        RunItemStreamEvent(name="tool_output", item=FakeToolOutputItem(output="ran")),
        _text_event("I ran ```bash\nls\n``` for you"),
    ]
    runner = FakeRunner([FakeResult(events)])
    ui = RecordingUI()

    await _run(monkeypatch, runner, ui)

    assert ui.bash_proposals == []


async def test_dead_air_triggers_exactly_one_retry(monkeypatch):
    runner = FakeRunner([FakeResult([]), FakeResult([])])
    ui = RecordingUI()

    await _run(monkeypatch, runner, ui)

    assert len(runner.inputs) == 2
    assert "no assistant text" in runner.inputs[1].lower()
    levels = [level for level, _ in ui.notices]
    assert levels == ["warning", "warning"]


async def test_finalize_signal_forces_a_summary_turn(monkeypatch):
    events = [
        RunItemStreamEvent(name="tool_output", item=FakeToolOutputItem(output="FINALIZE_NOW"))
    ]
    forced = FakeResult([], final_output="Here is the summary.")
    runner = FakeRunner([FakeResult(events)], forced=forced)
    ui = RecordingUI()

    outcome = await _run(monkeypatch, runner, ui)

    assert runner.run_calls == 1
    assert ui.texts == ["Here is the summary."]
    assert outcome.text == "Here is the summary."


async def test_max_turns_exceeded_is_reported_not_raised(monkeypatch):
    runner = FakeRunner([MaxTurnsExceeded("too many")])
    ui = RecordingUI()

    outcome = await _run(monkeypatch, runner, ui)

    assert outcome.error is not None
    assert "Max turns reached" in outcome.error
    assert ui.notices[0][0] == "error"


async def test_unexpected_errors_are_reported_not_raised(monkeypatch):
    runner = FakeRunner([RuntimeError("provider exploded")])
    ui = RecordingUI()

    outcome = await _run(monkeypatch, runner, ui)

    assert outcome.error == "provider exploded"
    assert ui.notices == [("error", "provider exploded")]
