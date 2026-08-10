from __future__ import annotations

import asyncio
import uuid

import click
import typer
from typer.core import TyperGroup

from agents import Agent, Runner, SQLiteSession, function_tool
from agents.run import DEFAULT_MAX_TURNS
from hepagent.agents.bash import TOOL_CANCEL_MESSAGE, execute_bash_command
from hepagent.agents.cli_repl import (
    CliRepl,
    _build_command_feedback,
    _extract_single_bash_command,
)
from hepagent.agents.common import AgentContext
from hepagent.agents.explorer import create as create_explorer_agent
from hepagent.agents.role import create as create_role_agent, create_role_cfg
from hepagent.agents.skilled import create as create_skilled_agent
from hepagent.config.env import env_config
from hepagent.helpers import (
    bootstrap_hepagent_home,
    enable_mlflow_for_tracing,
    get_env_var,
    get_hepagent_home,
)
from hepagent.model_providers import (
    get_model_provider_settings,
    get_supported_model_providers,
    list_available_models,
    parse_model_spec,
)
from hepagent.plan.templates import DEFAULT_TEMPLATE


class DefaultToRunGroup(TyperGroup):
    """Route unknown first token to the run command for backward compatibility."""

    default_command_name = "run"

    def resolve_command(
        self, ctx: click.Context, args: list[str]
    ) -> tuple[str | None, click.Command | None, list[str]]:
        try:
            return super().resolve_command(ctx, args)
        except click.UsageError:
            if args and not args[0].startswith("-"):
                cmd = self.get_command(ctx, self.default_command_name)
                if cmd is not None:
                    return self.default_command_name, cmd, args
            raise


app = typer.Typer(cls=DefaultToRunGroup)
DEFAULT_MAX_COMMAND_PROPOSALS = 1


def create_chat_session(conversation_id: str) -> SQLiteSession:
    """Create a persistent SQLite-backed session for a conversation id."""
    db_dir = get_hepagent_home() / "sessions"
    db_dir.mkdir(parents=True, exist_ok=True)
    db_path = db_dir / "conversation.db"
    return SQLiteSession(conversation_id, str(db_path))


def create_repl_session_id() -> str:
    """Create a short, random REPL session id."""
    return f"repl-{uuid.uuid4().hex[:12]}"


def _available_role_agents() -> dict[str, object]:
    """Return the configured role-agent registry."""
    return create_role_cfg()


def get_available_agents() -> dict[str, str]:
    """Return all user-selectable agents and descriptions."""
    agents = {
        "explorer": "Research-direction explorer with parallel specialist sub-agents.",
        "scientist": "Skilled Agent",
    }
    for role_name, role_cfg in _available_role_agents().items():
        agents[role_name] = role_cfg.description
    return dict(sorted(agents.items()))


def create_app_agent(
    agent_name: str,
    *,
    model_provider: str,
    model_name: str | None,
):
    """Create the selected agent implementation."""
    normalized = agent_name.lower()
    if normalized == "explorer":
        return create_explorer_agent(model_provider=model_provider, model_name=model_name)
    if normalized in _available_role_agents():
        return create_role_agent(
            role_name=normalized,
            model_provider=model_provider,
            model_name=model_name,
        )
    return create_skilled_agent(model_provider=model_provider, model_name=model_name)


def build_runtime(
    *,
    agent_name: str,
    model: str | None,
    chat: str | None,
):
    """Create the reusable runtime pieces shared by run and repl."""
    model_provider, model_name = parse_model_spec(model)
    agent = create_app_agent(
        agent_name,
        model_provider=model_provider,
        model_name=model_name,
    )
    context = AgentContext(agent_name=agent_name.lower())
    display_model = model_name or get_model_provider_settings(model_provider).default_model
    session = create_chat_session(chat) if chat else None
    return {
        "agent": agent,
        "context": context,
        "display_model": display_model,
        "session": session,
        "model_provider": model_provider,
        "model_name": model_name,
    }


class TerminalRunToolWrapper:
    """Wrap interactive tools for the headless `hepagent run` command."""

    def __init__(self, *, yolo: bool = False, non_interactive: bool = False):
        self.yolo = yolo
        self.non_interactive = non_interactive
        self.last_rejection_reason: str | None = None

    def wrap_tools(self, tools: list[object]) -> list[object]:
        wrapped = []
        for tool in tools:
            name = getattr(tool, "name", "")
            if "execute_bash_command" in name:
                wrapped.append(self._create_bash_tool())
            elif "ask_user_for_info" in name:
                wrapped.append(self._create_ask_user_tool())
            else:
                wrapped.append(tool)
        return wrapped

    def wrap_agent(self, agent: Agent) -> Agent:
        """Return a copy of the agent with terminal-safe tools."""
        return agent.clone(tools=self.wrap_tools(agent.tools))

    def _read_input(self, prompt: str) -> str:
        try:
            return input(prompt)
        except EOFError:
            try:
                with open("/dev/tty", encoding="utf-8") as tty:
                    print(prompt, end="", flush=True)
                    return tty.readline().strip()
            except OSError:
                return ""

    async def approve_command_async(self) -> bool:
        self.last_rejection_reason = None
        if self.yolo or self.non_interactive:
            mode = "non-interactive" if self.non_interactive else "yolo"
            typer.echo(f"Auto-approved in {mode} mode.")
            return True

        response = await asyncio.to_thread(
            self._read_input,
            "Approve command? Press Enter to allow, or type a reason to reject: ",
        )
        if response.strip():
            self.last_rejection_reason = response.strip()
            typer.echo(f"Rejected: {self.last_rejection_reason}")
            return False

        typer.echo("Approved.")
        return True

    def render_command_proposal(self, *, cmd: str, cwd: str = "", thought: str = "") -> None:
        if thought:
            typer.echo(f"THOUGHT: {thought}")
        typer.echo("Command:")
        typer.echo(f"```bash\n{cmd}\n```")
        if cwd:
            typer.echo(f"Working directory: {cwd}")

    def render_tool_result(self, result: dict) -> None:
        typer.echo(f"Return code: {result.get('returncode')}")
        output = str(result.get("output", ""))
        if output:
            typer.echo(output)

    async def execute_bash(self, *, cmd: str, cwd: str = "", thought: str = "") -> dict:
        self.render_command_proposal(cmd=cmd, cwd=cwd, thought=thought)
        if not await self.approve_command_async():
            reason = self.last_rejection_reason or "No reason provided"
            return {"output": TOOL_CANCEL_MESSAGE.format(reason=reason), "returncode": 1}

        result = await asyncio.to_thread(execute_bash_command, cmd, cwd=cwd)
        self.render_tool_result(result)
        return result

    def _create_bash_tool(self):
        @function_tool
        async def execute_bash_command_with_terminal_confirmation(
            cmd: str, cwd: str = "", thought: str = ""
        ) -> dict:
            """Execute a bash command with plain terminal confirmation."""
            return await self.execute_bash(cmd=cmd, cwd=cwd, thought=thought)

        return execute_bash_command_with_terminal_confirmation

    def _create_ask_user_tool(self):
        @function_tool
        async def ask_user_for_info(prompt: str, thought: str = "") -> str:
            """Collect missing task information from the terminal."""
            if thought:
                typer.echo(f"THOUGHT: {thought}")
            if self.non_interactive:
                typer.echo(f"Non-interactive mode: skipped user input for prompt: {prompt}")
                return ""
            response = await asyncio.to_thread(self._read_input, f"{prompt}: ")
            return response.strip()

        return ask_user_for_info


async def run_agent_task(
    *,
    agent: Agent,
    task_prompt: str,
    context: AgentContext,
    max_turns: int,
    session: object | None,
    yolo: bool,
    non_interactive: bool = False,
    max_command_proposals: int = DEFAULT_MAX_COMMAND_PROPOSALS,
) -> str:
    """Run one task in plain terminal mode and return the final agent output."""
    tool_wrapper = TerminalRunToolWrapper(yolo=yolo, non_interactive=non_interactive)
    current_agent = tool_wrapper.wrap_agent(agent)
    turn_input: object = task_prompt

    for _ in range(max_command_proposals):
        result = await Runner.run(
            current_agent,
            turn_input,
            context=context,
            max_turns=max_turns,
            session=session,
        )
        final_output = "" if result.final_output is None else str(result.final_output)
        bash_proposal = _extract_single_bash_command(final_output)
        if bash_proposal is None:
            return final_output

        command_result = await tool_wrapper.execute_bash(
            cmd=bash_proposal.cmd,
            thought=bash_proposal.thought,
        )
        feedback = _build_command_feedback(bash_proposal.cmd, command_result)
        if session is not None:
            turn_input = feedback
        else:
            turn_items = result.to_input_list()
            turn_items.append({"role": "user", "content": feedback})
            turn_input = turn_items
        current_agent = result.last_agent

    raise click.ClickException(
        f"Max command proposals reached while running task: {max_command_proposals}."
    )


@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    agent_name: str = typer.Option(
        "scientist",
        "--agent",
        "-a",
        show_default=True,
        help="Agent configuration to use.",
    ),
    yolo: bool = typer.Option(
        False,
        "--yolo",
        help="Auto-approve all bash commands.",
    ),
    non_interactive: bool = typer.Option(
        False,
        "--non-interactive",
        help="Run without prompting for bash approval or ask_user_for_info input.",
    ),
    max_turns: int = typer.Option(
        DEFAULT_MAX_TURNS,
        "--max-turn",
        help="Maximum number of agent turns (defaults to SDK default).",
        show_default=True,
    ),
    max_command_proposals: int = typer.Option(
        DEFAULT_MAX_COMMAND_PROPOSALS,
        "--max-command-proposals",
        help="Maximum number of text bash proposals to execute during one task.",
        show_default=True,
    ),
    model: str | None = typer.Option(
        None,
        "--model",
        help='Specify the model as "provider:model" (e.g. "openai:gpt-5-mini") '
        "or a bare model name (defaults to cborg).",
        show_default="cborg default",
    ),
    chat: str | None = typer.Option(
        None,
        "--chat",
        help="Conversation id for persistent SQLite chat history.",
    ),
) -> None:
    """HepAgent: A framework for building and deploying AI agents in HEP."""
    bootstrap_hepagent_home()

    # check if OPENAI_API_KEY is set. If not, disable tracing.
    open_ai_key_missing = True
    try:
        openai_key = get_env_var("OPENAI_API_KEY", default=None, set_env=True)
        if openai_key is not None and openai_key.strip() != "":
            open_ai_key_missing = False
    except Exception:
        pass
    if open_ai_key_missing:
        import os

        os.environ["OPENAI_AGENTS_DISABLE_TRACING"] = "1"

    ctx.obj = {
        "agent_name": agent_name,
        "yolo": yolo,
        "non_interactive": non_interactive,
        "max_turns": max_turns,
        "max_command_proposals": max_command_proposals,
        "model": model,
        "chat": chat,
    }
    # When invoked without a subcommand, show help instead of silently exiting.
    if ctx.invoked_subcommand is None:
        typer.echo(ctx.get_help())
        raise typer.Exit()


@app.command("run", hidden=True)
def run_task(
    ctx: typer.Context,
    task_prompt: str = typer.Argument(
        ...,
        help="Task prompt to send to the agent.",
    ),
    agent_name: str = typer.Option(
        "scientist",
        "--agent",
        "-a",
        help="Agent configuration to use.",
    ),
    yolo: bool = typer.Option(
        False,
        "--yolo",
        help="Auto-approve all bash commands.",
    ),
    non_interactive: bool = typer.Option(
        False,
        "--non-interactive",
        help="Run without prompting for bash approval or ask_user_for_info input.",
    ),
    max_turns: int | None = typer.Option(
        None,
        "--max-turn",
        help="Maximum number of agent turns (defaults to SDK default).",
    ),
    max_command_proposals: int | None = typer.Option(
        None,
        "--max-command-proposals",
        help="Maximum number of text bash proposals to execute during one task.",
    ),
    model: str | None = typer.Option(
        None,
        "--model",
        help='Specify the model as "provider:model" (e.g. "openai:gpt-5-mini") '
        "or a bare model name (defaults to cborg).",
    ),
    chat: str | None = typer.Option(
        None,
        "--chat",
        help="Conversation id for persistent SQLite chat history.",
    ),
) -> None:
    """Run HepAgent with a task prompt."""
    options = ctx.obj or {}
    agent_name = agent_name.lower() or str(options.get("agent_name", "scientist"))
    yolo = yolo or bool(options.get("yolo", False))
    non_interactive = non_interactive or bool(options.get("non_interactive", False))
    max_turns = max_turns or int(options.get("max_turns", DEFAULT_MAX_TURNS))
    max_command_proposals = max_command_proposals or int(
        options.get("max_command_proposals", DEFAULT_MAX_COMMAND_PROPOSALS)
    )
    model = model or options.get("model")
    chat = chat or options.get("chat")

    if env_config.use_mlflow_tracing and enable_mlflow_for_tracing():
        typer.echo("MLflow found. Using MLflow for tracing.")
        import mlflow

        mlflow.openai.autolog()

    runtime = build_runtime(agent_name=agent_name, model=model, chat=chat)
    result = asyncio.run(
        run_agent_task(
            agent=runtime["agent"],
            task_prompt=task_prompt,
            context=runtime["context"],
            max_turns=max_turns,
            max_command_proposals=max_command_proposals,
            session=runtime["session"],
            yolo=yolo,
            non_interactive=non_interactive,
        )
    )
    if result:
        typer.echo(result)


@app.command("repl")
def repl(
    ctx: typer.Context,
    agent_name: str = typer.Option(
        "shell",
        "--agent",
        "-a",
        help="Agent configuration to use.",
    ),
    yolo: bool = typer.Option(
        False,
        "--yolo",
        help="Auto-approve all bash commands.",
    ),
    max_turns: int | None = typer.Option(
        None,
        "--max-turn",
        help="Maximum number of agent turns (defaults to SDK default).",
    ),
    model: str | None = typer.Option(
        None,
        "--model",
        help='Specify the model as "provider:model" (e.g. "openai:gpt-5-mini") '
        "or a bare model name (defaults to cborg).",
    ),
    chat: str | None = typer.Option(
        None,
        "--chat",
        help="Conversation id for persistent SQLite chat history.",
    ),
    disable_session: bool = typer.Option(
        False,
        "--disable-session",
        help="Disable the default persistent REPL session.",
    ),
) -> None:
    """Start the interactive coding REPL."""
    options = ctx.obj or {}
    agent_name = agent_name.lower() or str(options.get("agent_name", "scientist"))
    yolo = yolo or bool(options.get("yolo", False))
    max_turns = max_turns or int(options.get("max_turns", DEFAULT_MAX_TURNS))
    model = model or options.get("model")
    chat = chat or options.get("chat")
    if disable_session and chat:
        raise typer.BadParameter("Use either --chat or --disable-session, not both.")

    repl_session_id = None if disable_session else chat or create_repl_session_id()

    runtime = build_runtime(agent_name=agent_name, model=model, chat=repl_session_id)
    model_provider = runtime["model_provider"]
    display_model = runtime["display_model"]
    cli = CliRepl(
        agent_name=agent_name,
        agent_factory=lambda selected, platform, selected_model: create_app_agent(
            selected,
            model_provider=platform,
            model_name=selected_model,
        ),
        available_agents=get_available_agents(),
        context=runtime["context"],
        max_turns=max_turns,
        yolo=yolo,
        session=runtime["session"],
        session_factory=create_chat_session,
        session_id=repl_session_id,
        session_base_id=repl_session_id,
        model_platform=model_provider,
        model_name=display_model,
    )
    cli.run()


@app.command("web")
def web(
    ctx: typer.Context,
    agent_name: str = typer.Option(
        "scientist",
        "--agent",
        "-a",
        help="Agent configuration to use.",
    ),
    yolo: bool = typer.Option(
        False,
        "--yolo",
        help="Auto-approve all bash commands.",
    ),
    max_turns: int | None = typer.Option(
        None,
        "--max-turn",
        help="Maximum number of agent turns.",
    ),
    model: str | None = typer.Option(
        None,
        "--model",
        help='Specify the model as "provider:model" (e.g. "openai:gpt-5-mini") '
        "or a bare model name (defaults to cborg).",
    ),
    chat: str | None = typer.Option(
        None,
        "--chat",
        help="Conversation id to resume for persistent SQLite chat history.",
    ),
    host: str = typer.Option("127.0.0.1", "--host", help="Interface to bind."),
    port: int = typer.Option(8000, "--port", help="Port to serve on."),
    headless: bool = typer.Option(
        False,
        "--headless",
        help="Do not open a browser window on startup.",
    ),
    base_dir: str = typer.Option(
        "analyses",
        "--base-dir",
        help="Parent directory for analyses, used by the plan editor at /plan/<name>.",
    ),
) -> None:
    """Start the browser-based chat UI, including the plan editor at /plan/<name>."""
    from hepagent.web.server import launch_web_ui

    options = ctx.obj or {}
    agent_name = agent_name.lower() or str(options.get("agent_name", "scientist"))
    yolo = yolo or bool(options.get("yolo", False))
    max_turns = max_turns or int(options.get("max_turns", DEFAULT_MAX_TURNS))
    model = model or options.get("model")
    chat = chat or options.get("chat")

    launch_web_ui(
        agent_name=agent_name,
        model=model,
        max_turns=max_turns,
        mode="yolo" if yolo else "confirm",
        chat=chat,
        base_dir=base_dir,
        host=host,
        port=port,
        headless=headless,
    )


@app.command("list-agents")
def list_agents() -> None:
    """List available run modes and role agents."""
    typer.echo("Built-in run modes:")
    typer.echo("\tscientist -> skilled research agent")
    typer.echo("\texplorer -> research-direction explorer")
    typer.echo("\tshell -> --shell/-s")
    typer.echo("\tshell_describer -> --describe/-d")
    typer.echo("\tcoder -> --code/-c")

    typer.echo("\nAvailable role agents:")
    roles = _available_role_agents()
    for role_key in sorted(roles):
        description = roles[role_key].description
        typer.echo(f"\t{role_key}: {description}")


@app.command("list-platforms")
def list_platforms() -> None:
    """List all supported model providers/platforms."""
    typer.echo("Supported model providers:")
    for provider in sorted(get_supported_model_providers()):
        typer.echo(f"\t{provider}")


@app.command("list-models")
def list_models(
    platform: str = typer.Option(
        "cborg",
        "--platform",
        "-p",
        show_default=True,
        help=(f"Platform to query ({', '.join(get_supported_model_providers())})."),
    ),
) -> None:
    """List available models for a provider."""
    settings = get_model_provider_settings(platform)
    if not settings.api_key:
        typer.echo(f"{settings.api_key_env} is not set.")
        raise typer.Exit(code=1)

    typer.echo(f"Available models for {platform}:")
    for name in list_available_models(platform, settings=settings):
        typer.echo("\t" + name)


jfc_app = typer.Typer(name="jfc", help="JFC autonomous HEP analysis pipeline.")
app.add_typer(jfc_app)


def _analysis_root(name: str, base_dir: str):
    """Resolve an analysis name to its root directory, or exit."""
    from pathlib import Path

    root = Path(base_dir).resolve() / name
    if not root.is_dir():
        typer.echo(f"Error: analysis not found: {root}", err=True)
        raise typer.Exit(code=1)
    return root


def _load_plan(analysis_root):
    """Read an analysis's plan, or exit with a message pointing at `plan migrate`."""
    from hepagent.plan.store import load_plan

    try:
        return load_plan(analysis_root)
    except Exception as exc:  # noqa: BLE001 - turned into a CLI message
        typer.echo(f"Error: could not read {analysis_root / 'plan.json'}: {exc}", err=True)
        typer.echo(
            f"If this analysis predates plans, run: "
            f"hepagent jfc plan migrate --name {analysis_root.name}",
            err=True,
        )
        raise typer.Exit(code=1) from exc


def _read_plan_file(path: str):
    """Read a plan document from an arbitrary path, or exit."""
    import json
    from pathlib import Path

    from hepagent.plan.store import plan_from_dict

    p = Path(path)
    if not p.is_file():
        typer.echo(f"Error: plan file not found: {path}", err=True)
        raise typer.Exit(code=1)
    try:
        return plan_from_dict(json.loads(p.read_text(encoding="utf-8")))
    except Exception as exc:  # noqa: BLE001 - turned into a CLI message
        typer.echo(f"Error: {path} is not a readable plan: {exc}", err=True)
        raise typer.Exit(code=1) from exc


@jfc_app.command("run")
def jfc_run(
    name: str = typer.Option(..., "--name", "-n", help="Analysis name (short identifier)."),
    analysis_type: str = typer.Option(
        ...,
        "--type",
        "-t",
        help="Analysis type: measurement or search.",
    ),
    prompt_file: str = typer.Option(
        ...,
        "--prompt-file",
        "-p",
        help="Path to a markdown file containing the physics prompt / question.",
    ),
    base_dir: str = typer.Option("analyses", "--base-dir", help="Parent directory for analyses."),
    model: str | None = typer.Option(
        None,
        "--model",
        help='Model as "provider:model" (e.g. "cborg:claude-sonnet-4-5").',
    ),
    max_iterations: int = typer.Option(
        3, "--max-iterations", help="Max review iterations per node."
    ),
    max_turns: int | None = typer.Option(
        None,
        "--max-turns",
        help="Max turns per agent call. "
        "Defaults: executor=50, note_writer/fixer=30, reviewer/investigator/typesetter=20.",
    ),
    yolo: bool = typer.Option(False, "--yolo", help="Auto-approve all bash commands."),
    codesign: bool = typer.Option(
        False,
        "--codesign",
        help=(
            "Enable every codesign gate the plan declares: generates a summary, "
            "facilitates interactive Q&A, then re-adjudicates with the arbiter."
        ),
    ),
    template: str = typer.Option(
        DEFAULT_TEMPLATE,
        "--template",
        help="Plan template to start from. See `hepagent jfc templates`.",
    ),
    plan_file: str | None = typer.Option(
        None,
        "--plan",
        help="Path to an authored plan.json to run instead of a template.",
    ),
    review_plan: bool = typer.Option(
        False,
        "--review-plan",
        help="Open the plan editor and wait for approval before any agent work begins.",
    ),
    plan_port: int = typer.Option(8001, "--plan-port", help="Port for the plan editor."),
) -> None:
    """Start a new JFC analysis from scratch."""
    import asyncio
    import os
    from pathlib import Path

    from hepagent.agents.jfc.orchestrator import MaxIterationsExceeded, run_jfc_analysis
    from hepagent.agents.jfc.review_gate import PhaseEscalationError

    if yolo:
        os.environ["HEPAGENT_YOLO"] = "1"

    p = Path(prompt_file)
    if not p.exists():
        typer.echo(f"Error: prompt file not found: {prompt_file}", err=True)
        raise typer.Exit(code=1)
    prompt = p.read_text(encoding="utf-8")

    plan = _read_plan_file(plan_file) if plan_file else None

    model_provider, model_name = parse_model_spec(model)

    def _cb(node_id: str, status: str) -> None:
        typer.echo(f"[jfc] node={node_id} {status}")

    def _analysis(**extra):
        return run_jfc_analysis(
            analysis_name=name,
            physics_prompt=prompt,
            analysis_type=analysis_type,  # type: ignore[arg-type]
            base_dir=base_dir,
            model_provider=model_provider,
            model_name=model_name,
            max_iterations_per_phase=max_iterations,
            max_turns=max_turns,
            progress_callback=_cb,
            codesign=codesign,
            template=template,
            plan=plan,
            **extra,
        )

    try:
        if review_plan:
            pdf = asyncio.run(_run_reviewed(name, base_dir, plan_port, _analysis))
        else:
            pdf = asyncio.run(_analysis())
        typer.echo(f"Analysis complete. Final PDF: {pdf}")
    except MaxIterationsExceeded as e:
        typer.echo(f"Error: {e}", err=True)
        typer.echo(f"Resume with: hepagent jfc resume --name {name}", err=True)
        raise typer.Exit(code=1) from e
    except PhaseEscalationError as e:
        typer.echo(f"Escalation at node {e.phase}: {e}", err=True)
        raise typer.Exit(code=2) from e
    except KeyboardInterrupt as e:
        typer.echo("\nInterrupted. State saved. Resume with: hepagent jfc resume --name {name}")
        raise typer.Exit(code=130) from e


@jfc_app.command("resume")
def jfc_resume(
    name: str = typer.Option(..., "--name", "-n", help="Analysis name to resume."),
    from_phase: str | None = typer.Option(
        None,
        "--from-phase",
        "--from-node",
        help=(
            "Plan node id to start from, e.g. 'selection'. Omit to let the analysis "
            "graph pick up from the most recent consistent checkpoint."
        ),
    ),
    base_dir: str = typer.Option("analyses", "--base-dir", help="Parent directory for analyses."),
    model: str | None = typer.Option(None, "--model", help="Model override."),
    max_iterations: int = typer.Option(3, "--max-iterations"),
    max_turns: int | None = typer.Option(
        None,
        "--max-turns",
        help="Max turns per agent call. "
        "Defaults: executor=50, note_writer/fixer=30, reviewer/investigator/typesetter=20.",
    ),
    yolo: bool = typer.Option(False, "--yolo", help="Auto-approve all bash commands."),
) -> None:
    """Resume an interrupted JFC analysis from a specific node."""
    import asyncio
    import os
    from pathlib import Path

    from hepagent.agents.jfc.orchestrator import MaxIterationsExceeded, load_state, run_jfc_analysis
    from hepagent.agents.jfc.review_gate import PhaseEscalationError

    if yolo:
        os.environ["HEPAGENT_YOLO"] = "1"

    analysis_root = Path(base_dir).resolve() / name
    if not analysis_root.exists():
        typer.echo(f"Error: analysis directory not found: {analysis_root}", err=True)
        raise typer.Exit(code=1)

    model_provider, model_name = parse_model_spec(model)

    # Load physics prompt and type from saved state
    state_path = analysis_root / ".orchestration_state.json"
    prompt_file = analysis_root / "prompt.md"
    prompt = prompt_file.read_text(encoding="utf-8") if prompt_file.exists() else ""
    analysis_type = "measurement"
    completed: list[str] = []
    if state_path.exists():
        s = load_state(analysis_root)
        analysis_type = s.analysis_type
        completed = s.completed_nodes
        if not model_provider or model_provider == "cborg":
            model_provider = s.model_provider
            model_name = model_name or s.model_name

    if from_phase is None:
        # Ask the graph where the analysis actually still holds up. This is not
        # "the node after the last one that finished" — a node a later review
        # invalidated is not a checkpoint worth resuming past.
        from hepagent.agents.jfc.planner import resume_point

        from_phase = resume_point(analysis_root, completed)
        if from_phase is None:
            typer.echo(f"Nothing left to run for '{name}'.")
            return
        typer.echo(f"[jfc] resuming from node {from_phase} (graph checkpoint)")

    def _cb(node_id: str, status: str) -> None:
        typer.echo(f"[jfc] node={node_id} {status}")

    try:
        pdf = asyncio.run(
            run_jfc_analysis(
                analysis_name=name,
                physics_prompt=prompt,
                analysis_type=analysis_type,  # type: ignore[arg-type]
                base_dir=base_dir,
                model_provider=model_provider,
                model_name=model_name,
                start_from_phase=from_phase,
                max_turns=max_turns,
                max_iterations_per_phase=max_iterations,
                progress_callback=_cb,
            )
        )
        typer.echo(f"Analysis complete. Final PDF: {pdf}")
    except (MaxIterationsExceeded, PhaseEscalationError) as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(code=1) from e
    except KeyboardInterrupt as e:
        typer.echo("\nInterrupted. State saved.")
        raise typer.Exit(code=130) from e


@jfc_app.command("status")
def jfc_status(
    name: str = typer.Option(..., "--name", "-n", help="Analysis name."),
    base_dir: str = typer.Option("analyses", "--base-dir", help="Parent directory for analyses."),
) -> None:
    """Show the current node status of a JFC analysis."""
    from pathlib import Path

    from hepagent.agents.jfc.orchestrator import load_state

    analysis_root = Path(base_dir).resolve() / name
    if not analysis_root.exists():
        typer.echo(f"Error: analysis not found: {analysis_root}", err=True)
        raise typer.Exit(code=1)

    state_path = analysis_root / ".orchestration_state.json"
    if not state_path.exists():
        typer.echo(f"No orchestration state found for '{name}'.")
        raise typer.Exit(code=1)

    state = load_state(analysis_root)
    plan = _load_plan(analysis_root)

    typer.echo(f"\nJFC Analysis: {name}")
    typer.echo(f"Type: {state.analysis_type}")
    typer.echo(f"Plan: {plan.template or '(authored)'}  rev {plan.revision}")
    typer.echo(f"Root: {analysis_root}\n")
    typer.echo(f"{'Node':<24} {'Name':<26} {'Status'}")
    typer.echo("-" * 70)
    for node in plan.nodes:
        if node.id in state.completed_nodes:
            status = "✓ PASS"
            iters = state.phase_iterations.get(node.id, 1)
            if iters > 1:
                status += f"  ({iters} review iterations)"
        elif node.id == state.current_node:
            status = "→ IN PROGRESS"
        else:
            status = "○ pending"
        typer.echo(f"{node.id:<24} {node.label:<26} {status}")
    typer.echo()


plan_app = typer.Typer(name="plan", help="Inspect, validate and migrate the analysis plan.")
jfc_app.add_typer(plan_app)


@jfc_app.command("templates")
def jfc_templates() -> None:
    """List the built-in plan templates a new analysis can start from."""
    import textwrap

    from hepagent.plan.templates import describe_templates

    typer.echo("\nPlan templates:\n")
    for template_name, description in describe_templates():
        typer.echo(f"  {template_name}")
        for line in textwrap.wrap(description, width=78):
            typer.echo(f"      {line}")
        typer.echo()
    typer.echo(f"Default: {DEFAULT_TEMPLATE}\n")


async def _run_reviewed(name: str, base_dir: str, port: int, analysis):
    """Serve the plan editor alongside a run that waits for approval.

    One process and one event loop, so the browser's approve button and the
    orchestrator's `await gate.wait(...)` are the same latch.
    """
    import asyncio
    from pathlib import Path

    from hepagent.web.server import open_when_plan_exists, plan_editor_running

    root = Path(base_dir).resolve() / name
    url = f"http://127.0.0.1:{port}/plan/{name}"

    async with plan_editor_running(base_dir=base_dir, port=port):
        typer.echo(f"[jfc] review the plan at {url} — the run starts when you approve it")
        opener = asyncio.create_task(open_when_plan_exists(root, url))
        try:
            return await analysis(require_approval=True)
        finally:
            opener.cancel()


@plan_app.command("propose")
def jfc_plan_propose(
    name: str = typer.Option(..., "--name", "-n", help="Analysis name (short identifier)."),
    prompt_file: str = typer.Option(
        ..., "--prompt-file", "-p", help="Markdown file holding the physics prompt."
    ),
    analysis_type: str = typer.Option(
        "measurement", "--type", "-t", help="Analysis type: measurement or search."
    ),
    template: str = typer.Option(
        DEFAULT_TEMPLATE, "--template", help="Template the architect edits."
    ),
    model: str | None = typer.Option(None, "--model", help="Model override for the architect."),
    base_dir: str = typer.Option("analyses", "--base-dir", help="Parent directory for analyses."),
    out: str | None = typer.Option(
        None,
        "--out",
        help="Write the plan here. Defaults to the analysis directory when it exists, "
        "otherwise <name>-plan.json in the current directory.",
    ),
) -> None:
    """Have the architect propose an analysis plan from the physics prompt."""
    import asyncio
    import json
    from pathlib import Path

    from hepagent.agents.jfc.architect import propose_plan
    from hepagent.plan.report import to_table
    from hepagent.plan.store import save_plan

    p = Path(prompt_file)
    if not p.is_file():
        typer.echo(f"Error: prompt file not found: {prompt_file}", err=True)
        raise typer.Exit(code=1)

    model_provider, model_name = parse_model_spec(model)
    result = asyncio.run(
        propose_plan(
            physics_prompt=p.read_text(encoding="utf-8"),
            analysis_name=name,
            analysis_type=analysis_type,
            template=template,
            model_provider=model_provider,
            model_name=model_name,
        )
    )

    for note in result.notes:
        typer.echo(f"  {note}")
    if result.rationale:
        typer.echo(f"\nArchitect's reasoning:\n{result.rationale}\n")
    typer.echo(to_table(result.plan))

    analysis_root = Path(base_dir).resolve() / name
    if out:
        destination = Path(out)
        destination.write_text(json.dumps(result.plan.to_dict(), indent=2) + "\n", encoding="utf-8")
    elif analysis_root.is_dir():
        save_plan(analysis_root, result.plan)
        destination = analysis_root / "plan.json"
    else:
        destination = Path(f"{name}-plan.json")
        destination.write_text(json.dumps(result.plan.to_dict(), indent=2) + "\n", encoding="utf-8")

    typer.echo(f"\nPlan written to {destination}")
    if not analysis_root.is_dir():
        typer.echo(
            f"Review it, then run: hepagent jfc run --name {name} --type {analysis_type} "
            f"--prompt-file {prompt_file} --plan {destination}"
        )


@plan_app.command("show")
def jfc_plan_show(
    name: str = typer.Option(..., "--name", "-n", help="Analysis name."),
    base_dir: str = typer.Option("analyses", "--base-dir", help="Parent directory for analyses."),
    output_format: str = typer.Option(
        "table", "--format", "-f", help="Output format: table, mermaid or json."
    ),
) -> None:
    """Print the analysis plan as a table, a Mermaid diagram or raw JSON."""
    import json

    from hepagent.plan.report import to_mermaid, to_table

    plan = _load_plan(_analysis_root(name, base_dir))

    if output_format == "mermaid":
        typer.echo(to_mermaid(plan))
    elif output_format == "json":
        typer.echo(json.dumps(plan.to_dict(), indent=2))
    elif output_format == "table":
        typer.echo(to_table(plan))
    else:
        typer.echo(
            f"Error: unknown format '{output_format}'. Use table, mermaid or json.", err=True
        )
        raise typer.Exit(code=1)


@plan_app.command("edit")
def jfc_plan_edit(
    name: str = typer.Option(..., "--name", "-n", help="Analysis name."),
    base_dir: str = typer.Option("analyses", "--base-dir", help="Parent directory for analyses."),
    host: str = typer.Option("127.0.0.1", "--host", help="Interface to bind."),
    port: int = typer.Option(8001, "--port", help="Port to bind."),
    no_browser: bool = typer.Option(False, "--no-browser", help="Do not open a browser."),
    until_approved: bool = typer.Option(
        False, "--until-approved", help="Exit once the plan is approved, instead of serving on."
    ),
) -> None:
    """Open the plan editor in a browser. Serves until interrupted."""
    root = _analysis_root(name, base_dir)
    _load_plan(root)  # fail fast with a useful message rather than a blank page

    try:
        from hepagent.web.server import launch_plan_editor
    except ImportError as exc:  # pragma: no cover - depends on the optional extra
        typer.echo(
            "Error: the plan editor requires the 'web' extra. Install it with:\n"
            "  uv sync --all-extras",
            err=True,
        )
        raise typer.Exit(code=1) from exc

    typer.echo(f"Plan editor for '{name}': http://{host}:{port}/plan/{name}")
    if not until_approved:
        typer.echo("Press Ctrl-C to stop.")
    try:
        approved = launch_plan_editor(
            name,
            base_dir=base_dir,
            host=host,
            port=port,
            open_browser=not no_browser,
            wait_for_approval=until_approved,
        )
    except KeyboardInterrupt:
        typer.echo("\nEditor stopped.")
        return
    if until_approved and approved:
        typer.echo(f"Plan approved. Run it with: hepagent jfc run --name {name} ...")


@plan_app.command("validate")
def jfc_plan_validate(
    name: str = typer.Option(..., "--name", "-n", help="Analysis name."),
    base_dir: str = typer.Option("analyses", "--base-dir", help="Parent directory for analyses."),
) -> None:
    """Check the plan for consistency. Exits 1 when findings are blocking."""
    from hepagent.plan.validate import validate_plan

    report = validate_plan(_load_plan(_analysis_root(name, base_dir)))
    typer.echo(report.to_markdown())
    if report.blocking:
        raise typer.Exit(code=1)


@plan_app.command("migrate")
def jfc_plan_migrate(
    name: str = typer.Option(..., "--name", "-n", help="Analysis name."),
    base_dir: str = typer.Option("analyses", "--base-dir", help="Parent directory for analyses."),
    template: str = typer.Option(
        DEFAULT_TEMPLATE, "--template", help="Template whose node ids the legacy phases map onto."
    ),
    force: bool = typer.Option(False, "--force", help="Overwrite an existing plan.json."),
) -> None:
    """Give a pre-plan analysis directory a plan.json and node-keyed state."""
    from hepagent.plan.migrate import migrate_analysis

    root = _analysis_root(name, base_dir)
    try:
        result = migrate_analysis(root, template=template, force=force)
    except Exception as exc:  # noqa: BLE001 - turned into a CLI message
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    for line in result.notes:
        typer.echo(f"  {line}")
    typer.echo(f"Migrated '{name}': {len(result.plan.nodes)} nodes, plan.json written.")
    typer.echo(f"Next: hepagent jfc graph rebuild --name {name}")


graph_app = typer.Typer(name="graph", help="Inspect and rebuild the analysis provenance graph.")
jfc_app.add_typer(graph_app)


def _load_analysis_graph(name: str, base_dir: str):
    """Resolve an analysis name to its root and loaded graph, or exit."""
    from pathlib import Path

    from hepagent.graph.store import AnalysisGraph

    analysis_root = Path(base_dir).resolve() / name
    if not analysis_root.exists():
        typer.echo(f"Error: analysis not found: {analysis_root}", err=True)
        raise typer.Exit(code=1)
    return analysis_root, AnalysisGraph.load(analysis_root)


@graph_app.command("show")
def jfc_graph_show(
    name: str = typer.Option(..., "--name", "-n", help="Analysis name."),
    base_dir: str = typer.Option("analyses", "--base-dir", help="Parent directory for analyses."),
    output_format: str = typer.Option(
        "table", "--format", "-f", help="Output format: table or mermaid."
    ),
    node_type: str | None = typer.Option(
        None, "--type", "-t", help="Restrict to one node type (artifact, figure, commitment, ...)."
    ),
) -> None:
    """Print the analysis graph as a table or a Mermaid diagram."""
    from hepagent.graph.query import to_mermaid, to_table

    _root, graph = _load_analysis_graph(name, base_dir)
    if len(graph) == 0:
        typer.echo(f"No graph found for '{name}'. Run: hepagent jfc graph rebuild --name {name}")
        raise typer.Exit(code=1)

    if output_format == "mermaid":
        typer.echo(to_mermaid(graph, node_type=node_type))
    else:
        typer.echo(to_table(graph, node_type=node_type))


@graph_app.command("validate")
def jfc_graph_validate(
    name: str = typer.Option(..., "--name", "-n", help="Analysis name."),
    base_dir: str = typer.Option("analyses", "--base-dir", help="Parent directory for analyses."),
) -> None:
    """Check the analysis graph for consistency. Exits 1 when findings are blocking."""
    from hepagent.graph.validation import validate

    _root, graph = _load_analysis_graph(name, base_dir)
    report = validate(graph)
    typer.echo(report.to_markdown())
    if not report.ok:
        raise typer.Exit(code=1)


@graph_app.command("trace")
def jfc_graph_trace(
    name: str = typer.Option(..., "--name", "-n", help="Analysis name."),
    node: str = typer.Option(
        ..., "--node", help="Node id or file path, e.g. 'mjj.png' or 'figure:...'."
    ),
    base_dir: str = typer.Option("analyses", "--base-dir", help="Parent directory for analyses."),
) -> None:
    """Answer "what produced this?" for a node or file in the analysis."""
    from hepagent.graph.query import describe

    _root, graph = _load_analysis_graph(name, base_dir)
    target = graph.get_node(node)
    if target is None:
        matches = graph.find_by_content_ref(node)
        target = matches[0] if matches else None
    if target is None:
        typer.echo(f"No node found for '{node}' in analysis '{name}'.", err=True)
        raise typer.Exit(code=1)
    typer.echo(describe(graph, target))


@graph_app.command("rebuild")
def jfc_graph_rebuild(
    name: str = typer.Option(..., "--name", "-n", help="Analysis name."),
    base_dir: str = typer.Option("analyses", "--base-dir", help="Parent directory for analyses."),
) -> None:
    """Re-derive the graph from the artifacts on disk. Safe to run repeatedly."""
    from pathlib import Path

    from hepagent.agents.jfc.graph_builder import bootstrap_graph, rebuild

    analysis_root = Path(base_dir).resolve() / name
    if not analysis_root.exists():
        typer.echo(f"Error: analysis not found: {analysis_root}", err=True)
        raise typer.Exit(code=1)

    plan = _load_plan(analysis_root)

    # Seed the graph only when it is missing; bootstrap declares *pending*
    # placeholders and must not run over a graph that already has real nodes.
    if not (analysis_root / "graph" / "nodes.jsonl").exists():
        bootstrap_graph(analysis_root, plan)
    report = rebuild(analysis_root, plan)
    typer.echo(f"Rebuilt graph for '{name}': {report.summary()}")
    for note in report.skipped:
        typer.echo(f"  skipped: {note}")


@jfc_app.command("list")
def jfc_list(
    base_dir: str = typer.Option("analyses", "--base-dir", help="Parent directory for analyses."),
) -> None:
    """List JFC analyses in the analyses directory."""
    from pathlib import Path

    analyses_dir = Path(base_dir).resolve()
    if not analyses_dir.exists():
        typer.echo(f"No analyses directory found at {analyses_dir}")
        return

    analyses = [d for d in sorted(analyses_dir.iterdir()) if d.is_dir()]
    if not analyses:
        typer.echo(f"No analyses found in {analyses_dir}")
        return

    typer.echo(f"\nJFC Analyses in {analyses_dir}:\n")
    for analysis_dir in analyses:
        state_path = analysis_dir / ".orchestration_state.json"
        if state_path.exists():
            try:
                from hepagent.agents.jfc.orchestrator import load_state

                state = load_state(analysis_dir)
                total = len(_load_plan(analysis_dir).nodes)
                n_complete = len(state.completed_nodes)
                typer.echo(
                    f"  {analysis_dir.name:<30} node={state.current_node}  "
                    f"({n_complete}/{total} complete)"
                )
            except Exception:
                typer.echo(f"  {analysis_dir.name:<30} (state unreadable)")
        else:
            typer.echo(f"  {analysis_dir.name:<30} (no state)")


if __name__ == "__main__":
    app()
