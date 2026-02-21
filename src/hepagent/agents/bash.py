"""Bash agent that solve problems by running bash commands.
Adapted from min-swe-agent:
https://github.com/SWE-agent/mini-swe-agent/blob/main/src/minisweagent/agents/default.py
"""

import os
import re
import shlex
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


def _get_int_env(name: str, default: int, min_value: int = 1) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        parsed = int(raw)
    except ValueError:
        return default
    return max(min_value, parsed)


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


def _overread_reason(cmd: str) -> str | None:
    normalized = " ".join(cmd.strip().split())

    # Avoid reading many files at once with raw `cat`; prefer bounded reads.
    if re.search(r"(^|[;&|]\s*)cat\s+\S+\s+\S+", normalized):
        return "multi-file 'cat' is usually over-broad; read one file at a time with bounded output"

    return None


def _portability_reason(cmd: str) -> str | None:
    # BSD/macOS and GNU sed differ on -i behavior; avoid fragile one-liners in agent loops.
    for segment in _split_command_segments(cmd):
        try:
            tokens = shlex.split(segment)
        except ValueError:
            continue
        if not tokens or tokens[0] != "sed":
            continue
        for t in tokens[1:]:
            if t == "-i" or t.startswith("-i"):
                return "in-place 'sed -i' is platform-fragile (GNU/BSD differences)"
    return None


READ_ONLY_COMMANDS = {
    "ls",
    "cat",
    "sed",
    "head",
    "tail",
    "find",
    "rg",
    "tree",
    "stat",
    "wc",
    "grep",
}

BOOTSTRAP_READ_PATHS = {
    "hepagent_instruction.txt",
    "registry.yaml",
    "AGENTS.md",
    "orchestrator/contract.yaml",
    "class/contract.yaml",
    "cosmicic/contract.yaml",
    "nyx/contract.yaml",
    "growth/contract.yaml",
    "gimlet/contract.yaml",
    "orchestrator/example_manifest.yaml",
}


def _normalize_text(text: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9\s]+", " ", text.lower()).split())


def _split_command_segments(cmd: str) -> list[str]:
    parts = re.split(r"\s*(?:&&|\|\||;|\|)\s*", cmd.strip())
    return [p for p in parts if p]


def _is_read_only_command(cmd: str) -> bool:
    segments = _split_command_segments(cmd)
    if not segments:
        return False
    for segment in segments:
        try:
            tokens = shlex.split(segment)
        except ValueError:
            return False
        if not tokens:
            continue
        if tokens[0] not in READ_ONLY_COMMANDS:
            return False
    return True


def _extract_path_args(cmd_name: str, tokens: list[str]) -> list[str]:
    if len(tokens) <= 1:
        return []
    args = tokens[1:]
    if cmd_name == "sed":
        # For sed, first non-option token is usually the script expression.
        script_consumed = False
        out: list[str] = []
        for t in args:
            if not script_consumed:
                if t.startswith("-"):
                    continue
                script_consumed = True
                continue
            if t.startswith("-"):
                continue
            out.append(t)
        return out

    out = []
    for t in args:
        if t.startswith("-"):
            continue
        out.append(t)
    return out


def _normalize_path_token(token: str) -> str:
    if token.startswith("./"):
        token = token[2:]
    return token


def _is_bootstrap_read_command(cmd: str) -> bool:
    segments = _split_command_segments(cmd)
    if not segments:
        return False
    for segment in segments:
        try:
            tokens = shlex.split(segment)
        except ValueError:
            return False
        if not tokens:
            return False
        cmd_name = tokens[0]
        if cmd_name not in READ_ONLY_COMMANDS:
            return False
        path_args = _extract_path_args(cmd_name, tokens)
        if not path_args:
            return False
        if not all(_normalize_path_token(p) in BOOTSTRAP_READ_PATHS for p in path_args):
            return False
    return True


class _ProgressGuardState:
    def __init__(self) -> None:
        self.successful_read_since_nonread = 0
        self.successful_bootstrap_reads = 0
        self.bootstrap_mode = True
        self.last_cmd = ""
        self.same_cmd_streak = 0
        self.last_thought = ""
        self.same_thought_streak = 0
        self.pending_clarification_blocks = 0

    def _limits(self) -> tuple[int, int, int, int]:
        mode = os.getenv("HEPAGENT_POLICY_MODE", "balanced").strip().lower()
        if mode == "conservative":
            return (4, 1, 1, 800)
        if mode == "exploratory":
            return (14, 3, 3, 1800)
        # balanced default
        return (8, 2, 2, 1200)

    def evaluate(self, cmd: str, thought: str) -> str | None:
        default_read, default_cmd_streak, default_thought_streak, default_thought_chars = self._limits()
        max_read_steps = _get_int_env("HEPAGENT_MAX_READ_STEPS", default_read)
        max_bootstrap_reads = _get_int_env("HEPAGENT_MAX_BOOTSTRAP_READ_STEPS", 6)
        max_same_cmd_streak = _get_int_env("HEPAGENT_MAX_SAME_COMMAND_STREAK", default_cmd_streak)
        max_same_thought_streak = _get_int_env("HEPAGENT_MAX_SAME_THOUGHT_STREAK", default_thought_streak)
        max_thought_chars = _get_int_env("HEPAGENT_MAX_THOUGHT_CHARS", default_thought_chars)

        normalized_cmd = _normalize_text(cmd)
        normalized_thought = _normalize_text(thought or "")

        if self.pending_clarification_blocks > 0 and _is_read_only_command(cmd):
            self.pending_clarification_blocks -= 1
            return (
                "Progress guard: previous step hit a missing-path error. "
                "Ask exactly one blocking clarification question before additional reads."
            )

        if len(thought or "") > max_thought_chars:
            return (
                "Progress guard: thought is too long for an actionable step. "
                "Use a concise thought, then execute one concrete command."
            )

        if normalized_cmd and normalized_cmd == self.last_cmd:
            self.same_cmd_streak += 1
        else:
            self.same_cmd_streak = 1
        self.last_cmd = normalized_cmd

        if normalized_thought and normalized_thought == self.last_thought:
            self.same_thought_streak += 1
        elif normalized_thought:
            self.same_thought_streak = 1
        self.last_thought = normalized_thought

        if self.same_cmd_streak > max_same_cmd_streak:
            return (
                "Progress guard: repeated command proposals detected. "
                "Execute a different next step or ask one blocking clarification question."
            )
        if normalized_thought and self.same_thought_streak > max_same_thought_streak:
            return (
                "Progress guard: repeated reasoning detected. "
                "Proceed with one concrete action or ask one blocking clarification question."
            )

        if self.bootstrap_mode and _is_read_only_command(cmd):
            if self.successful_bootstrap_reads >= max_bootstrap_reads:
                return (
                    "Progress guard: bootstrap reading budget reached. "
                    "Execute the next required action, or ask one blocking clarification question."
                )

        if _is_read_only_command(cmd) and not _is_bootstrap_read_command(cmd):
            if self.successful_read_since_nonread >= max_read_steps:
                return (
                    "Progress guard: too many read-only steps in a row. "
                    "Run the next required execution step, or ask the user one blocking question."
                )

        return None

    def record_result(self, cmd: str, returncode: int, output: str = "") -> None:
        if returncode != 0:
            if "No such file or directory" in (output or ""):
                # Force one clarification turn before more read-only probing.
                self.pending_clarification_blocks = 1
            return
        if self.bootstrap_mode and _is_read_only_command(cmd):
            self.successful_bootstrap_reads += 1
            if not _is_bootstrap_read_command(cmd):
                self.successful_read_since_nonread += 1
            return
        if _is_read_only_command(cmd):
            if _is_bootstrap_read_command(cmd):
                return
            self.successful_read_since_nonread += 1
            return
        self.bootstrap_mode = False
        self.successful_bootstrap_reads = 0
        self.successful_read_since_nonread = 0


_PROGRESS_GUARD = _ProgressGuardState()


def _reset_progress_guard_for_tests() -> None:
    global _PROGRESS_GUARD
    _PROGRESS_GUARD = _ProgressGuardState()


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
                    "Use a narrower command (target specific path, add depth/output limits).\n"
                    f"{BLOCKED_RETRY_HINT}"
                ),
                "returncode": 2,
            }
        overread = _overread_reason(cmd)
        if overread:
            return {
                "output": (
                    f"Command blocked by safety guard: {overread}.\n"
                    "Use bounded file reads, e.g. `sed -n '1,200p' <file>`.\n"
                    f"{BLOCKED_RETRY_HINT}"
                ),
                "returncode": 2,
            }

    portability = _portability_reason(cmd)
    if portability and os.getenv("HEPAGENT_ALLOW_FRAGILE_EDIT") != "1":
        return {
            "output": (
                f"Command blocked by safety guard: {portability}.\n"
                "Use a portable file-write/edit approach (rewrite file content directly, "
                "or use a non-fragile script).\n"
                f"{BLOCKED_RETRY_HINT}"
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

BLOCKED_RETRY_HINT = (
    "Blocked command policy: immediately propose ONE safer replacement command that is in-scope, "
    "bounded, and advances the task."
)


@function_tool
def execute_bash_command_with_confirmation(cmd: str, cwd: str = "", thought: str = "") -> dict:
    """Only execute a bash command with user's confirmation and return the output."""

    # print the thought and the command to be executed for user's review.
    print(f"THOUGHT:{thought}", flush=True)
    print(f"About to execute command:\n\tcmd={cmd}\n\tcwd={cwd}", flush=True)

    if os.getenv("HEPAGENT_DISABLE_PROGRESS_GUARD") != "1":
        guard_reason = _PROGRESS_GUARD.evaluate(cmd, thought)
        if guard_reason:
            return {
                "output": (
                    f"{guard_reason}\n"
                    "If path/context is missing, ask the user for exact scope instead of further probing.\n"
                    f"{BLOCKED_RETRY_HINT}"
                ),
                "returncode": 2,
            }

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
    _PROGRESS_GUARD.record_result(
        cmd, int(results.get("returncode", 1)), str(results.get("output", ""))
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
            "When reading files, avoid raw multi-file `cat`; read one file at a time with"
            " bounded output, for example `sed -n '1,200p' <file>`."
            "If a required path is missing or a command returns 'No such file or directory',"
            " do not probe sibling/top-level directories to guess."
            " Instead, ask the user for the correct path or permission to search."
            "Do not stop after reading initial files when the task includes required execution steps."
            " Continue with the next required step, or explicitly ask one blocking clarification question."
            "Avoid repeated reasoning loops: do not propose the same command or same analysis repeatedly."
            " Keep THOUGHT concise and action-oriented."
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
