import importlib
import inspect
import pkgutil

import click

import hepagent.cli
from agents.run import DEFAULT_MAX_TURNS
from hepagent.agents.common import AgentContext
from hepagent.agents.skilled import create as create_skilled_agent
from hepagent.agents.textual import AgentAdapter, TextualAgent
from hepagent.agents.textual_bash import BashToolWrapper
from hepagent.agents.textual_common import AskUserToolWrapper, CompositeToolWrapper
from hepagent.helpers import load_mlflow_for_tracing
from hepagent.model_providers import get_model_provider_settings, parse_model_spec


@click.group(invoke_without_command=True)
@click.option("--agent", "agent_name", default="research_scientist", show_default=True)
@click.option("--task", "task_prompt")
@click.option("--yolo", is_flag=True, help="Auto-approve all bash commands.")
@click.option(
    "--max-turn",
    "max_turns",
    type=int,
    default=DEFAULT_MAX_TURNS,
    help="Maximum number of agent turns (defaults to SDK default).",
)
@click.option(
    "--model",
    default=None,
    show_default="cborg default",
    help='Specify the model as "provider:model" (e.g. "openai:gpt-5-mini") '
    "or a bare model name (defaults to cborg).",
)
@click.pass_context
def main(
    ctx: click.Context,
    agent_name: str,
    task_prompt: str | None,
    yolo: bool,
    max_turns: int = DEFAULT_MAX_TURNS,
    model: str | None = None,
) -> None:
    """HepAgent: A framework for building and deploying AI agents in HEP."""
    if ctx.invoked_subcommand is not None:
        return

    if not task_prompt:
        raise click.UsageError("Missing required option '--task'.")

    # check if ML flow is available.
    # If so, use it for log traces.
    if load_mlflow_for_tracing():
        click.echo("MLflow found. Using MLflow for tracing.")
        import mlflow

        mlflow.openai.autolog()

    model_provider, model_name = parse_model_spec(model)
    agent = create_skilled_agent(model_provider=model_provider, model_name=model_name)
    context = AgentContext(agent_name=agent_name)
    display_model = model_name or get_model_provider_settings(model_provider).default_model
    app = TextualAgent(model=display_model, env={})

    # Wrap the bash agent with our adapter
    wrapper = CompositeToolWrapper(BashToolWrapper(), AskUserToolWrapper())
    app.agent = AgentAdapter(agent, app, tool_wrapper=wrapper)
    if yolo:
        app.agent.config.mode = "yolo"
    exit_status, result = app.run_task(task=task_prompt, context=context, max_turns=max_turns)
    print(f"Agent exited with status: {exit_status}, result: {result}")


@main.command("list-models")
@click.option(
    "--platform",
    "platform",
    default="cborg",
    show_default=True,
    help="Platform to query (cborg, amsc, openai).",
)
def list_models(platform: str = "cborg") -> None:
    """List available models for a provider."""
    from openai import OpenAI

    settings = get_model_provider_settings(platform)
    if not settings.api_key:
        raise click.ClickException(f"{settings.api_key_env} is not set.")

    click.echo(f"Available models for {platform}:")
    client = OpenAI(base_url=settings.base_url, api_key=settings.api_key)
    models = client.models.list()
    names = sorted(model.id for model in models.data)
    for name in names:
        click.echo("\t" + name)


@main.command("list-cborg-models")
def list_cborg_models() -> None:
    """List available CBORG models (use list-models instead)."""
    list_models("cborg")


# --- Auto-discover subcommands ---
def _register_commands():
    package = hepagent.cli

    for _, module_name, _ in pkgutil.iter_modules(package.__path__):  # type: ignore
        if module_name == "main":
            continue  # skip main.py itself
        module = importlib.import_module(f"{package.__name__}.{module_name}")  # type: ignore

        # find all click commands in the module
        for _obj_name, obj in inspect.getmembers(module):
            if isinstance(obj, click.core.Command):
                main.add_command(obj)


# Run discovery
_register_commands()
