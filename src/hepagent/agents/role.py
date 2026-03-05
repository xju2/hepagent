"""Role-based agent creation."""

from __future__ import annotations

import os
import platform

from distro import name as distro_name
from pydantic import BaseModel

from agents import Agent
from hepagent.model_providers import get_model_provider


class RoleAgentConfig(BaseModel):
    name: str
    instructions: str
    variables: dict[str, str] | None = None
    tools: list[str] | None = None


DEFAULT_ROLE = """You are programming and system administration assistant.
You are managing {os} operating system with {shell} shell.
Provide short responses in about 100 words, unless you are specifically asked for more details.
If you need to store any data, assume it will be stored in the conversation.
APPLY MARKDOWN formatting when possible."""

ROLE_TEMPLATE = "You are {name}\n{role}"


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


role_agents = {
    "system_admin": RoleAgentConfig(
        name="System Administrator",
        instructions=ROLE_TEMPLATE.format(name="System Administrator", role=DEFAULT_ROLE),
        variables={"os": _os_name(), "shell": _shell_name()},
        tools=[],
    ),
}


def create(
    role_name: str = "system_admin",
    model_provider: str = "cborg",
    model_name: str | None = None,
) -> Agent:
    role_config = role_agents.get(role_name)
    if not role_config:
        raise ValueError(f"Role '{role_name}' not found.")

    instructions = role_config.instructions
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


async def main(task: str):
    from agents import Runner

    agent = create(role_name="system_admin")
    result = await Runner.run(agent, task)
    print(result.final_output)


if __name__ == "__main__":
    import asyncio
    import sys

    if len(sys.argv) != 2:
        print("Usage: python role.py '<task>'")
        sys.exit(1)

    task = sys.argv[1]
    asyncio.run(main(task))
