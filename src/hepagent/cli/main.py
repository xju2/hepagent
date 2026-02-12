import importlib
import inspect
import os
import pkgutil

import click

import hepagent.cli
from hepagent.agents.common import AgentContext
from hepagent.agents.skilled import create as create_skilled_agent


@click.group(invoke_without_command=True)
@click.option("--agent", "agent_name", default="nyx", show_default=True)
@click.option("--task", "task_prompt")
@click.option("--yolo", is_flag=True, help="Auto-approve all bash commands.")
@click.pass_context
def main(ctx: click.Context, agent_name: str, task_prompt: str | None, yolo: bool):
    """HepAgent: A framework for building and deploying AI agents in HEP."""
    if ctx.invoked_subcommand is not None:
        return

    if not task_prompt:
        raise click.UsageError("Missing required option '--task'.")

    if yolo:
        os.environ["HEPAGENT_YOLO"] = "1"

    import asyncio

    from agents import Runner

    async def _run():
        agent = create_skilled_agent()
        context = AgentContext(agent_name=agent_name)
        result = await Runner.run(agent, task_prompt, context=context)
        click.echo(result.final_output)

    asyncio.run(_run())


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
