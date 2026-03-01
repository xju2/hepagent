"""CLI subcommand to install the function_tools directory."""

import shutil
from pathlib import Path

import click

from hepagent.tools.user_tools import get_function_tools_dir


@click.command("install-functions")
@click.argument("source", required=False, type=click.Path(exists=True, path_type=Path))
def install_functions(source: Path | None = None) -> None:
    """Set up the function_tools directory for user-defined tools.

    Optionally copies Python files from SOURCE into the function_tools directory.
    If SOURCE is omitted, the directory is created and its location is printed.
    """
    target_dir = get_function_tools_dir()
    target_dir.mkdir(parents=True, exist_ok=True)

    if source is not None:
        source_files = list(source.glob("*.py")) if source.is_dir() else [source]
        copied = 0
        for py_file in source_files:
            if py_file.suffix == ".py":
                dest = target_dir / py_file.name
                if dest.exists():
                    click.echo(f"  Skipped (already exists): {py_file.name}")
                    continue
                shutil.copy2(py_file, dest)
                click.echo(f"  Installed: {py_file.name} -> {dest}")
                copied += 1
        click.echo(f"Installed {copied} file(s) into {target_dir}")
    else:
        click.echo(f"function_tools directory ready at: {target_dir}")
        click.echo("Drop Python files there to add custom function tools to the agent.")
