"""Role-based agent creation.
Adopted from shell_gpt:
https://github.com/TheR1D/shell_gpt/blob/main/sgpt/role.py
"""

from __future__ import annotations

import os
import platform
from functools import lru_cache

from distro import name as distro_name
from pydantic import BaseModel

from agents import Agent
from hepagent.model_providers import get_model_provider


class RoleAgentConfig(BaseModel):
    name: str
    role: str
    variables: dict[str, str] | None = None
    tools: list[str] | None = None


SHELL_ROLE = """Provide only {shell} commands for {os} without any description.
If there is a lack of details, provide most logical solution.
Ensure the output is a valid shell command.
If multiple steps required try to combine them together using &&.
Provide only plain text without Markdown formatting.
Do not provide markdown formatting such as ```.
"""

DESCRIBE_SHELL_ROLE = """Provide a terse, single sentence description of the given shell command.
Describe each argument and option of the command.
Provide short responses in about 80 words.
APPLY MARKDOWN formatting when possible."""
# Note that output for all roles containing "APPLY MARKDOWN" will be formatted as Markdown.

CODE_ROLE = """Provide only code as output without any description.
Provide only code in plain text format without Markdown formatting.
Do not include symbols such as ``` or ```python.
If there is a lack of details, provide most logical solution.
You are not allowed to ask for more details.
For example if the prompt is "Hello world Python", you should return "print('Hello world')"."""

DEFAULT_ROLE = """You are programming and system administration assistant.
You are managing {os} operating system with {shell} shell.
Provide short responses in about 100 words, unless you are specifically asked for more details.
If you need to store any data, assume it will be stored in the conversation.
APPLY MARKDOWN formatting when possible."""

ROLE_TEMPLATE = "You are {name}\nYour maximum thinking turns are TWO. "
"Try to provide final outputs with only one turn.\n{role}"


def _shell_name() -> str:
    current_platform = platform.system()
    if current_platform in ("Windows", "nt"):
        is_powershell = len(os.getenv("PSModulePath", "").split(os.pathsep)) >= 3
        return "powershell.exe" if is_powershell else "cmd.exe"
    return os.path.basename(os.getenv("SHELL", "/bin/sh"))


def _os_name() -> str:
    current_platform = platform.system()
    if current_platform == "Linux":
        return "Linux/" + distro_name(pretty=True)
    if current_platform == "Windows":
        return "Windows " + platform.release()
    if current_platform == "Darwin":
        return "Darwin/MacOS " + platform.mac_ver()[0]
    return current_platform


@lru_cache
def create_role_cfg() -> dict[str, RoleAgentConfig]:
    role_agents = {
        "ShellGPT": RoleAgentConfig(
            name="ShellGPT",
            role=DEFAULT_ROLE,
            variables={"os": _os_name(), "shell": _shell_name()},
            tools=[],
        ),
        "ShellCommandGenerator": RoleAgentConfig(
            name="Shell Command Generator",
            role=SHELL_ROLE,
            variables={"os": _os_name(), "shell": _shell_name()},
            tools=[],
        ),
        "ShellCommandDescriber": RoleAgentConfig(
            name="Shell Command Describer",
            role=DESCRIBE_SHELL_ROLE,
            variables={},
            tools=[],
        ),
        "CodeGenerator": RoleAgentConfig(
            name="Code Generator",
            role=CODE_ROLE,
            variables={},
            tools=[],
        ),
    }
    return role_agents


def create(
    role_name: str = "ShellGPT",
    model_provider: str = "cborg",
    model_name: str | None = None,
) -> Agent:
    role_config = create_role_cfg().get(role_name)
    if not role_config:
        raise ValueError(f"Role '{role_name}' not found.")

    instructions = ROLE_TEMPLATE.format(name=role_config.name, role=role_config.role)
    if role_config.variables:
        instructions = instructions.format(**role_config.variables)

    tools = []
    if role_config.tools:
        from hepagent.tools import common

        for tool in role_config.tools:
            if hasattr(common, tool):
                tools.append(getattr(common, tool))
            else:
                raise ValueError(f"Tool '{tool}' not found in common tools.")

    agent = Agent(
        name=role_config.name,
        instructions=instructions,
        model=get_model_provider(model_provider=model_provider, model_name=model_name),
        tools=tools,
    )
    return agent


async def main(task: str, role_name: str = "ShellGPT"):
    from agents import Runner

    agent = create(role_name=role_name)
    result = await Runner.run(agent, task, max_turns=2)
    print(result.final_output)


if __name__ == "__main__":
    import argparse
    import asyncio

    parser = argparse.ArgumentParser(description="Run role-based agent")
    parser.add_argument("task", help="Task to perform")
    parser.add_argument(
        "-s", "--shell", help="Generate and execute shell commands", action="store_true"
    )
    parser.add_argument("-d", "--describe", help="Describe shell commands", action="store_true")
    parser.add_argument("-c", "--code", help="Generate code snippets", action="store_true")

    args = parser.parse_args()

    role_name = "ShellGPT"
    if args.shell:
        role_name = "ShellCommandGenerator"
    elif args.describe:
        role_name = "ShellCommandDescriber"
    elif args.code:
        role_name = "CodeGenerator"
    task = args.task
    asyncio.run(main(task, role_name=role_name))
