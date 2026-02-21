"""Bash agent that solve problems by running bash commands.
Adapted from min-swe-agent:
https://github.com/SWE-agent/mini-swe-agent/blob/main/src/minisweagent/agents/default.py
"""

import os
import re
import subprocess
from pathlib import Path

from pydantic import BaseModel

from agents import Agent, function_tool
from hepagent.agents.common import OUTPUT_TRUNCATE_LENGTH
from hepagent.model_providers import get_model_provider


class LocalEnvironmentConfig(BaseModel):
    cwd: str = ""
    env: dict[str, str] = {}
    timeout: int = 30


def _get_output_limit() -> int:
    env_limit = os.getenv("HEPAGENT_OUTPUT_CHAR_LIMIT", "").strip()
    if env_limit:
        try:
            parsed = int(env_limit)
            if parsed > 0:
                return parsed
        except ValueError:
            pass
    # Keep larger than UI truncation so the model still gets enough context.
    return max(OUTPUT_TRUNCATE_LENGTH, 8000)


def _truncate_output(output: str, limit: int) -> str:
    if len(output) <= limit:
        return output
    omitted = len(output) - limit
    return output[:limit] + f"\n\n[output truncated: omitted {omitted} chars]"


def _broad_scan_reason(cmd: str) -> str | None:
    normalized = " ".join(cmd.strip().split())

    patterns = [
        (
            r"(^|[;&|]\s*)ls\s+-[^\n]*R\b|(^|[;&|]\s*)ls\b[^\n]*\s-R\b",
            "recursive 'ls -R' can explode on large trees",
        ),
        (
            r"(^|[;&|]\s*)find\s+\.(\s|$)(?![^\n]*-maxdepth\b)",
            "'find .' without -maxdepth scans the entire tree",
        ),
        (
            r"(^|[;&|]\s*)rg\s+--files(\s+\.|\s*$)",
            "'rg --files' at repo root can enumerate very large trees",
        ),
        (
            r"(^|[;&|]\s*)tree(\s|$)(?![^\n]*\s-L\s*\d+\b)",
            "'tree' without depth limit can be very large",
        ),
    ]
    for pattern, reason in patterns:
        if re.search(pattern, normalized):
            return reason
    return None


def execute_bash_command(cmd: str, cwd: str = "") -> dict:
    """Execute a bash command and return the output and return code."""
    config = LocalEnvironmentConfig()
    cwd = cwd or config.cwd or str(Path.cwd())

    if os.getenv("HEPAGENT_ALLOW_BROAD_SCAN") != "1":
        reason = _broad_scan_reason(cmd)
        if reason:
            return {
                "output": (
                    f"Command blocked by safety guard: {reason}.\n"
                    "Use a narrower command (target specific path, add depth/output limits)."
                ),
                "returncode": 2,
            }

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
    return {"output": _truncate_output(result.stdout, _get_output_limit()), "returncode": result.returncode}


TOOL_CANCEL_MESSAGE = """Tool calling is cancelled by user.
Here is the reason: {reason}.
Stop thinking!
Tell users what was your plan to justify the tool calling
and suggest user running the request again if needed."""


@function_tool
def execute_bash_command_with_confirmation(cmd: str, cwd: str = "", thought: str = "") -> dict:
    """Only execute a bash command with user's confirmation and return the output."""

    # print the thought and the command to be executed for user's review.
    print(f"THOUGHT:{thought}", flush=True)
    print(f"About to execute command:\n\tcmd={cmd}\n\tcwd={cwd}", flush=True)

    if os.getenv("HEPAGENT_YOLO") == "1":
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
            "Your response must contain exactly ONE bash code block with ONE command (or commands"
            " connected with && or ||)."
            "Include a THOUGHT section before your command "
            "where you explain your reasoning process."
            "Format your response as shown in <format_example>."
            "<format_example>"
            "THOUGHT: Your reasoning and analysis here"
            "```bash"
            "your_command_here"
            "```"
            "</format_example>"
            "Do not run unbounded filesystem discovery commands. Avoid recursive scans like 'ls -R',"
            " 'find .' without -maxdepth, and 'rg --files' at repo root."
            "Always scope discovery to a specific path and limit output, for example with"
            " '-maxdepth' or '| head -n N'."
            "If a required path is missing or a command returns 'No such file or directory',"
            " do not probe sibling/top-level directories to guess."
            " Instead, ask the user for the correct path or permission to search."
            "Do not stop after reading initial files when the task includes required execution steps."
            " Continue with the next required step, or explicitly ask one blocking clarification question."
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
