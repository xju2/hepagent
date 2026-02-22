from hepagent.agents import repl
import asyncio


def test_tool_output_contains_finalize_signal_in_string():
    assert repl._tool_output_contains_finalize_signal("FINALIZE_NOW: stop tool-calling")


def test_tool_output_contains_finalize_signal_in_dict():
    output = {"output": "x\nFINALIZE_NOW: stop tool-calling and produce the final summary."}
    assert repl._tool_output_contains_finalize_signal(output)


def test_tool_output_contains_finalize_signal_negative():
    assert not repl._tool_output_contains_finalize_signal({"output": "normal tool output"})


def test_run_demo_loop_retries_once_on_dead_air(monkeypatch):
    calls: list[list[dict]] = []

    class FakeAgent:
        name = "Fake Agent"

    class FakeResult:
        def __init__(self, agent, input_items):
            self.last_agent = agent
            self._input_items = input_items

        async def stream_events(self):
            if False:
                yield None

        def to_input_list(self):
            return list(self._input_items)

    class FakeRunner:
        @staticmethod
        def run_streamed(agent, input=None, context=None, max_turns=None):
            calls.append(list(input or []))
            return FakeResult(agent, list(input or []))

    # First user turn then exit.
    prompts = iter(["task", "/exit"])
    monkeypatch.setattr(repl, "_read_user_input", lambda _p: next(prompts))
    monkeypatch.setattr(repl, "Runner", FakeRunner)

    asyncio.run(repl.run_demo_loop(FakeAgent(), stream=True, context=None, max_turns=5))

    # First streamed call is the original task, second is one dead-air retry.
    assert len(calls) >= 2
    assert any(item.get("content") == "task" for item in calls[0] if isinstance(item, dict))
    assert any(item.get("content") == repl.DEAD_AIR_RETRY_PROMPT for item in calls[1] if isinstance(item, dict))
