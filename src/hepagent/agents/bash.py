"""Bash agent that solves problems by running shell commands.
Adapted from min-swe-agent:
https://github.com/SWE-agent/mini-swe-agent/blob/main/src/minisweagent/agents/default.py
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from pydantic import BaseModel

from agents import Agent, function_tool
from hepagent.config import env_config
from hepagent.utils.model_providers import get_model_provider


class LocalEnvironmentConfig(BaseModel):
    cwd: str = ""
    env: dict[str, str] = {}
    timeout: int | None = None


def _truncate_output(output: str, limit: int) -> str:
    words = output.split()
    if len(words) <= limit:
        return output
    omitted = len(words) - limit
    return " ".join(words[:limit]) + f"\n\n[output truncated: omitted {omitted} words]"


def execute_bash_command(cmd: str, cwd: str = "") -> dict:
    """Execute a bash command and return output + return code."""
    config = LocalEnvironmentConfig()
    cwd = cwd or config.cwd or str(Path.cwd())

    result = subprocess.run(
        cmd,
        shell=True,
        text=True,
        cwd=cwd,
        env=os.environ | config.env,
        timeout=config.timeout,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    return {
        "output": _truncate_output(result.stdout, env_config.output_word_limit),
        "returncode": result.returncode,
    }


TOOL_CANCEL_MESSAGE = """Tool calling is cancelled by user.
Here is the reason: {reason}.
Stop thinking!
Tell users what was your plan to justify the tool calling
and suggest user running the request again if needed."""


@function_tool
def execute_bash_command_with_confirmation(cmd: str, cwd: str = "", thought: str = "") -> dict:
    """Execute a bash command with optional confirmation and return output."""

    print(f"THOUGHT:{thought}", flush=True)
    print(f"About to execute command:\n\tcmd={cmd}\n\tcwd={cwd}", flush=True)

    if env_config.yolo_mode:
        return execute_bash_command(cmd, cwd=cwd)

    prompt = (
        "⚠️ Agent called tool in HUMAN mode. Allow?\n(Enter 'y' to allow, type reason to reject): "
    )
    try:
        confirmation = input(prompt)
    except EOFError:
        try:
            with open("/dev/tty", encoding="utf-8") as tty:
                print(prompt, end="", flush=True)
                confirmation = tty.readline().strip()
        except OSError:
            confirmation = ""

    if confirmation.lower() != "y":
        return {"output": TOOL_CANCEL_MESSAGE.format(reason=confirmation), "returncode": 1}

    results = execute_bash_command(cmd, cwd=cwd)
    print(f"Command return code:\t{results['returncode']}")
    return results


def create(
    model_provider: str = "cborg",
    model_name: str | None = None,
) -> Agent:
    agent = Agent(
        name="Bash Agent",
        instructions=(
            "You are a helpful assistant that can interact multiple times with a computer shell "
            "to solve programming tasks."
            "When you need to execute a tool call, "
            "your response must contain exactly ONE bash code block "
            "with ONE command (or commands connected with && or ||)."
            "When the task is complete, provide a concise final summary in plain text."
            "Read AGENTS.md early when present in the working tree or parents."
            "Include a THOUGHT section before your command to explain your reasoning process."
            "Format your response as shown in <format_example>."
            "<format_example>"
            "THOUGHT: Your reasoning and analysis here"
            "```bash"
            "your_command_here"
            "```"
            "</format_example>"
            "Avoid repeated reasoning loops and keep THOUGHT concise."
            "When reporting executed commands, use get_execution_journal for grounding."
            "Failure to follow these rules will cause your response to be rejected."
        ),
        model=get_model_provider(model_provider=model_provider, model_name=model_name),
        tools=[execute_bash_command_with_confirmation],
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
