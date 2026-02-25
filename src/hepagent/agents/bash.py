"""Bash agent that solves problems by running shell commands."""

from __future__ import annotations

import os
import subprocess
import threading
import time
from pathlib import Path

from pydantic import BaseModel

from agents import Agent, function_tool
from hepagent.agents.common import OUTPUT_TRUNCATE_LENGTH
from hepagent.model_providers import get_model_provider


class LocalEnvironmentConfig(BaseModel):
    cwd: str = ""
    env: dict[str, str] = {}
    timeout: int | None = None


_EXECUTION_JOURNAL: list[dict[str, object]] = []
_EXECUTION_JOURNAL_LOCK = threading.Lock()


def _append_execution_journal(cmd: str, cwd: str, returncode: int, status: str) -> None:
    with _EXECUTION_JOURNAL_LOCK:
        _EXECUTION_JOURNAL.append(
            {
                "timestamp": time.time(),
                "cmd": cmd,
                "cwd": cwd,
                "returncode": int(returncode),
                "status": status,
            }
        )


def _snapshot_execution_journal(limit: int = 50) -> list[dict[str, object]]:
    with _EXECUTION_JOURNAL_LOCK:
        if limit <= 0:
            return []
        return list(_EXECUTION_JOURNAL[-limit:])


def _reset_execution_journal_for_tests() -> None:
    with _EXECUTION_JOURNAL_LOCK:
        _EXECUTION_JOURNAL.clear()


def _get_output_limit() -> int:
    raw = os.getenv("HEPAGENT_OUTPUT_CHAR_LIMIT", "").strip()
    if raw:
        try:
            parsed = int(raw)
            if parsed > 0:
                return parsed
        except ValueError:
            pass
    return max(OUTPUT_TRUNCATE_LENGTH, 8000)


def _truncate_output(output: str, limit: int) -> str:
    if len(output) <= limit:
        return output
    omitted = len(output) - limit
    return output[:limit] + f"\n\n[output truncated: omitted {omitted} chars]"


def _classify_tool_result(result: dict) -> str:
    code = int(result.get("returncode", 1))
    out = str(result.get("output", ""))
    if code == 0:
        return "executed"
    if "Tool calling is cancelled by user." in out:
        return "rejected_by_user"
    return "failed"


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
        "output": _truncate_output(result.stdout, _get_output_limit()),
        "returncode": result.returncode,
    }


TOOL_CANCEL_MESSAGE = """Tool calling is cancelled by user.
Here is the reason: {reason}.
Stop thinking!
Tell users what was your plan to justify the tool calling
and suggest user running the request again if needed."""


def _normalize_escaped_heredoc_newlines(cmd: str) -> str:
    """Convert literal '\\n' sequences to real newlines for heredoc-shaped commands."""
    if "\\n" not in cmd or "\n" in cmd:
        return cmd
    if "<<" not in cmd:
        return cmd
    return cmd.replace("\\n", "\n")


@function_tool
def get_execution_journal(limit: int = 50) -> dict:
    """Return recent command execution journal entries for grounded reporting."""
    safe_limit = max(1, min(int(limit), 200))
    entries = _snapshot_execution_journal(safe_limit)
    return {"count": len(entries), "entries": entries}


@function_tool
def execute_bash_command_with_confirmation(cmd: str, cwd: str = "", thought: str = "") -> dict:
    """Execute a bash command with optional confirmation and return output."""
    cmd = _normalize_escaped_heredoc_newlines(cmd)

    print(f"THOUGHT:{thought}", flush=True)
    print(f"About to execute command:\n\tcmd={cmd}\n\tcwd={cwd}", flush=True)

    if os.getenv("HEPAGENT_YOLO") == "1":
        results = execute_bash_command(cmd, cwd=cwd)
        _append_execution_journal(
            cmd=cmd,
            cwd=cwd or "",
            returncode=int(results.get("returncode", 1)),
            status=_classify_tool_result(results),
        )
        return results

    prompt = "⚠️ Agent called tool in HUMAN mode. Allow?\n(Enter 'y' to allow, type reason to reject): "
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
        cancelled = {"output": TOOL_CANCEL_MESSAGE.format(reason=confirmation), "returncode": 1}
        _append_execution_journal(cmd=cmd, cwd=cwd or "", returncode=1, status="rejected_by_user")
        return cancelled

    results = execute_bash_command(cmd, cwd=cwd)
    _append_execution_journal(
        cmd=cmd,
        cwd=cwd or "",
        returncode=int(results.get("returncode", 1)),
        status=_classify_tool_result(results),
    )
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
            "When you need to execute a tool call, your response must contain exactly ONE bash code block "
            "with ONE command (or commands connected with && or ||)."
            "When the task is complete, provide a concise final summary in plain text."
            "Read AGENTS.md early when present in the working tree or parents."
            "Include a THOUGHT section before your command where you explain your reasoning process."
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
        tools=[execute_bash_command_with_confirmation, get_execution_journal],
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
