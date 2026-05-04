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

    async def prompt_async(self, message, **kwargs):
        self.prompts.append(message)
        return self.responses.pop(0) if self.responses else ""


def _make_console():
    return cli_repl.Console(file=io.StringIO(), force_terminal=False, color_system=None)


def _make_agent(name="scientist"):
    return Agent(name=name, instructions=f"{name} instructions", model="test-model", tools=[])


def _make_repl(**kwargs):
    defaults = {
        "agent_name": "scientist",
        "agent_factory": lambda name, platform, model_name: _make_agent(name),
        "available_agents": {"scientist": "default", "coder": "code agent"},
        "context": SimpleNamespace(agent_name="scientist"),
        "max_turns": 5,
        "prompt_session": FakePromptSession([]),
        "console": _make_console(),
        "model_platform": "cborg",
        "model_name": "test-model",
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
    repl = _make_repl(
        agent_factory=lambda name, platform, model_name: calls.append((name, platform, model_name))
        or _make_agent(name)
    )

    repl.switch_agent("coder")

    assert repl.agent_name == "coder"
    assert repl.context.agent_name == "coder"
    assert calls == [
        ("scientist", "cborg", "test-model"),
        ("coder", "cborg", "test-model"),
    ]


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


def test_render_help_preserves_line_breaks_and_strips_markup():
    console = cli_repl.Console(
        file=io.StringIO(),
        force_terminal=False,
        color_system=None,
        width=120,
    )
    repl = _make_repl(console=console)

    repl.render_help()

    output = console.file.getvalue()
    assert "[bold]" not in output
    assert "Slash Commands" in output
    assert "/help  Show this help text" in output
    assert "/platforms  List supported model platforms" in output
    assert "/platform <name>  Switch the active model platform" in output
    assert "/models [platform]  List available models for the current or given platform" in output
    assert "/model <name>  Switch the active model on the current platform" in output
    assert "/mode <confirm|yolo|human>  Change command approval mode" in output
    assert "Modes" in output
    assert 'human  Type "y" to allow each command explicitly' in output


def test_approve_command_modes():
    console = _make_console()
    repl = _make_repl(
        prompt_session=FakePromptSession(["", "because no", "y", "not now"]),
        console=console,
    )

    assert asyncio.run(repl.approve_command_async(cmd="echo hi")) is True
    assert asyncio.run(repl.approve_command_async(cmd="echo hi")) is False
    repl.config.mode = "human"
    assert asyncio.run(repl.approve_command_async(cmd="echo hi")) is True
    assert asyncio.run(repl.approve_command_async(cmd="echo hi")) is False
    output = console.file.getvalue()
    assert "approval" in output
    assert "Approved." in output
    assert "Rejected." in output
    assert "Reason: because no" in output
    assert "Reason: not now" in output


def test_approve_command_yolo_skips_prompt():
    prompt = FakePromptSession(["should-not-be-used"])
    console = _make_console()
    repl = _make_repl(prompt_session=prompt, console=console)
    repl.config.mode = "yolo"

    assert asyncio.run(repl.approve_command_async(cmd="echo hi")) is True
    assert prompt.prompts == []
    assert "Auto-approved in yolo mode." in console.file.getvalue()


def test_render_command_proposal_shows_mode():
    console = _make_console()
    repl = _make_repl(console=console)
    repl.config.mode = "human"

    repl.render_command_proposal(cmd="echo hi", cwd="/tmp", thought="")

    output = console.file.getvalue()
    assert "Command" in output
    assert "echo hi" in output
    assert "Working directory" in output
    assert "/tmp" in output
    assert "Approval mode" in output
    assert "human" in output


def test_render_assistant_output_skips_duplicate_panel_after_stream():
    console = _make_console()
    repl = _make_repl(console=console)

    repl.render_assistant_output("hello", streamed=True)

    assert console.file.getvalue() == ""


def test_render_assistant_output_renders_panel_when_not_streamed():
    console = _make_console()
    repl = _make_repl(console=console)

    repl.render_assistant_output("hello", streamed=False)

    output = console.file.getvalue()
    assert "assistant" in output
    assert "hello" in output


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
    import io

    class StubConsole:
        file = io.StringIO()

    class StubRepl:
        console = StubConsole()
        last_rejection_reason = "nope"

        def render_command_proposal(self, **kwargs):
            self.proposal = kwargs

        async def approve_command_async(self, **kwargs):
            self.approval = kwargs
            return False

        def render_tool_result(self, *args, **kwargs):
            raise AssertionError("Tool result should not render on rejection")

    tool = cli_repl.ReplToolWrapper()._create_bash_tool(StubRepl())

    result = _invoke_tool(tool, cmd="echo hi", cwd="/tmp", thought="")

    assert result["returncode"] == 1
    assert "nope" in result["output"]


def test_ask_user_tool_collects_input():
    import io

    class StubPromptSession:
        async def prompt_async(self, message, **kwargs):
            self.message = message
            return "answer"

    class StubConsole:
        file = io.StringIO()

        def print(self, *args, **kwargs):
            return None

    class StubRepl:
        console = StubConsole()
        prompt_session = StubPromptSession()

        def _build_completer(self):
            return None

        def _bottom_toolbar(self):
            return ""

    tool = cli_repl.ReplToolWrapper()._create_ask_user_tool(StubRepl())

    result = _invoke_tool(tool, prompt="Enter value", thought="Think first")

    assert result == "answer"


def test_handle_unknown_command_is_handled():
    repl = _make_repl()

    result = repl.handle_command(cli_repl.SlashCommand(name="wat", args=()))

    assert result.handled is True
    assert result.should_exit is False


def test_render_platforms_shows_current_platform():
    console = _make_console()
    repl = _make_repl(console=console, model_platform="openai")

    repl.render_platforms()

    output = console.file.getvalue()
    assert "Platform" in output
    assert "openai" in output


def test_render_models_uses_current_platform(monkeypatch):
    console = _make_console()
    repl = _make_repl(console=console, model_platform="openai", model_name="gpt-5-mini")
    monkeypatch.setattr(cli_repl, "get_supported_model_providers", lambda: ("cborg", "openai"))
    monkeypatch.setattr(
        cli_repl,
        "get_model_provider_settings",
        lambda platform: SimpleNamespace(base_url="https://example.com", api_key="key"),
    )
    monkeypatch.setattr(
        cli_repl,
        "list_available_models",
        lambda platform, settings=None: ("gpt-5-mini", "gpt-5"),
    )

    repl.render_models()

    output = console.file.getvalue()
    assert "Model" in output
    assert "openai" in output
    assert "gpt-5-mini" in output
    assert "gpt-5" in output


def test_render_models_rejects_unknown_platform(monkeypatch):
    console = _make_console()
    repl = _make_repl(console=console, model_platform="cborg")
    monkeypatch.setattr(cli_repl, "get_supported_model_providers", lambda: ("cborg", "openai"))

    repl.render_models("wat")

    output = console.file.getvalue()
    assert "Unknown platform" in output
    assert "cborg, openai" in output


def test_set_platform_switches_platform_and_resets_to_default_model(monkeypatch):
    calls = []
    console = _make_console()
    repl = _make_repl(
        console=console,
        model_platform="cborg",
        model_name="gemini-flash",
        agent_factory=lambda name, platform, model_name: calls.append((name, platform, model_name))
        or _make_agent(name),
    )
    monkeypatch.setattr(cli_repl, "get_supported_model_providers", lambda: ("cborg", "openai"))
    monkeypatch.setattr(
        cli_repl,
        "get_model_provider_settings",
        lambda platform: SimpleNamespace(default_model="gpt-5-mini"),
    )

    repl.set_platform("openai")

    assert repl.model.platform == "openai"
    assert repl.model.name == "gpt-5-mini"
    assert calls[-1] == ("scientist", "openai", "gpt-5-mini")
    output = console.file.getvalue()
    assert "Platform set to openai." in output
    assert "Model set to default gpt-5-mini." in output


def test_set_model_switches_current_model(monkeypatch):
    calls = []
    console = _make_console()
    repl = _make_repl(
        console=console,
        model_platform="openai",
        model_name="gpt-5-mini",
        agent_factory=lambda name, platform, model_name: calls.append((name, platform, model_name))
        or _make_agent(name),
    )
    monkeypatch.setattr(
        cli_repl,
        "get_model_provider_settings",
        lambda platform: SimpleNamespace(base_url="https://example.com", api_key="key"),
    )
    monkeypatch.setattr(
        cli_repl,
        "list_available_models",
        lambda platform, settings=None: ("gpt-5-mini", "gpt-5"),
    )

    repl.set_model("gpt-5")

    assert repl.model.name == "gpt-5"
    assert calls[-1] == ("scientist", "openai", "gpt-5")
    output = console.file.getvalue()
    assert "Model set to gpt-5" in output
    assert "Platform: openai" in output


def test_set_model_rejects_unknown_model(monkeypatch):
    console = _make_console()
    repl = _make_repl(console=console, model_platform="openai", model_name="gpt-5-mini")
    monkeypatch.setattr(
        cli_repl,
        "get_model_provider_settings",
        lambda platform: SimpleNamespace(base_url="https://example.com", api_key="key"),
    )
    monkeypatch.setattr(
        cli_repl,
        "list_available_models",
        lambda platform, settings=None: ("gpt-5-mini", "gpt-5"),
    )

    repl.set_model("wat")

    output = console.file.getvalue()
    assert "is not on openai" in output
    assert "Try /models" in output


def test_render_startup_includes_platform_and_model():
    console = _make_console()
    repl = _make_repl(console=console, model_platform="openai", model_name="gpt-5-mini")

    repl.render_startup()

    output = console.file.getvalue()
    assert "Platform: openai" in output
    assert "Model: gpt-5-mini" in output


def test_bottom_toolbar_includes_platform_and_model():
    repl = _make_repl(model_platform="openai", model_name="gpt-5-mini")

    toolbar = repl._bottom_toolbar()

    assert "platform=openai" in toolbar
    assert "model=gpt-5-mini" in toolbar


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
