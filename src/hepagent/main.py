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


if __name__ == "__main__":
    app()
