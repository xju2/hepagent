from __future__ import annotations

import uuid

import click
import typer
from typer.core import TyperGroup

from agents import SQLiteSession
from agents.run import DEFAULT_MAX_TURNS
from hepagent.agents.cli_repl import CliRepl
from hepagent.agents.common import AgentContext
from hepagent.agents.explorer import create as create_explorer_agent
from hepagent.agents.role import create as create_role_agent, create_role_cfg
from hepagent.agents.skilled import create as create_skilled_agent
from hepagent.agents.textual import AgentAdapter, TextualAgent
from hepagent.agents.textual_bash import BashToolWrapper
from hepagent.agents.textual_common import AskUserToolWrapper, CompositeToolWrapper
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
    max_turns: int = typer.Option(
        DEFAULT_MAX_TURNS,
        "--max-turn",
        help="Maximum number of agent turns (defaults to SDK default).",
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
        "max_turns": max_turns,
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
) -> None:
    """Run HepAgent with a task prompt."""
    options = ctx.obj or {}
    agent_name = agent_name.lower() or str(options.get("agent_name", "scientist"))
    yolo = yolo or bool(options.get("yolo", False))
    max_turns = max_turns or int(options.get("max_turns", DEFAULT_MAX_TURNS))
    model = model or options.get("model")
    chat = chat or options.get("chat")

    if env_config.use_mlflow_tracing and enable_mlflow_for_tracing():
        typer.echo("MLflow found. Using MLflow for tracing.")
        import mlflow

        mlflow.openai.autolog()

    runtime = build_runtime(agent_name=agent_name, model=model, chat=chat)
    agent = runtime["agent"]
    context = runtime["context"]
    display_model = runtime["display_model"]
    app_agent = TextualAgent(model=display_model, env={})

    wrapper = CompositeToolWrapper(BashToolWrapper(), AskUserToolWrapper())
    app_agent.agent = AgentAdapter(agent, app_agent, tool_wrapper=wrapper)

    if yolo:
        app_agent.agent.config.mode = "yolo"

    exit_status, result = app_agent.run_task(
        task=task_prompt,
        context=context,
        max_turns=max_turns,
        session=runtime["session"],
    )
    typer.echo(f"Agent exited with status: {exit_status}, result: {result}")


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
) -> None:
    """Start the browser-based chat UI."""
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
        3, "--max-iterations", help="Max review iterations per phase."
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
            "Enable human codesign review after Phase 1 PASS: generates a strategy summary, "
            "facilitates interactive Q&A, then re-adjudicates with the arbiter before Phase 2."
        ),
    ),
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

    model_provider, model_name = parse_model_spec(model)

    def _cb(phase: str, status: str) -> None:
        typer.echo(f"[jfc] phase={phase} {status}")

    try:
        pdf = asyncio.run(
            run_jfc_analysis(
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
            )
        )
        typer.echo(f"Analysis complete. Final PDF: {pdf}")
    except MaxIterationsExceeded as e:
        typer.echo(f"Error: {e}", err=True)
        typer.echo(f"Resume with: hepagent jfc resume --name {name}", err=True)
        raise typer.Exit(code=1) from e
    except PhaseEscalationError as e:
        typer.echo(f"Escalation at phase {e.phase}: {e}", err=True)
        raise typer.Exit(code=2) from e
    except KeyboardInterrupt as e:
        typer.echo("\nInterrupted. State saved. Resume with: hepagent jfc resume --name {name}")
        raise typer.Exit(code=130) from e


@jfc_app.command("resume")
def jfc_resume(
    name: str = typer.Option(..., "--name", "-n", help="Analysis name to resume."),
    from_phase: str = typer.Option(
        ..., "--from-phase", help="Phase to start from, e.g. '3' or '4a'"
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
    """Resume an interrupted JFC analysis from a specific phase."""
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
    if state_path.exists():
        s = load_state(analysis_root)
        analysis_type = s.analysis_type
        if not model_provider or model_provider == "cborg":
            model_provider = s.model_provider
            model_name = model_name or s.model_name

    # Parse phase
    try:
        start_phase: int | str = int(from_phase)
    except ValueError:
        start_phase = from_phase

    def _cb(phase: str, status: str) -> None:
        typer.echo(f"[jfc] phase={phase} {status}")

    try:
        pdf = asyncio.run(
            run_jfc_analysis(
                analysis_name=name,
                physics_prompt=prompt,
                analysis_type=analysis_type,  # type: ignore[arg-type]
                base_dir=base_dir,
                model_provider=model_provider,
                model_name=model_name,
                start_from_phase=start_phase,
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
    """Show the current phase status of a JFC analysis."""
    from pathlib import Path

    from hepagent.agents.jfc.orchestrator import PHASE_ORDER, load_state

    analysis_root = Path(base_dir).resolve() / name
    if not analysis_root.exists():
        typer.echo(f"Error: analysis not found: {analysis_root}", err=True)
        raise typer.Exit(code=1)

    state_path = analysis_root / ".orchestration_state.json"
    if not state_path.exists():
        typer.echo(f"No orchestration state found for '{name}'.")
        raise typer.Exit(code=1)

    state = load_state(analysis_root)

    phase_names = {
        "1": "Strategy",
        "2": "Exploration",
        "3": "Processing",
        "4a": "Expected Results",
        "4b": "10% Validation",
        "4c": "Full Data",
        "5": "Documentation",
    }

    typer.echo(f"\nJFC Analysis: {name}")
    typer.echo(f"Type: {state.analysis_type}")
    typer.echo(f"Root: {analysis_root}\n")
    typer.echo(f"{'Phase':<6} {'Name':<22} {'Status'}")
    typer.echo("-" * 50)
    for phase in PHASE_ORDER:
        key = str(phase)
        pname = phase_names.get(key, key)
        if key in state.completed_phases:
            status = "✓ PASS"
            iters = state.phase_iterations.get(key, 1)
            if iters > 1:
                status += f"  ({iters} review iterations)"
        elif key == state.current_subphase:
            status = "→ IN PROGRESS"
        else:
            status = "○ pending"
        typer.echo(f"{key:<6} {pname:<22} {status}")
    typer.echo()


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
                current = state.current_subphase
                n_complete = len(state.completed_phases)
                typer.echo(f"  {analysis_dir.name:<30} phase={current}  ({n_complete}/7 complete)")
            except Exception:
                typer.echo(f"  {analysis_dir.name:<30} (state unreadable)")
        else:
            typer.echo(f"  {analysis_dir.name:<30} (no state)")


if __name__ == "__main__":
    app()
