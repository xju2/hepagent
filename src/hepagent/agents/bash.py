"""Bash agent that solve problems by running bash commands.
Adapted from min-swe-agent:
https://github.com/SWE-agent/mini-swe-agent/blob/main/src/minisweagent/agents/default.py
"""

import os
import re
import shlex
import subprocess
import threading
import time
from pathlib import Path
from pathlib import PurePosixPath

from pydantic import BaseModel

from agents import Agent, function_tool
from hepagent.agents.common import OUTPUT_TRUNCATE_LENGTH
from hepagent.agents.workflow_policy import build_workflow_policy
from hepagent.model_providers import get_model_provider


class LocalEnvironmentConfig(BaseModel):
    cwd: str = ""
    env: dict[str, str] = {}
    timeout: int = 30


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


def _normalize_rel_path(path: str) -> str:
    p = path.strip().strip("\"'`")
    while p.startswith("./"):
        p = p[2:]
    if p.endswith("/") and p != "/":
        p = p[:-1]
    return p


def _extract_runs_paths(text: str) -> set[str]:
    found = set()
    for m in re.finditer(r"(?<![A-Za-z0-9_./-])(runs/[^\s\"'`;,]+)", text):
        raw = m.group(1).rstrip(".)]:")
        norm = _normalize_rel_path(raw)
        if norm.startswith("runs/"):
            found.add(norm)
    return found


def _extract_missing_paths(text: str) -> set[str]:
    found = set()
    for line in (text or "").splitlines():
        line = line.strip()
        if "No such file or directory" not in line:
            continue
        # Typical forms:
        #   ls: runs/x/y: No such file or directory
        #   cat: ./runs/x: No such file or directory
        m = re.search(r"^[^:]+:\s+(.+?):\s+No such file or directory$", line)
        if not m:
            continue
        candidate = _normalize_rel_path(m.group(1))
        if candidate:
            found.add(candidate)
    return found


def _scoped_setup_suggestion(missing_paths: set[str], active_runs_scopes: set[str]) -> str | None:
    parent_dirs: set[str] = set()
    for p in missing_paths:
        norm = _normalize_rel_path(p)
        if not norm.startswith("runs/"):
            continue
        if not any(
            scope == norm or norm.startswith(scope + "/") or scope.startswith(norm + "/")
            for scope in active_runs_scopes
        ):
            continue
        parent = str(PurePosixPath(norm).parent)
        if parent and parent not in {".", "/"}:
            parent_dirs.add(parent)

    if not parent_dirs and active_runs_scopes:
        # Fall back to the shallowest known scope.
        parent_dirs.add(sorted(active_runs_scopes, key=lambda s: (s.count("/"), len(s)))[0])

    if not parent_dirs:
        return None

    ordered = " ".join(sorted(parent_dirs))
    return f"mkdir -p {ordered}"


def _expand_runs_scopes(paths: set[str]) -> set[str]:
    scopes: set[str] = set()
    for p in paths:
        norm = _normalize_rel_path(p)
        if not norm.startswith("runs/"):
            continue
        scopes.add(norm)
        cur = PurePosixPath(norm)
        while True:
            parent = str(cur.parent)
            if parent in {".", "", "runs"}:
                break
            scopes.add(parent)
            cur = PurePosixPath(parent)
    return scopes


def _runs_scan_reason(cmd: str, allowed_scopes: set[str]) -> str | None:
    for segment in _split_command_segments(cmd):
        try:
            tokens = shlex.split(segment)
        except ValueError:
            continue
        if not tokens:
            continue
        cmd_name = tokens[0]
        if cmd_name not in {"ls", "find", "rg"}:
            continue

        path_args = _extract_path_args(cmd_name, tokens)
        if cmd_name == "rg":
            # Only care about file listing probes.
            if "--files" not in tokens:
                continue

        if not path_args:
            continue

        for raw in path_args:
            p = _normalize_rel_path(raw)
            if p not in {"runs", "runs/"} and not p.startswith("runs/"):
                continue

            if p in {"runs", "runs/"}:
                return "top-level 'runs/' discovery is disallowed without an explicit manifest-scoped subpath"

            is_allowed = any(
                scope == p or scope.startswith(p + "/") or p.startswith(scope + "/") for scope in allowed_scopes
            )
            if not is_allowed:
                return (
                    "command targets a runs/ subpath that is not in the active manifest scope; "
                    "ask for exact path or permission to search"
                )
    return None


def _overread_reason(cmd: str) -> str | None:
    normalized = " ".join(cmd.strip().split())

    # Allow heredoc-style file creation used for writes.
    # Supported forms:
    # - cat <<EOF > file
    # - cat > file <<EOF
    if re.search(r"(^|[;&|]\s*)cat\s+<<\S+", normalized):
        return None
    if re.search(r"(^|[;&|]\s*)cat\s+>>?\s*\S+\s+<<\S+", normalized):
        return None

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


def _classify_tool_result(result: dict) -> str:
    output = str(result.get("output", ""))
    rc = int(result.get("returncode", 1))
    if rc == 0:
        return "executed"
    if rc == 1 and output.startswith("Tool calling is cancelled by user"):
        return "rejected_by_user"
    if rc == 2:
        return "blocked_guard"
    return "failed"


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


def _command_reads_path(cmd: str, target_path: str) -> bool:
    target_norm = _normalize_path_token(target_path)
    target_name = Path(target_norm).name
    segments = _split_command_segments(cmd)
    for segment in segments:
        try:
            tokens = shlex.split(segment)
        except ValueError:
            continue
        if not tokens:
            continue
        if tokens[0] not in READ_ONLY_COMMANDS:
            continue
        for path_arg in _extract_path_args(tokens[0], tokens):
            candidate = _normalize_path_token(path_arg)
            if candidate == target_norm or Path(candidate).name == target_name:
                return True
    return False


class _ProgressGuardState:
    def __init__(self) -> None:
        self.no_progress_streak = 0
        self._seen_successful_reads: set[str] = set()
        self.last_cmd = ""
        self.same_cmd_streak = 0
        self.last_thought = ""
        self.same_thought_streak = 0
        self.pending_clarification_blocks = 0
        self.pending_scaffold_setup = 0
        self.pending_scaffold_suggestion: str | None = None
        self.pending_scaffold_announce = False
        self.active_runs_scopes: set[str] = set()
        self.workflow_policy = build_workflow_policy()

    def _scaffold_setup_message(self) -> str | None:
        if self.pending_scaffold_setup <= 0:
            return None
        suggestion = self.pending_scaffold_suggestion
        suggestion_line = (
            f" Suggested next command: `{suggestion}`."
            if suggestion
            else ""
        )
        return (
            "Progress guard: manifest-scoped paths are missing under runs/. "
            "Execute one in-scope setup action now (for example mkdir/write manifest/run preflight), "
            "instead of additional probing or asking for path discovery."
            f"{suggestion_line}"
        )

    def _is_missing_path_in_active_runs_scope(self, output: str) -> bool:
        missing_paths = _extract_missing_paths(output)
        if not missing_paths:
            return False
        for p in missing_paths:
            if not p.startswith("runs/"):
                continue
            if any(scope == p or p.startswith(scope + "/") or scope.startswith(p + "/") for scope in self.active_runs_scopes):
                return True
        return False

    def _limits(self) -> tuple[int, int, int, int]:
        mode = os.getenv("HEPAGENT_POLICY_MODE", "balanced").strip().lower()
        if mode == "conservative":
            return (6, 1, 1, 800)
        if mode == "exploratory":
            return (16, 3, 3, 1800)
        # balanced default
        return (10, 2, 2, 1200)

    def evaluate(self, cmd: str, thought: str) -> str | None:
        default_no_progress, default_cmd_streak, default_thought_streak, default_thought_chars = self._limits()
        max_no_progress_steps = _get_int_env("HEPAGENT_MAX_NO_PROGRESS_STEPS", default_no_progress)
        max_same_cmd_streak = _get_int_env("HEPAGENT_MAX_SAME_COMMAND_STREAK", default_cmd_streak)
        max_same_thought_streak = _get_int_env("HEPAGENT_MAX_SAME_THOUGHT_STREAK", default_thought_streak)
        max_thought_chars = _get_int_env("HEPAGENT_MAX_THOUGHT_CHARS", default_thought_chars)

        normalized_cmd = _normalize_text(cmd)
        normalized_thought = _normalize_text(thought or "")

        workflow_reason = self.workflow_policy.evaluate(cmd, thought)
        if workflow_reason:
            return workflow_reason

        if self.pending_scaffold_setup > 0 and _is_read_only_command(cmd):
            workflow_report_path = getattr(self.workflow_policy, "report_path", None)
            if workflow_report_path and _command_reads_path(cmd, workflow_report_path):
                return None
            msg = self._scaffold_setup_message()
            if msg:
                return msg

        if self.pending_clarification_blocks > 0:
            self.pending_clarification_blocks -= 1
            return (
                "Progress guard: previous step hit a missing-path error. "
                "Ask exactly one blocking clarification question now before any further tool calls."
            )

        # Scope-constrained runs/* discovery is a workflow-specific concern.
        # Keep default guard behavior generic unless a workflow policy has activated.
        if getattr(self.workflow_policy, "enabled", False):
            runs_reason = _runs_scan_reason(cmd, self.active_runs_scopes)
            if runs_reason:
                return f"Progress guard: {runs_reason}."

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

        if _is_read_only_command(cmd) and self.no_progress_streak >= max_no_progress_steps:
            return (
                "Progress guard: repeated low-progress loop detected. "
                "Run one concrete execution/edit step, or ask one blocking clarification question."
            )

        return None

    def record_result(self, cmd: str, returncode: int, output: str = "") -> None:
        self.workflow_policy.record_result(cmd, returncode, output)
        if getattr(self.workflow_policy, "report_path", None):
            # Once preflight has produced a report path, prioritize report-read/finalize flow
            # over earlier scaffold setup nudges.
            self.pending_scaffold_setup = 0
            self.pending_scaffold_suggestion = None
            self.pending_scaffold_announce = False

        if returncode != 0:
            if "No such file or directory" in (output or ""):
                # If the missing path is in known manifest-scoped runs/*,
                # allow the agent to proceed (for example create scaffold dirs/files).
                # Otherwise force one clarification turn before more probing.
                if self._is_missing_path_in_active_runs_scope(output or ""):
                    # Nudge toward setup actions, not endless reads/questions.
                    self.pending_scaffold_setup = 1
                    missing = _extract_missing_paths(output or "")
                    self.pending_scaffold_suggestion = _scoped_setup_suggestion(
                        missing_paths=missing,
                        active_runs_scopes=self.active_runs_scopes,
                    )
                    self.pending_scaffold_announce = True
                else:
                    self.pending_clarification_blocks = 1
            self.no_progress_streak += 1
            return
        if output:
            discovered = _extract_runs_paths(output)
            if discovered:
                self.active_runs_scopes.update(_expand_runs_scopes(discovered))
        if _is_read_only_command(cmd):
            normalized_cmd = _normalize_text(cmd)
            if normalized_cmd in self._seen_successful_reads:
                self.no_progress_streak += 1
            else:
                self._seen_successful_reads.add(normalized_cmd)
                self.no_progress_streak = max(0, self.no_progress_streak - 1)
            return
        # Successful non-read action is considered scaffold progress; clear pending scaffold nudge.
        self.pending_scaffold_setup = 0
        self.pending_scaffold_suggestion = None
        self.pending_scaffold_announce = False
        self.no_progress_streak = 0

    def consume_immediate_followup_message(self) -> str | None:
        """Return one-shot guard directive that should be surfaced immediately after a result."""
        if self.pending_scaffold_setup <= 0 or not self.pending_scaffold_announce:
            return None
        self.pending_scaffold_announce = False
        return self._scaffold_setup_message()


_PROGRESS_GUARD = _ProgressGuardState()


def _reset_progress_guard_for_tests() -> None:
    global _PROGRESS_GUARD
    _PROGRESS_GUARD = _ProgressGuardState()


@function_tool
def get_execution_journal(limit: int = 50) -> dict:
    """Return recent command execution journal entries for grounded reporting."""
    safe_limit = max(1, min(int(limit), 200))
    entries = _snapshot_execution_journal(safe_limit)
    return {
        "count": len(entries),
        "entries": entries,
    }


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
            is_finalize = "provide final summary now" in guard_reason.lower()
            blocked = {
                "output": (
                    f"{guard_reason}\n"
                    "If path/context is missing, ask the user for exact scope instead of further probing.\n"
                    f"{BLOCKED_RETRY_HINT}"
                    + ("\nFINALIZE_NOW: stop tool-calling and produce the final summary." if is_finalize else "")
                ),
                "returncode": 0 if is_finalize else 2,
            }
            _append_execution_journal(
                cmd=cmd,
                cwd=cwd or "",
                returncode=0 if is_finalize else 2,
                status="blocked_guard",
            )
            return blocked

    if os.getenv("HEPAGENT_YOLO") == "1":
        results = execute_bash_command(cmd, cwd=cwd)
        _PROGRESS_GUARD.record_result(
            cmd, int(results.get("returncode", 1)), str(results.get("output", ""))
        )
        immediate_guard = _PROGRESS_GUARD.consume_immediate_followup_message()
        if immediate_guard:
            blocked = {
                "output": (
                    f"{immediate_guard}\n"
                    "If path/context is missing, ask the user for exact scope instead of further probing.\n"
                    f"{BLOCKED_RETRY_HINT}"
                ),
                "returncode": 2,
            }
            _append_execution_journal(
                cmd=cmd, cwd=cwd or "", returncode=2, status="blocked_guard"
            )
            return blocked
        _append_execution_journal(
            cmd=cmd, cwd=cwd or "", returncode=int(results.get("returncode", 1)), status=_classify_tool_result(results)
        )
        return results

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
        cancelled = {"output": TOOL_CANCEL_MESSAGE.format(reason=confirmation), "returncode": 1}
        _append_execution_journal(cmd=cmd, cwd=cwd or "", returncode=1, status="rejected_by_user")
        return cancelled

    results = execute_bash_command(cmd, cwd=cwd)
    _PROGRESS_GUARD.record_result(
        cmd, int(results.get("returncode", 1)), str(results.get("output", ""))
    )
    immediate_guard = _PROGRESS_GUARD.consume_immediate_followup_message()
    if immediate_guard:
        blocked = {
            "output": (
                f"{immediate_guard}\n"
                "If path/context is missing, ask the user for exact scope instead of further probing.\n"
                f"{BLOCKED_RETRY_HINT}"
            ),
            "returncode": 2,
        }
        _append_execution_journal(
            cmd=cmd, cwd=cwd or "", returncode=2, status="blocked_guard"
        )
        return blocked
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
            "When the task is complete, do not emit another command. Provide a concise final summary in plain text."
            "When blocked by missing critical context/path, ask exactly one blocking clarification question in plain text."
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
            " If missing paths are already manifest-scoped under runs/ for an active workflow,"
            " do not ask for path discovery: perform one in-scope setup step (mkdir/write manifest/preflight)."
            " If a preflight report fails only because manifest-scoped files are missing,"
            " propose the minimal in-scope setup action and rerun preflight; do not ask the user to provide alternate paths."
            "Do not stop after reading initial files when the task includes required execution steps."
            " Continue with the next required step, or explicitly ask one blocking clarification question."
            "Avoid repeated reasoning loops: do not propose the same command or same analysis repeatedly."
            " Keep THOUGHT concise and action-oriented."
            "When reporting executed commands, call `get_execution_journal` first and only report entries "
            "present in that journal. Never invent commands or paths."
            "If a command is blocked by policy, your next response must propose exactly one safer replacement "
            "command (in-scope and bounded), not additional exploratory reads."
            "After reading a required result artifact/report, synthesize findings and stop instead of continuing exploration."
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
