"""What an agent is doing, narrated while it does it.

:mod:`hepagent.interaction` answers "who does an agent ask when it needs a
human". This is the quieter half of the same problem: who gets *told* what an
agent is up to when nobody asked. On the CLI the answer has always been stdout —
the bash tool prints the thought and the command, the SDK's own logging fills in
the rest — and a run launched from the plan page has no terminal to print to. It
had only the node boundaries the orchestrator chose to announce, which is why
the window that launched the server showed far more than the page supervising
the run.

A *sink* is bound to the thread that runs the agents, exactly as an interaction
backend is, and for exactly the same reason: the browser runs an analysis on its
own worker thread so a synchronous tool waiting on a human cannot stall the web
server's event loop, and a `ContextVar` set on that thread is not the one a tool
would read.

Two rules keep this from becoming a liability.

- **A sink is a courtesy, never a dependency.** :func:`report` no-ops when none
  is bound — every CLI path — and swallows whatever a sink raises. Narration
  must never be able to fail the run it is narrating.
- **Events are clipped here, not by the reader.** A single tool result can be a
  megabyte of log; the sink at the other end is a progress view held in memory,
  and the artefact on disk is the archive.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

#: Headlines stay one scannable line. The body belongs in `detail`, which a
#: reader opens only for the step it cares about.
MAX_HEADLINE_CHARS = 240

#: How much of a tool result or a model's answer is kept. Generous enough to
#: read a stack trace or a table, small enough that a chatty node cannot eat the
#: process.
MAX_DETAIL_CHARS = 4000

#: The kinds a sink may be asked to render. Open by convention rather than
#: enforced — an unknown kind must display as an ordinary line, not raise.
KINDS = ("agent", "message", "reasoning", "tool", "result")


@dataclass(frozen=True)
class ActivityEvent:
    """One thing an agent did.

    Args:
        kind: One of :data:`KINDS`. What the reader is looking at, so a page can
            style a command differently from its output.
        agent: The agent that did it, by display name.
        message: A single-line headline, already clipped.
        detail: The full body — a command's output, a model's answer, a tool
            call's arguments — already clipped. May be empty.
        level: "info", "warning" or "error".
    """

    kind: str
    agent: str
    message: str
    detail: str = ""
    level: str = "info"


@runtime_checkable
class ActivitySink(Protocol):
    """Where narration goes.

    Called from the agent thread, possibly hundreds of times per node, and
    always synchronously: an implementation must be cheap and must not block.
    """

    def activity(self, event: ActivityEvent) -> None:
        """Record one event. Must not raise; :func:`report` guards it anyway."""
        ...


_state = threading.local()


def set_sink(sink: ActivitySink | None) -> None:
    """Install (or with `None`, remove) the sink for the calling thread."""
    _state.sink = sink


def current_sink() -> ActivitySink | None:
    """The sink bound to the calling thread, or None to narrate nowhere."""
    return getattr(_state, "sink", None)


@contextmanager
def bound_sink(sink: ActivitySink | None) -> Iterator[ActivitySink | None]:
    """Bind `sink` for the calling thread for the duration of the block."""
    previous = current_sink()
    set_sink(sink)
    try:
        yield sink
    finally:
        set_sink(previous)


def clip(text: str, limit: int) -> str:
    """`text` shortened to `limit` characters, saying how much it dropped."""
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return f"{text[:limit]}\n\n[+{len(text) - limit} more characters]"


def one_line(text: str, limit: int = MAX_HEADLINE_CHARS) -> str:
    """`text` as a single clipped line, for a headline."""
    return clip(" ".join((text or "").split()), limit)


def report(
    kind: str,
    agent: str,
    message: str,
    detail: str = "",
    level: str = "info",
) -> None:
    """Narrate one step to the bound sink, or to nowhere if none is bound."""
    sink = current_sink()
    if sink is None:
        return
    headline = one_line(message)
    body = clip(detail, MAX_DETAIL_CHARS)
    # A body that *is* the headline is not worth a fold: the reader would open it
    # to be told what they just read. Compared exactly, not loosely — a body that
    # only collapses to the headline still has line breaks the headline lost, and
    # those are the shape of a stack trace or a table.
    if body == headline:
        body = ""
    event = ActivityEvent(
        kind=kind,
        agent=agent,
        message=headline,
        detail=body,
        level=level,
    )
    try:
        sink.activity(event)
    except Exception:  # noqa: BLE001 - narration must never fail the run
        pass
