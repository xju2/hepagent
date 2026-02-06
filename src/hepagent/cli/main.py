import importlib
import inspect
import pkgutil

import click

import hepagent.cli


@click.group()
def main():
    """HepAgent: A framework for building and deploying AI agents in HEP."""


# --- Auto-discover subcommands ---
def _register_commands():
    package = hepagent.cli

    for _, module_name, _ in pkgutil.iter_modules(package.__path__):  # type: ignore
        if module_name == "main":
            continue  # skip main.py itself
        module = importlib.import_module(f"{package.__name__}.{module_name}")  # type: ignore

        # find all click commands in the module
        for obj_name, obj in inspect.getmembers(module):
            if isinstance(obj, click.core.Command):
                main.add_command(obj)


# Run discovery
_register_commands()
