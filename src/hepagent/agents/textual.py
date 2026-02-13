"""Use the Textual terminal to interact with users for Bash Agent.

This module provides TextualAgent, an interactive TUI for running AI agents with real-time
display of thinking processes and bash command execution.

Features:
    - Interactive display of agent thinking and command execution
    - Three execution modes:
        * YOLO mode (y): Auto-approve all commands
        * CONFIRM mode (c): Ask for confirmation before each command (default)
        * HUMAN mode (u): Disable automatic command execution
    - Step-by-step navigation through agent execution
    - Real-time cost tracking
    - Support for both DummyAgent (testing) and real agents via tool wrappers

Usage:
UI Controls:
    - Press 'y' or Ctrl+Y to switch to YOLO mode
    - Press 'c' to switch to CONFIRM mode
    - Press 'u' or Ctrl+U to switch to HUMAN mode
    - Press 'left'/'h' or 'right'/'l' to navigate steps
    - Press 'q' or Ctrl+Q to quit

Integration Example:
    from hepagent.agents.bash import create as create_bash_agent
    from hepagent.agents.textual import TextualAgent, AgentAdapter
    from hepagent.agents.textual_bash import BashToolWrapper

    # Create bash agent
    bash_agent = create_bash_agent()

    # Create TextualAgent app
    app = TextualAgent(model="gpt-4", env={})

    # Wrap bash agent with adapter
    app.agent = AgentAdapter(bash_agent, app, tool_wrapper=BashToolWrapper())

    # Run with a task
    exit_status, result = app.run(task="List files in current directory")
"""

import asyncio
import logging
import threading
import time
from collections.abc import Iterable
from dataclasses import dataclass
from importlib.resources import files
from typing import Any, Protocol

from rich.spinner import Spinner
from rich.text import Text
from textual.app import App, ComposeResult, SystemCommand
from textual.binding import Binding
from textual.containers import Container, Vertical, VerticalScroll
from textual.css.query import NoMatches
from textual.events import Key
from textual.screen import Screen
from textual.widgets import Footer, Header, Input, Static, TextArea

from agents import Agent, AgentHooks, Runner
from agents.run_context import RunContextWrapper
from hepagent.token_costs import calculate_cost

# Constants for display and cost tracking
OUTPUT_TRUNCATE_LENGTH = 500  # Maximum characters to show from command output


class AddLogEmitCallback(logging.Handler):
    def __init__(self, callback):
        """Custom log handler that forwards messages via callback."""
        super().__init__()
        self.callback = callback

    def emit(self, record: logging.LogRecord):
        self.callback(record)  # type: ignore[attr-defined]


class ToolWrapper(Protocol):
    """Protocol for wrapping agent tools for UI behavior."""

    def wrap_tools(self, tools: list, adapter: AgentAdapter) -> list:
        """Return a list of tools to attach to the wrapped agent."""


class NoopToolWrapper:
    """Default wrapper that leaves tools unchanged."""

    def wrap_tools(self, tools: list, adapter: AgentAdapter) -> list:
        return list(tools)


def _message_header_label(self, message: dict) -> str:
    kind = message.get("kind", "")
    if kind == "final":
        return "CONCLUSION"
    if kind == "error":
        return "ERROR"
    if kind == "task":
        return "TASK"
    if message["role"] == "assistant":
        return "AGENT"
    if message["role"] == "user":
        return "USER"
    if message["role"] == "system":
        return "SYSTEM"
    return message["role"].upper()


def _messages_to_steps(messages: list[dict]) -> list[list[dict]]:
    """Group messages into "pages" as shown by the UI."""
    steps: list[list[dict]] = []
    current_step: list[dict] = []

    for msg in messages:
        # Start a new step when a new agent thought appears
        if (
            msg["role"] == "assistant"
            and isinstance(msg.get("content"), str)
            and msg["content"].lstrip().startswith("💭")
            and current_step
        ):
            steps.append(current_step)
            current_step = []

        # Start a new step for final output
        if msg.get("kind") == "final" and current_step:
            steps.append(current_step)
            current_step = []

        current_step.append(msg)

    if current_step:
        steps.append(current_step)

    return steps


class SmartInputContainer(Container):
    def __init__(self, app: TextualAgent):
        """Smart input container supporting single-line and multi-line input modes."""
        super().__init__(classes="smart-input-container")
        self._app = app
        self._multiline_mode = False
        self.can_focus = True
        self.display = False

        self.pending_prompt: str | None = None
        self._input_event = threading.Event()
        self._input_result: str | None = None

        self._header_display = Static(
            id="input-header-display", classes="message-header input-request-header"
        )
        self._hint_text = Static(classes="hint-text")
        self._single_input = Input(placeholder="Type your input...")
        self._multi_input = TextArea(show_line_numbers=False, classes="multi-input")
        self._input_elements_container = Vertical(
            self._header_display,
            self._hint_text,
            self._single_input,
            self._multi_input,
            classes="message-container",
        )

    def compose(self) -> ComposeResult:
        yield self._input_elements_container

    def on_mount(self) -> None:
        """Initialize the widget state."""
        self._multi_input.display = False
        self._update_mode_display()

    def on_focus(self) -> None:
        """Called when the container gains focus."""
        if self._multiline_mode:
            self._multi_input.focus()
        else:
            self._single_input.focus()

    def request_input(self, prompt: str) -> str:
        """Request input from user. Returns input text."""
        self._input_event.clear()
        self._input_result = None
        self.pending_prompt = prompt
        self._header_display.update(prompt)
        self._update_mode_display()
        self._app.call_from_thread(self._app.update_content)
        self._input_event.wait()
        return self._input_result or ""

    def _complete_input(self, input_text: str):
        """Internal method to complete the input process."""
        self._input_result = input_text
        self.pending_prompt = None
        self.display = False
        self._single_input.value = ""
        self._multi_input.text = ""
        self._multiline_mode = False
        self._update_mode_display()
        self._app.agent_state = "RUNNING"
        self._app.update_content()
        # Reset scroll position to bottom since input container disappearing changes layout
        # somehow scroll_to doesn't work.
        self._app._vscroll.scroll_y = 0
        self._input_event.set()

    def action_toggle_mode(self) -> None:
        """Switch from single-line to multi-line mode (one-way only)."""
        if self.pending_prompt is None or self._multiline_mode:
            return

        self._multiline_mode = True
        self._update_mode_display()
        self.on_focus()

    def _update_mode_display(self) -> None:
        """Update the display based on current mode."""
        if self._multiline_mode:
            self._multi_input.text = self._single_input.value
            self._single_input.display = False
            self._multi_input.display = True
            self._hint_text.update(
                "[reverse][bold][$accent] Ctrl+D [/][/][/] to submit, [reverse][bold][$accent] "
                "Tab [/][/][/] to switch focus with other controls"
            )
        else:
            self._hint_text.update(
                "[reverse][bold][$accent] Enter [/][/][/] to submit, [reverse][bold][$accent] "
                "Ctrl+T [/][/][/] to switch to multi-line input, [reverse][bold][$accent] "
                "Tab [/][/][/] to switch focus with other controls",
            )
            self._multi_input.display = False
            self._single_input.display = True

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """Handle single-line input submission."""
        if not self._multiline_mode:
            text = event.input.value.strip()
            self._complete_input(text)

    def on_key(self, event: Key) -> None:
        """Handle key events."""
        if event.key == "ctrl+t" and not self._multiline_mode:
            event.prevent_default()
            self.action_toggle_mode()
            return

        if self._multiline_mode and event.key == "ctrl+d":
            event.prevent_default()
            self._complete_input(self._multi_input.text.strip())
            return

        if event.key == "escape":
            event.prevent_default()
            self.can_focus = False
            self._app.set_focus(None)
            return


@dataclass
class AgentConfig:
    """Configuration for agent execution mode."""

    mode: str = "confirm"  # "yolo", "human", "confirm"


@dataclass
class AgentModel:
    """Model wrapper to track costs."""

    cost: float = 0.0
    name: str = ""


class AgentAdapter:
    """Adapter that wraps a real Agent to work with TextualAgent.

    This adapter bridges the gap between the openai-agents framework and TextualAgent's
    expected interface. It:

    1. Provides the attributes TextualAgent expects (messages, config, model, env)
    2. Wraps the agent's bash tool to make it mode-aware (YOLO/confirm/human)
    3. Uses AgentHooks to capture and display agent thinking and tool execution
    4. Manages the async event loop for running the agent

    Args:
        agent: The original Agent instance (e.g., from bash.create())
        textual_app: The TextualAgent instance that will display the agent's execution
        model_name: Optional model name, "google/gemini-flash" if None

    Attributes:
        messages: List of message dictionaries for display in the UI
        config: AgentConfig with mode ("yolo", "confirm", or "human")
        model: AgentModel for tracking costs
        env: Dictionary for environment variables
        agent: The wrapped Agent with mode-aware tools
    """

    def __init__(
        self,
        agent: Agent,
        textual_app: TextualAgent,
        tool_wrapper: ToolWrapper | None = None,
    ):
        self.original_agent = agent
        self.textual_app = textual_app
        self.messages = []
        self.env = {}
        self.config = AgentConfig()

        wrapped_tools = (
            tool_wrapper.wrap_tools(agent.tools, self)
            if tool_wrapper is not None
            else NoopToolWrapper().wrap_tools(agent.tools, self)
        )

        model_name = (
            agent.model if isinstance(agent.model, str) else getattr(agent.model, "model", "")
        )
        self.model = AgentModel(name=model_name)

        # Create a new agent with wrapped tools while preserving the model
        self.agent = Agent(
            name=agent.name,
            instructions=agent.instructions,
            model=agent.model,
            tools=wrapped_tools,
            hooks=LogBashCallAgentHooks(self),
        )

    def add_message(self, role: str, content: str, **kwargs):
        """Add a message to the messages list."""
        self.messages.append(
            {
                "role": role,
                "content": content,
                "timestamp": time.time(),
                **kwargs,
            }
        )
        if self.textual_app.agent_state != "UNINITIALIZED":
            self.textual_app.call_from_thread(self.textual_app.on_message_added)

    def run(self, task: str, **kwargs):
        """Run the agent with the given task."""
        self.messages = []
        self.add_message("system", self.original_agent.instructions)
        self.add_message("user", task, kind="task")

        # Run the agent asynchronously
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            context = kwargs.get("context")
            if context is not None:
                result = loop.run_until_complete(
                    Runner.run(self.agent, task, max_turns=20, context=context)
                )
            else:
                result = loop.run_until_complete(Runner.run(self.agent, task, max_turns=20))
            self.add_message("system", f"✓ Task completed: {result.final_output}", kind="final")
            self.textual_app.call_from_thread(
                self.textual_app.on_agent_finished, "success", result.final_output
            )
        except Exception as e:
            self.add_message("system", f"✗ Error: {str(e)}")
            self.textual_app.call_from_thread(self.textual_app.on_agent_finished, "error", str(e))
            import traceback

            traceback.print_exc()
            raise
        finally:
            loop.close()
            print("Agent run complete.")


class LogBashCallAgentHooks(AgentHooks):
    """Hooks to capture agent execution events and display them in TextualAgent."""

    def __init__(self, adapter: AgentAdapter):
        super().__init__()
        self.adapter = adapter

    async def on_llm_start(
        self,
        context: RunContextWrapper,
        agent: Agent,
        system_prompt: str | None,
        input_items: list,
    ) -> None:
        """Called when the LLM starts processing."""
        # We could show a "thinking" message here

    async def on_llm_end(self, context: RunContextWrapper, agent: Agent, response: Any) -> None:
        """Called when the LLM finishes processing."""
        # Extract assistant's response
        if hasattr(response, "output_items"):
            for item in response.output_items:
                if hasattr(item, "text") and item.text:
                    # Don't add if it's a duplicate of the last message
                    if (
                        not self.adapter.messages
                        or self.adapter.messages[-1].get("content") != item.text
                    ):
                        self.adapter.add_message("assistant", item.text)
                        self.adapter.textual_app.call_from_thread(
                            self.adapter.textual_app.on_message_added
                        )

        # Track costs using actual token usage from context
        if hasattr(context, "usage") and context.usage:
            # Calculate cost based on actual token usage and model name
            cost = calculate_cost(context.usage, self.adapter.model.name)
            self.adapter.model.cost += cost


class DummyAgent:
    """Dummy agent for testing the TextualAgent UI."""

    def __init__(self):
        self.messages = []
        self.model = type("Model", (), {"cost": 0.0, "name": "dummy"})()
        self.env = {}
        self.config = type("Config", (), {"mode": "human"})()

    def run(self, task: str, **kwargs):
        """Simulate running the agent."""
        self.messages.append({"role": "system", "content": f"Starting task: {task}"})
        time.sleep(1)
        self.messages.append({"role": "assistant", "content": "Thinking about the task..."})
        time.sleep(1)
        self.messages.append({"role": "user", "content": "Please provide your input."})
        time.sleep(1)
        self.messages.append({"role": "assistant", "content": "Processing your input..."})
        time.sleep(1)
        self.messages.append({"role": "system", "content": "Task completed."})
        self.model.cost += 0.05


class TextualAgent(App):
    BINDINGS = [
        Binding("right,l", "next_step", "Step++", tooltip="Show next step of the agent"),
        Binding("left,h", "previous_step", "Step--", tooltip="Show previous step of the agent"),
        Binding("0", "first_step", "Step=0", tooltip="Show first step of the agent", show=False),
        Binding("$", "last_step", "Step=-1", tooltip="Show last step of the agent", show=False),
        Binding("j,down", "scroll_down", "Scroll down", show=False),
        Binding("k,up", "scroll_up", "Scroll up", show=False),
        Binding("q,ctrl+q", "quit", "Quit", tooltip="Quit the agent"),
        Binding(
            "y,ctrl+y",
            "yolo",
            "YOLO mode",
            tooltip="Switch to YOLO Mode (LM actions will execute immediately)",
        ),
        Binding(
            "c",
            "confirm",
            "CONFIRM mode",
            tooltip="Switch to Confirm Mode (LM proposes commands and you confirm/reject them)",
        ),
        Binding(
            "u,ctrl+u",
            "human",
            "HUMAN mode",
            tooltip="Switch to Human Mode (you can now type commands directly)",
        ),
        Binding("f1,question_mark", "toggle_help_panel", "Help", tooltip="Show help"),
    ]

    def __init__(self, model, env, **kwargs):
        css_path = files("hepagent.config").joinpath("mini.tcss")
        self.__class__.CSS = css_path.read_text()
        super().__init__()
        self.agent_state = "UNINITIALIZED"
        self.agent = DummyAgent()
        self._i_step = 0
        self.n_steps = 1
        self.input_container = SmartInputContainer(self)
        self.log_handler = AddLogEmitCallback(
            lambda record: self.call_from_thread(self.on_log_message_emitted, record)
        )
        logging.getLogger().addHandler(self.log_handler)
        self._spinner = Spinner("dots")
        self.exit_status: str = "ExitStatusUnset"
        self.result: str = ""

        self._vscroll = VerticalScroll()

        self._task: str | None = None
        self._task_kwargs: dict[str, Any] = {}
        self._agent_thread: threading.Thread | None = None
        self._ui_ready = False

    def run_task(self, task: str, **kwargs) -> tuple[str, str]:
        """Run the agent with the given task in a separate thread."""
        self._task = task
        self._task_kwargs = kwargs

        super().run()
        return self.exit_status, self.result

    def run(self, task: str, **kwargs) -> tuple[str, str]:
        threading.Thread(target=lambda: self.agent.run(task, **kwargs), daemon=True).start()
        super().run()
        return self.exit_status, self.result

    # --- Basics ---

    @property
    def config(self):
        return self.agent.config

    @property
    def i_step(self) -> int:
        """Current step index."""
        return self._i_step

    @i_step.setter
    def i_step(self, value: int) -> None:
        """Set current step index, automatically clamping to valid bounds."""
        if value != self._i_step:
            self._i_step = max(0, min(value, self.n_steps - 1))
            self._vscroll.scroll_to(y=0, animate=False)
            self.update_content()

    def compose(self) -> ComposeResult:
        yield Header()
        with Container(id="main"):
            with self._vscroll:
                with Vertical(id="content"):
                    pass
                yield self.input_container
        yield Footer()

    def on_mount(self) -> None:
        self._ui_ready = True
        self.agent_state = "RUNNING"
        self.update_content()
        self.set_interval(1 / 8, self._update_headers)
        # Start agent ONLY after the app is running (safe to call_from_thread)
        if self._task is not None and self._agent_thread is None:

            def _runner():
                try:
                    self.agent.run(self._task, **self._task_kwargs)
                except Exception as e:
                    import traceback

                    traceback.print_exc()
                    # Ensure UI gets an error state even if agent crashes
                    self.call_from_thread(self.on_agent_finished, "error", str(e))

            self._agent_thread = threading.Thread(target=_runner, daemon=True)
            self._agent_thread.start()

    @property
    def messages(self) -> list[dict]:
        return self.agent.messages

    @property
    def model(self):
        return self.agent.model

    @property
    def env(self):
        return self.agent.env

    # --- Reacting to events ---

    def on_message_added(self) -> None:
        auto_follow = self.i_step == self.n_steps - 1 and self._vscroll.scroll_y <= 1
        self.n_steps = len(_messages_to_steps(self.agent.messages))
        self.update_content()
        if auto_follow:
            self.action_last_step()

    def on_log_message_emitted(self, record: logging.LogRecord) -> None:
        """Handle log messages of warning level or higher by showing them as notifications."""
        if record.levelno >= logging.WARNING:
            self.notify(f"[{record.levelname}] {record.getMessage()}", severity="warning")

    def on_unmount(self) -> None:
        """Clean up the log handler when the app shuts down."""
        if hasattr(self, "log_handler"):
            logging.getLogger().removeHandler(self.log_handler)

    def on_agent_finished(self, exit_status: str, result: str):
        self.agent_state = "STOPPED"

        self.exit_status = exit_status
        self.result = result
        if self._ui_ready:
            self.update_content()
            self.notify(f"Agent finished with status: {exit_status}")
            self.notify("Press q to quit, ←/→ to inspect steps")
            self.refresh()

    # --- UI update logic ---

    def update_content(self) -> None:
        if not self._ui_ready:
            return
        container = self.query_one("#content", Vertical)
        container.remove_children()
        items = _messages_to_steps(self.agent.messages)

        if not items:
            container.mount(Static("Waiting for agent to start..."))
            return

        for message in items[self.i_step]:
            if isinstance(message["content"], list):
                content_str = "\n".join([item["text"] for item in message["content"]])
            else:
                content_str = str(message["content"])
            message_container = Vertical(classes="message-container")
            if message.get("kind") == "final":
                message_container.add_class("final-message")
            container.mount(message_container)
            header_label = _message_header_label(self, message)
            message_container.mount(Static(header_label, classes="message-header"))
            content_widget = Static(Text(content_str, no_wrap=False), classes="message-content")
            if message.get("kind") == "final":
                content_widget.add_class("final-content")
            message_container.mount(content_widget)
        if self.input_container.pending_prompt is not None:
            self.agent_state = "AWAITING_INPUT"
        self.input_container.display = (
            self.input_container.pending_prompt is not None and self.i_step == len(items) - 1
        )
        if self.input_container.display:
            self.input_container.on_focus()

        self._update_headers()
        self.refresh()

    def _update_headers(self) -> None:
        """Update just the title with current state and spinner if needed."""
        status_text = self.agent_state
        if self.agent_state == "RUNNING":
            spinner_frame = str(self._spinner.render(time.time())).strip()
            status_text = f"{self.agent_state} {spinner_frame}"
        self.title = (
            f"Step {self.i_step + 1}/{self.n_steps} - {status_text} "
            f"- Cost: ${self.agent.model.cost:.6f}"
        )
        self.sub_title = f"Mode: {self.agent.config.mode}"
        try:
            self.query_one("Header").set_class(self.agent_state == "RUNNING", "running")
        except NoMatches:  # might be called when shutting down
            pass

    # --- Other textual overrides ---

    def get_system_commands(self, screen: Screen) -> Iterable[SystemCommand]:
        # Add to palette
        yield from super().get_system_commands(screen)
        for binding in self.BINDINGS:
            description = f"{binding.description} (shortcut {' OR '.join(binding.key.split(','))})"  # type: ignore[attr-defined]
            action_method = getattr(self, f"action_{binding.action}")  # type: ignore[attr-defined]
            yield SystemCommand(description, binding.tooltip, action_method)  # type: ignore[attr-defined]

    # --- Textual bindings ---

    def action_yolo(self):
        self.agent.config.mode = "yolo"
        if self.input_container.pending_prompt is not None:
            self.input_container._complete_input("")  # accept
        self.notify("YOLO mode enabled - LM actions will execute immediately")

    def action_human(self):
        if self.agent.config.mode == "confirm" and self.input_container.pending_prompt is not None:
            self.input_container._complete_input(
                "User switched to manual mode, this command will be ignored"
            )
        self.agent.config.mode = "human"
        self.notify("Human mode enabled - you can now type commands directly")

    def action_confirm(self):
        if self.agent.config.mode == "human" and self.input_container.pending_prompt is not None:
            self.input_container._complete_input("")  # just submit blank action
        self.agent.config.mode = "confirm"
        self.notify("Confirm mode enabled - LM proposes commands and you confirm/reject them")

    def action_next_step(self) -> None:
        self.i_step += 1

    def action_previous_step(self) -> None:
        self.i_step -= 1

    def action_first_step(self) -> None:
        self.i_step = 0

    def action_last_step(self) -> None:
        self.i_step = self.n_steps - 1

    def action_scroll_down(self) -> None:
        self._vscroll.scroll_to(y=self._vscroll.scroll_target_y + 15)

    def action_scroll_up(self) -> None:
        self._vscroll.scroll_to(y=self._vscroll.scroll_target_y - 15)

    def action_toggle_help_panel(self) -> None:
        if self.query("HelpPanel"):
            self.action_hide_help_panel()
        else:
            self.action_show_help_panel()
