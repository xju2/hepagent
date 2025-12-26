"""Bash agent that solve problems by running bash commands.
Adapted from min-swe-agent:
https://github.com/SWE-agent/mini-swe-agent/blob/main/src/minisweagent/agents/default.py
"""

from pydantic import BaseModel
from agents import Agent
from agents import function_tool
from hepagent.model_providers import get_cborg_model_provider
import subprocess


class LocalEnvironmentConfig(BaseModel):
    cwd: str = ""
    env: dict[str, str] = {}
    timeout: int = 30


@function_tool
def execute_bash_command(cmd: str) -> dict:
    """Execute a bash command and return the output and return code."""
    config = LocalEnvironmentConfig()

    result = subprocess.run(
        cmd,
        shell=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=config.timeout,
    )
    return {"output": result.stdout, "returncode": result.returncode}


def create() -> Agent:
    agent = Agent(
        name="Bash Agent",
        instructions=(
            "You are a helpful assistant that can interact multiple times with a computer shell to solve programming tasks."
            "Your response must contain exactly ONE bash code block with ONE command (or commands connected with && or ||)."
            "Include a THOUGHT section before your command where you explain your reasoning process."
            "Format your response as shown in <format_example>."
            "<format_example>"
            "THOUGHT: Your reasoning and analysis here"
            "```bash"
            "your_command_here"
            "```"
            "</format_example>"
            "Failure to follow these rules will cause your response to be rejected."
        ),
        model=get_cborg_model_provider(),
        tools=[execute_bash_command],
    )
    return agent


async def main():
    from agents import Runner

    task_prompt = "List the files in the current directory and tell me how many there are."
    agent = create()
    result = await Runner.run(agent, task_prompt)
    print(result.final_output)


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
