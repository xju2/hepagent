"""Who answers when an agent asks a human.

Three call sites block on a person: the bash confirmation tool
(:mod:`hepagent.agents.bash`), ``ask_user_for_info``
(:mod:`hepagent.tools.common`), and the JFC human gate
(:mod:`hepagent.agents.jfc.orchestrator`). All three read stdin, which is right
for the CLI and wrong for a run launched from a browser — there is no terminal
attached to it, and `input()` would either raise or, worse, block the server.

A backend is therefore *installed on the thread that runs the agents*. Nothing
changes for the CLI: with no backend bound, every call site keeps its existing
terminal behaviour. Thread-local rather than a `ContextVar` because the browser
runs an analysis on its own worker thread (so a synchronous tool blocking on a
human cannot stall the web server's event loop), and a `ContextVar` set in that
thread would not be the one the tool reads unless the context were copied at
exactly the right moment.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class Approval:
    """A human's answer to "may I run this command?"."""

    approved: bool
    reason: str = ""


@runtime_checkable
class InteractionBackend(Protocol):
    """Where a blocking question goes when it must not go to stdin.

    Both methods are **synchronous and may block for a long time** — they are
    called from inside synchronous function tools, on a thread that is allowed
    to wait for a human.
    """

    def approve(self, cmd: str, cwd: str = "", thought: str = "") -> Approval:
        """Ask whether a shell command may run."""
        ...

    def ask(self, prompt: str, thought: str = "", choices: Sequence[str] = ()) -> str:
        """Ask a free-text question, optionally offering `choices` as buttons."""
        ...


_state = threading.local()


def set_backend(backend: InteractionBackend | None) -> None:
    """Install (or with `None`, remove) the backend for the calling thread."""
    _state.backend = backend


def current_backend() -> InteractionBackend | None:
    """The backend bound to the calling thread, or None for terminal behaviour."""
    return getattr(_state, "backend", None)


@contextmanager
def bound_backend(backend: InteractionBackend | None) -> Iterator[InteractionBackend | None]:
    """Bind `backend` for the calling thread for the duration of the block."""
    previous = current_backend()
    set_backend(backend)
    try:
        yield backend
    finally:
        set_backend(previous)
