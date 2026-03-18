from __future__ import annotations

from pathlib import Path

import click
import typer
from typer.core import TyperGroup

from agents import SQLiteSession
from agents.run import DEFAULT_MAX_TURNS
from hepagent.agents.common import AgentContext
from hepagent.agents.skilled import create as create_skilled_agent
from hepagent.agents.textual import AgentAdapter, TextualAgent
from hepagent.agents.textual_bash import BashToolWrapper
from hepagent.agents.textual_common import AskUserToolWrapper, CompositeToolWrapper
from hepagent.config.env import env_config
from hepagent.helpers import enable_mlflow_for_tracing, get_agent_dir
from hepagent.model_providers import get_model_provider_settings, parse_model_spec


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
    db_dir = get_agent_dir() / "sessions"
    db_dir.mkdir(parents=True, exist_ok=True)
    db_path = db_dir / "conversation.db"
    return SQLiteSession(conversation_id, str(db_path))


@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    agent_name: str = typer.Option(
        "research_scientist",
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
    agent_name: str | None = typer.Option(
        None,
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
    agent_name = agent_name or str(options.get("agent_name", "research_scientist"))
    yolo = yolo or bool(options.get("yolo", False))
    max_turns = max_turns or int(options.get("max_turns", DEFAULT_MAX_TURNS))
    model = model or options.get("model")
    chat = chat or options.get("chat")

    if env_config.use_mlflow_tracing and enable_mlflow_for_tracing():
        typer.echo("MLflow found. Using MLflow for tracing.")
        import mlflow

        mlflow.openai.autolog()

    model_provider, model_name = parse_model_spec(model)
    agent = create_skilled_agent(model_provider=model_provider, model_name=model_name)
    context = AgentContext(agent_name=agent_name)
    display_model = model_name or get_model_provider_settings(model_provider).default_model
    app_agent = TextualAgent(model=display_model, env={})

    wrapper = CompositeToolWrapper(BashToolWrapper(), AskUserToolWrapper())
    app_agent.agent = AgentAdapter(agent, app_agent, tool_wrapper=wrapper)

    if yolo:
        app_agent.agent.config.mode = "yolo"

    session = create_chat_session(chat) if chat else None
    exit_status, result = app_agent.run_task(
        task=task_prompt,
        context=context,
        max_turns=max_turns,
        session=session,
    )
    typer.echo(f"Agent exited with status: {exit_status}, result: {result}")


@app.command("list-models")
def list_models(
    platform: str = typer.Option(
        "cborg",
        "--platform",
        "-p",
        show_default=True,
        help="Platform to query (cborg, amsc, openai).",
    ),
) -> None:
    """List available models for a provider."""
    from openai import OpenAI

    settings = get_model_provider_settings(platform)
    if not settings.api_key:
        typer.echo(f"{settings.api_key_env} is not set.")
        raise typer.Exit(code=1)

    typer.echo(f"Available models for {platform}:")
    client = OpenAI(base_url=settings.base_url, api_key=settings.api_key)
    models = client.models.list()
    names = sorted(model.id for model in models.data)
    for name in names:
        typer.echo("\t" + name)


@app.command("list-cborg-models")
def list_cborg_models() -> None:
    """List available CBORG models (use list-models instead)."""
    list_models("cborg")


if __name__ == "__main__":
    app()
