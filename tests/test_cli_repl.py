import asyncio
import inspect
import io
import json
from types import SimpleNamespace

from openai.types.responses.response_text_delta_event import ResponseTextDeltaEvent

import hepagent.agents.cli_repl as cli_repl
from agents import Agent
from agents.tool import ToolContext


class FakePromptSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.prompts = []

    def prompt(self, message, **kwargs):
        self.prompts.append(message)
        return self.responses.pop(0) if self.responses else ""


def _make_console():
    return cli_repl.Console(file=io.StringIO(), force_terminal=False, color_system=None)


def _make_agent(name="scientist"):
    return Agent(name=name, instructions=f"{name} instructions", model="test-model", tools=[])


def _make_repl(**kwargs):
    defaults = {
        "agent_name": "scientist",
        "agent_factory": lambda name: _make_agent(name),
        "available_agents": {"scientist": "default", "coder": "code agent"},
        "context": SimpleNamespace(agent_name="scientist"),
        "max_turns": 5,
        "prompt_session": FakePromptSession([]),
        "console": _make_console(),
    }
    defaults.update(kwargs)
    return cli_repl.CliRepl(**defaults)


def _invoke_tool(tool, **kwargs):
    payload = json.dumps(kwargs)
    ctx = ToolContext(
        tool_name=getattr(tool, "name", ""),
        tool_call_id="test-call",
        tool_arguments=payload,
        context=None,
    )
    result = tool.on_invoke_tool(ctx, payload)
    if inspect.isawaitable(result):
        return asyncio.run(result)
    return result


def test_parse_slash_command():
    parsed = cli_repl.parse_slash_command('/agent "shell coder"')
    assert parsed == cli_repl.SlashCommand(name="agent", args=("shell coder",))
    assert cli_repl.parse_slash_command("plain text") is None


def test_switch_agent_updates_context_and_agent():
    calls = []
    repl = _make_repl(agent_factory=lambda name: calls.append(name) or _make_agent(name))

    repl.switch_agent("coder")

    assert repl.agent_name == "coder"
    assert repl.context.agent_name == "coder"
    assert calls == ["scientist", "coder"]


def test_clear_session_state_rotates_chat_session():
    created = []
    repl = _make_repl(
        session=object(),
        chat_base_id="conv",
        session_factory=lambda cid: created.append(cid) or f"session:{cid}",
    )
    repl.input_items = [{"role": "user", "content": "hello"}]
    repl.model.cost = 1.23

    repl.clear_session_state()

    assert repl.input_items == []
    assert repl.model.cost == 0.0
    assert len(created) == 1
    assert created[0].startswith("conv-")
    assert repl.session == f"session:{created[0]}"


def test_set_mode_updates_mode():
    repl = _make_repl()

    repl.set_mode("human")

    assert repl.config.mode == "human"


def test_approve_command_modes():
    repl = _make_repl(prompt_session=FakePromptSession(["", "because no", "y", "not now"]))

    assert repl.approve_command(cmd="echo hi") is True
    assert repl.approve_command(cmd="echo hi") is False
    repl.config.mode = "human"
    assert repl.approve_command(cmd="echo hi") is True
    assert repl.approve_command(cmd="echo hi") is False


def test_approve_command_yolo_skips_prompt():
    prompt = FakePromptSession(["should-not-be-used"])
    repl = _make_repl(prompt_session=prompt)
    repl.config.mode = "yolo"

    assert repl.approve_command(cmd="echo hi") is True
    assert prompt.prompts == []


def test_repl_tool_wrapper_replaces_known_tools():
    wrapper = cli_repl.ReplToolWrapper()
    tools = [
        SimpleNamespace(name="execute_bash_command"),
        SimpleNamespace(name="ask_user_for_info"),
    ]

    wrapped = wrapper.wrap_tools(tools, _make_repl())

    assert len(wrapped) == 2
    assert wrapped[0] is not tools[0]
    assert "execute_bash_command" in getattr(wrapped[0], "name", "")
    assert "ask_user_for_info" in getattr(wrapped[1], "name", "")


def test_bash_tool_rejects_when_not_approved():
    class StubRepl:
        last_rejection_reason = "nope"

        def render_command_proposal(self, **kwargs):
            self.proposal = kwargs

        def approve_command(self, **kwargs):
            self.approval = kwargs
            return False

        def render_tool_result(self, *args, **kwargs):
            raise AssertionError("Tool result should not render on rejection")

    tool = cli_repl.ReplToolWrapper()._create_bash_tool(StubRepl())

    result = _invoke_tool(tool, cmd="echo hi", cwd="/tmp", thought="")

    assert result["returncode"] == 1
    assert "nope" in result["output"]


def test_ask_user_tool_collects_input():
    class StubConsole:
        def print(self, *args, **kwargs):
            return None

    class StubRepl:
        console = StubConsole()

        def prompt_inline(self, message):
            self.message = message
            return "answer"

    tool = cli_repl.ReplToolWrapper()._create_ask_user_tool(StubRepl())

    result = _invoke_tool(tool, prompt="Enter value", thought="Think first")

    assert result == "answer"


def test_handle_unknown_command_is_handled():
    repl = _make_repl()

    result = repl.handle_command(cli_repl.SlashCommand(name="wat", args=()))

    assert result.handled is True
    assert result.should_exit is False


def test_run_turn_updates_input_history(monkeypatch):
    raw_event = cli_repl.RawResponsesStreamEvent(
        data=ResponseTextDeltaEvent(
            delta="Hello from agent",
            type="response.output_text.delta",
            event_id="e1",
            item_id="i1",
            output_index=0,
            content_index=0,
            logprobs=[],
            sequence_number=0,
        )
    )

    class FakeResult:
        def __init__(self, agent, input_items):
            self.last_agent = agent
            self._input_items = list(input_items)

        async def stream_events(self):
            yield raw_event

        def to_input_list(self):
            return self._input_items + [{"role": "assistant", "content": "Hello from agent"}]

    class FakeRunner:
        @staticmethod
        def run_streamed(agent, input=None, context=None, max_turns=None, session=None):
            return FakeResult(agent, input or [])

    repl = _make_repl()
    monkeypatch.setattr(cli_repl, "Runner", FakeRunner)

    asyncio.run(repl._run_turn("hello"))

    assert repl.input_items[0]["content"] == "hello"
    assert repl.input_items[-1]["content"] == "Hello from agent"
