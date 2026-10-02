"""Turn the Agents SDK's lifecycle callbacks into progress a page can show.

Instrumenting tools one at a time would mean touching every tool and would still
miss what the model *said* between them. `RunHooks` sees all of it from one
place: which agent is working, what it answered, which tool it called with which
arguments, and what that tool returned. One stateless instance,
:data:`ACTIVITY_HOOKS`, is passed to every ``Runner.run`` in a JFC analysis.

It is stateless because the destination is not its business: it reports to
whatever :mod:`hepagent.activity` sink is bound to the calling thread, which is
nothing at all on the CLI. So the cost of these hooks on a terminal run is a few
attribute reads per turn.

Everything here is defensive about the shapes it is handed. Output items come
from whichever provider answered — CBORG, OpenAI, Gemini — through the SDK's
converters, and a field that moves must degrade to a duller log line rather than
raise inside a hook and take the analysis with it.
"""

from __future__ import annotations

import ast
import json
from typing import Any

from agents import Agent, RunContextWrapper, RunHooks, Tool
from hepagent import activity

#: Argument keys worth putting in a headline, best first. A bash command or a
#: search query says what the step *is*; the rest of the arguments are detail.
_HEADLINE_KEYS = ("cmd", "command", "query", "prompt", "question", "name", "path", "skill")

#: Arguments that are narration in their own right — the bash tool's `thought`
#: is the model's reasoning for the command, which is exactly what the terminal
#: prints and the page was missing.
_THOUGHT_KEYS = ("thought", "reason", "rationale")


def _text_of(item: Any) -> str:
    """The assistant text in one output item, or "" if it carries none."""
    content = getattr(item, "content", None)
    if isinstance(content, str):
        return content
    parts: list[str] = []
    for piece in content or []:
        text = getattr(piece, "text", None)
        if isinstance(text, str) and text.strip():
            parts.append(text)
    return "\n".join(parts)


def _summary_of(item: Any) -> str:
    """The reasoning summary in a reasoning item, when the model emitted one."""
    parts: list[str] = []
    for piece in getattr(item, "summary", None) or []:
        text = getattr(piece, "text", None)
        if isinstance(text, str) and text.strip():
            parts.append(text)
    return "\n".join(parts)


def _is_tool_call(item: Any) -> bool:
    if getattr(item, "type", "") == "function_call":
        return True
    return hasattr(item, "name") and hasattr(item, "arguments")


def _describe_call(name: str, raw_arguments: Any) -> tuple[str, str]:
    """A `(headline, detail)` pair for one tool call.

    A tool's arguments are the most useful thing in the whole log — they are the
    command about to run — so the headline carries the one that says what is
    happening and the detail carries all of them, readably.
    """
    try:
        arguments = json.loads(raw_arguments or "{}")
    except (TypeError, ValueError):
        # A provider that hands back malformed JSON still tells us something.
        return f"{name}", str(raw_arguments or "")
    if not isinstance(arguments, dict):
        return f"{name}", json.dumps(arguments, indent=2, default=str)

    # A known key first, then any string argument, then the shape of the call.
    # The point is that a reader should see *what* is being done without opening
    # the detail - "python fit.py", not "(cmd, cwd, thought)".
    candidates = [k for k in _HEADLINE_KEYS if k in arguments]
    candidates += [k for k in arguments if k not in _HEADLINE_KEYS and k not in _THOUGHT_KEYS]
    subject = ""
    for key in candidates:
        value = arguments.get(key)
        if isinstance(value, str) and value.strip():
            subject = value
            break
    if not subject:
        keys = ", ".join(sorted(arguments)) or "no arguments"
        subject = f"({keys})"

    lines: list[str] = []
    for key in _THOUGHT_KEYS:
        value = arguments.get(key)
        if isinstance(value, str) and value.strip():
            lines.append(f"{key}: {value}")
    rest = {k: v for k, v in arguments.items() if k not in _THOUGHT_KEYS}
    if rest:
        lines.append(json.dumps(rest, indent=2, default=str))
    return f"{name} → {subject}", "\n\n".join(lines)


def _as_mapping(text: str) -> dict[str, Any] | None:
    """`text` read back as a dict, or None if it is not one.

    A function tool that returns a mapping — the bash tool returns
    `{"output": ..., "returncode": ...}` — reaches a hook as that mapping's
    `repr`, which is not JSON. `literal_eval` parses it without running
    anything: it builds literals and refuses everything else.
    """
    stripped = (text or "").strip()
    if not (stripped.startswith("{") and stripped.endswith("}")):
        return None
    for parse in (json.loads, ast.literal_eval):
        try:
            parsed = parse(stripped)
        except (ValueError, SyntaxError, MemoryError, RecursionError):
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def _readable_result(text: str, payload: dict[str, Any] | None) -> str:
    """A tool result a reader can actually read.

    Left alone, a command's output arrives with its newlines spelled `\n`,
    inside a Python repr — unreadable in exactly the place a physicist most
    needs to read it. A payload that carries an `output` becomes that output.
    """
    if payload is None:
        return text
    output = payload.get("output")
    if isinstance(output, str):
        return output
    return json.dumps(payload, indent=2, default=str)


class ActivityHooks(RunHooks[Any]):
    """Narrate an agent run to the thread's bound activity sink."""

    async def on_agent_start(self, context: RunContextWrapper[Any], agent: Agent[Any]) -> None:
        activity.report("agent", agent.name, "started")

    async def on_agent_end(
        self, context: RunContextWrapper[Any], agent: Agent[Any], output: Any
    ) -> None:
        # A plain-text answer was already reported turn by turn, so only a
        # structured final output — an architect's edit list, a judge's verdict —
        # has anything left to say here.
        detail = "" if isinstance(output, str) else str(output)
        activity.report("agent", agent.name, "finished", detail=detail)

    async def on_handoff(
        self,
        context: RunContextWrapper[Any],
        from_agent: Agent[Any],
        to_agent: Agent[Any],
    ) -> None:
        activity.report("agent", from_agent.name, f"handed off to {to_agent.name}")

    async def on_llm_end(
        self, context: RunContextWrapper[Any], agent: Agent[Any], response: Any
    ) -> None:
        """Report what the model just said and what it decided to call.

        Reported here rather than in `on_tool_start`, which is handed the tool
        but not its arguments — and the arguments are the interesting half.
        """
        for item in getattr(response, "output", None) or []:
            if _is_tool_call(item):
                headline, detail = _describe_call(
                    str(getattr(item, "name", "tool")), getattr(item, "arguments", "")
                )
                activity.report("tool", agent.name, headline, detail=detail)
                continue
            reasoning = _summary_of(item)
            if reasoning:
                activity.report("reasoning", agent.name, reasoning, detail=reasoning)
                continue
            text = _text_of(item)
            if text.strip():
                activity.report("message", agent.name, text, detail=text)

    async def on_tool_end(
        self, context: RunContextWrapper[Any], agent: Agent[Any], tool: Tool, result: str
    ) -> None:
        text = result if isinstance(result, str) else str(result)
        payload = _as_mapping(text)
        # A failed command is the thing a supervising physicist most wants to
        # spot, and the tool layer reports it in its payload rather than raising.
        code = payload.get("returncode") if payload else None
        failed = isinstance(code, int) and code != 0
        activity.report(
            "result",
            agent.name,
            f"{tool.name} returned" + (f" (exit {code})" if failed else ""),
            detail=_readable_result(text, payload),
            level="warning" if failed else "info",
        )


#: Stateless, so one instance serves every run in the process.
ACTIVITY_HOOKS = ActivityHooks()
