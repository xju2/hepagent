"""One analysis run, supervised from the browser.

`plan/service.py` gets a plan approved; this module is what happens next when
approval came from a page rather than from a terminal: the run is started on a
worker thread, its progress is collected as a polled event log, and every
question it wants to ask a human becomes a *pending prompt* the page renders and
answers.

Three constraints shape it.

1. **The run may not touch the server's event loop.** Function tools in the
   Agents SDK are synchronous and inline; one that waits an hour for an approval
   would take the websocket with it. The run therefore owns a thread and its own
   loop, and everything here is thread-safe rather than async.
2. **A page can disappear.** State lives in the registry, not in a connection,
   so a reload re-attaches to a run in flight. Events carry sequence numbers so a
   reconnecting page asks for what it missed instead of the whole history.
3. **This module stays domain-agnostic.** It knows how to run *something* and
   how to ask a human; it does not know what a JFC analysis is. The callable that
   actually runs one is injected — `agents/jfc/launch.py` supplies it, the same
   way `plan_api` gets its vocabulary from `agents/jfc/capabilities`.
"""

from __future__ import annotations

import dataclasses
import threading
import time
import traceback
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from hepagent.activity import ActivityEvent
from hepagent.interaction import Approval

#: A run that finished, failed or was cancelled is *terminal*: it can be
#: inspected and replaced, but never resumed in place.
TERMINAL_STATUSES = frozenset({"done", "failed", "cancelled"})

#: How long a prompt waits for a human before it gives up. A physicist reading a
#: draft note is not fast; a page that was closed is not coming back at all. An
#: hour is long enough for the first and short enough that the second does not
#: pin a thread forever.
PROMPT_TIMEOUT_SECONDS = 60 * 60

#: Kept per run so a long analysis cannot grow an unbounded log in memory. The
#: page polls incrementally, so it only ever misses history it already saw. It
#: has to be generous: since the run narrates every model turn and every tool
#: call, a single node can be hundreds of events, and the page is where a
#: physicist is meant to be able to follow the whole thing.
MAX_EVENTS = 20000


class RunAlreadyActive(RuntimeError):
    """Raised when a second run is started for an analysis already running one."""


class RunCancelled(RuntimeError):
    """Raised inside the run thread to unwind a cancelled run."""


@dataclass(frozen=True)
class RunEvent:
    """One line of progress.

    Args:
        seq: Monotonic within a run. The page polls `?since=<seq>`.
        at: Unix timestamp.
        node: Plan node id the event belongs to, or a pseudo-node the
            orchestrator uses for whole-run steps ("scaffold", "graph", "plan").
        message: Human-readable progress text, verbatim from the run. One line:
            anything longer belongs in `detail`.
        level: "info", "warning" or "error".
        kind: What the reader is looking at — "progress" for a node boundary the
            orchestrator announced, or one of `activity.KINDS` for a step an
            agent narrated. The page styles and filters on this, so a log of a
            thousand tool calls can still be read as a handful of phases.
        agent: The agent that produced it, for an activity event; "" for the
            orchestrator's own progress.
        detail: The full body — a command's output, a model's answer — shown
            folded away until the reader opens it. May be empty.
    """

    seq: int
    at: float
    node: str
    message: str
    level: str = "info"
    kind: str = "progress"
    agent: str = ""
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "seq": self.seq,
            "at": self.at,
            "node": self.node,
            "message": self.message,
            "level": self.level,
            "kind": self.kind,
            "agent": self.agent,
            "detail": self.detail,
        }


@dataclass(frozen=True)
class Prompt:
    """A question the run is blocked on.

    Args:
        id: Unique within the run; the answer names it, so an answer to a prompt
            that has already timed out is discarded rather than misapplied.
        kind: "approval" for a shell command, "question" for free text.
        text: What to show the human.
        cmd: The command awaiting approval, for `kind="approval"`.
        cwd: Its working directory.
        thought: The agent's stated reason for asking.
        choices: Suggested answers, rendered as buttons.
    """

    id: str
    kind: str
    text: str
    cmd: str = ""
    cwd: str = ""
    thought: str = ""
    choices: tuple[str, ...] = ()
    at: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "text": self.text,
            "cmd": self.cmd,
            "cwd": self.cwd,
            "thought": self.thought,
            "choices": list(self.choices),
            "at": self.at,
        }


@dataclass
class _PendingAnswer:
    """The half of a prompt the run thread waits on."""

    event: threading.Event = field(default_factory=threading.Event)
    value: Any = None


class RunHandle:
    """The state of one run, shared between the run thread and HTTP handlers.

    Every public method is safe to call from either side. The run thread calls
    `record`, `set_node_status`, `request_approval` and `ask`; the HTTP handlers
    call `snapshot`, `answer` and `cancel`.
    """

    def __init__(self, name: str, root: Path, *, unattended: bool = False):
        self.name = name
        self.root = Path(root)
        self.unattended = unattended
        self.started_at = time.time()
        self.finished_at: float | None = None
        self.status = "starting"
        self.result: str | None = None
        self.error: str | None = None

        self._lock = threading.RLock()
        self._events: list[RunEvent] = []
        self._seq = 0
        self._dropped = 0
        self._nodes: dict[str, str] = {}
        self._prompt: Prompt | None = None
        self._answer = _PendingAnswer()
        self._prompt_seq = 0
        self._resume_status = "running"
        self._cancel = threading.Event()
        #: The plan node the run is inside, so narration that knows only what an
        #: agent did can still say where it happened. Set by the launcher at
        #: every node boundary; "" before the first one.
        self._current_node = ""

    # ---- called from the run thread -------------------------------------

    def _append(self, event: RunEvent) -> RunEvent:
        with self._lock:
            self._seq += 1
            event = dataclasses.replace(event, seq=self._seq, at=time.time())
            self._events.append(event)
            if len(self._events) > MAX_EVENTS:
                self._dropped += len(self._events) - MAX_EVENTS
                del self._events[:-MAX_EVENTS]
        return event

    def record(self, node: str, message: str, level: str = "info") -> RunEvent:
        """Append a progress event. Raises `RunCancelled` if a stop was asked for.

        Progress reporting is the one thing a run does at *every* node boundary,
        which makes it the natural place to notice a cancellation: there is no
        safe way to interrupt a thread mid-tool, so a cancel takes effect at the
        next boundary rather than immediately.
        """
        event = self._append(RunEvent(seq=0, at=0.0, node=node, message=message, level=level))
        if self._cancel.is_set():
            raise RunCancelled(f"Run for {self.name!r} was cancelled")
        return event

    def note(self, event: ActivityEvent) -> RunEvent:
        """Append one narrated step, attributed to the node the run is inside.

        Deliberately *not* a cancellation point, unlike `record`. This is called
        from inside a function tool, and the SDK turns an exception raised there
        into an error message handed back to the model — so a `RunCancelled`
        here would be swallowed into the conversation instead of unwinding the
        run. Cancelling still lands at the next node boundary, where `record` is.
        """
        return self._append(
            RunEvent(
                seq=0,
                at=0.0,
                node=self.current_node or "agent",
                message=event.message,
                level=event.level,
                kind=event.kind,
                agent=event.agent,
                detail=event.detail,
            )
        )

    def set_node_status(self, node: str, status: str) -> None:
        """Record what a plan node is doing, for the page's node colouring."""
        with self._lock:
            self._nodes[node] = status

    def set_current_node(self, node: str) -> None:
        """Say which plan node the run is inside, for attributing narration."""
        with self._lock:
            self._current_node = node

    @property
    def current_node(self) -> str:
        with self._lock:
            return self._current_node

    def request_approval(self, cmd: str, cwd: str = "", thought: str = "") -> Approval:
        """Block until a human approves or rejects `cmd`."""
        if self.unattended:
            return Approval(approved=True)
        answer = self._ask(
            Prompt(
                id="", kind="approval", text="Run this command?", cmd=cmd, cwd=cwd, thought=thought
            )
        )
        if answer is None:
            return Approval(approved=False, reason="No answer from the plan page (timed out).")
        if answer.get("approved"):
            return Approval(approved=True)
        return Approval(
            approved=False, reason=str(answer.get("reason") or "Rejected in the browser")
        )

    def ask(self, prompt: str, thought: str = "", choices: Sequence[str] = ()) -> str:
        """Block until a human answers a free-text question. "" when unanswered."""
        answer = self._ask(
            Prompt(id="", kind="question", text=prompt, thought=thought, choices=tuple(choices))
        )
        if answer is None:
            return ""
        return str(answer.get("text") or "")

    def _ask(self, prompt: Prompt) -> dict[str, Any] | None:
        """Publish a prompt, wait for its answer, and clear it. None on timeout.

        One prompt at a time: the agents in a run are driven sequentially, so a
        second question can only exist if the first was abandoned, and the page
        would have nowhere to show it anyway.
        """
        with self._lock:
            if self.status in TERMINAL_STATUSES:
                return None
            self._prompt_seq += 1
            published = Prompt(
                id=f"{self._prompt_seq}",
                kind=prompt.kind,
                text=prompt.text,
                cmd=prompt.cmd,
                cwd=prompt.cwd,
                thought=prompt.thought,
                choices=prompt.choices,
                at=time.time(),
            )
            self._prompt = published
            self._answer = _PendingAnswer()
            waiter = self._answer
            self._resume_status = self.status
            self.status = "blocked"
        self.record(
            "human",
            published.cmd or published.text if published.kind == "approval" else published.text,
        )

        answered = waiter.event.wait(PROMPT_TIMEOUT_SECONDS)

        with self._lock:
            self._prompt = None
            if self.status == "blocked":
                self.status = self._resume_status or "running"
            value = waiter.value if answered else None
        if not answered:
            self.record(
                "human", "no answer within the timeout; treating it as no answer", "warning"
            )
        return value

    # ---- called from the server ------------------------------------------

    def answer(self, prompt_id: str, payload: dict[str, Any]) -> bool:
        """Deliver a human's answer. False if no prompt with that id is waiting."""
        with self._lock:
            pending = self._prompt
            if pending is None or pending.id != str(prompt_id):
                return False
            waiter = self._answer
            waiter.value = payload
        waiter.event.set()
        return True

    def cancel(self) -> None:
        """Ask the run to stop at its next node boundary.

        A thread cannot be interrupted safely, so this does not kill anything in
        flight: the command that is running finishes, and the run unwinds when it
        next reports progress. A blocked prompt is released immediately, since
        nothing else would ever come to free it.
        """
        self._cancel.set()
        with self._lock:
            waiter, pending = self._answer, self._prompt
        if pending is not None:
            waiter.value = {"approved": False, "reason": "The run was cancelled.", "text": ""}
            waiter.event.set()

    @property
    def cancelled(self) -> bool:
        """True once a cancel has been requested."""
        return self._cancel.is_set()

    def finish(self, *, result: str | None = None, error: str | None = None) -> None:
        """Mark the run terminal. Called once, by the supervising thread."""
        with self._lock:
            self.finished_at = time.time()
            self.result = result
            self.error = error
            if error is not None:
                self.status = "cancelled" if self._cancel.is_set() else "failed"
            else:
                self.status = "done"

    def snapshot(self, since: int = 0) -> dict[str, Any]:
        """Everything the page needs, with events after `since` only."""
        with self._lock:
            return {
                "name": self.name,
                "status": self.status,
                "unattended": self.unattended,
                "started_at": self.started_at,
                "finished_at": self.finished_at,
                "nodes": dict(self._nodes),
                "events": [e.to_dict() for e in self._events if e.seq > since],
                "latest_seq": self._seq,
                "dropped_events": self._dropped,
                "prompt": self._prompt.to_dict() if self._prompt else None,
                "result": self.result,
                "error": self.error,
                "active": self.status not in TERMINAL_STATUSES,
            }


class RunRegistry:
    """The runs this process is supervising, keyed by resolved analysis root."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._runs: dict[str, RunHandle] = {}
        self._threads: dict[str, threading.Thread] = {}

    @staticmethod
    def _key(analysis_root: Path | str) -> str:
        return str(Path(analysis_root).resolve())

    def get(self, analysis_root: Path | str) -> RunHandle | None:
        """The run for an analysis, finished or not."""
        with self._lock:
            return self._runs.get(self._key(analysis_root))

    def active(self, analysis_root: Path | str) -> RunHandle | None:
        """The run for an analysis only while it is still going."""
        handle = self.get(analysis_root)
        return handle if handle is not None and handle.status not in TERMINAL_STATUSES else None

    def start(
        self,
        name: str,
        analysis_root: Path | str,
        runner: Callable[[RunHandle], str | None],
        *,
        unattended: bool = False,
    ) -> RunHandle:
        """Run `runner` on a worker thread and return its handle.

        Args:
            name: Analysis name, for messages.
            analysis_root: Directory the run belongs to; also the registry key.
            runner: Called with the handle *on the worker thread*. It should do
                the whole run and return a result string. Anything it raises is
                recorded on the handle rather than propagated.
            unattended: Auto-approve shell commands instead of asking the page.

        Raises:
            RunAlreadyActive: if this analysis is already running.
        """
        key = self._key(analysis_root)
        with self._lock:
            existing = self._runs.get(key)
            if existing is not None and existing.status not in TERMINAL_STATUSES:
                raise RunAlreadyActive(f"An analysis run for {name!r} is already in progress.")
            handle = RunHandle(name=name, root=Path(analysis_root), unattended=unattended)
            self._runs[key] = handle

            def supervise() -> None:
                handle.status = "running"
                try:
                    result = runner(handle)
                except RunCancelled as exc:
                    handle.finish(error=str(exc))
                except BaseException as exc:  # noqa: BLE001 - a run must not kill the server
                    handle.record("run", f"failed: {exc}", "error")
                    handle.finish(error=f"{exc}\n{traceback.format_exc()}")
                else:
                    handle.finish(result=str(result) if result is not None else None)

            thread = threading.Thread(target=supervise, name=f"hepagent-run-{name}", daemon=True)
            self._threads[key] = thread
            thread.start()
            return handle

    def reset(self) -> None:
        """Forget every run. For tests."""
        with self._lock:
            self._runs.clear()
            self._threads.clear()


class RunInteractionBackend:
    """Routes an agent's blocking questions to a `RunHandle`.

    Installed on the run thread by the launcher, which is what turns the plan
    page into the place a running analysis talks to its human.
    """

    def __init__(self, handle: RunHandle):
        self._handle = handle

    def approve(self, cmd: str, cwd: str = "", thought: str = "") -> Approval:
        return self._handle.request_approval(cmd=cmd, cwd=cwd, thought=thought)

    def ask(self, prompt: str, thought: str = "", choices: Sequence[str] = ()) -> str:
        return self._handle.ask(prompt, thought=thought, choices=choices)


class RunActivitySink:
    """Routes an agent's narration to a `RunHandle`.

    The other half of `RunInteractionBackend`: that one carries the questions an
    agent asks, this one carries what it is doing while it is not asking. Bound
    on the run thread by the launcher, which is what makes the plan page — and
    not the terminal that happens to be running the server — the place a run is
    watched from.
    """

    def __init__(self, handle: RunHandle):
        self._handle = handle

    def activity(self, event: ActivityEvent) -> None:
        self._handle.note(event)


#: Process-wide registry. The plan editor and the run share one process, the
#: same assumption `plan.service.APPROVAL_GATE` already makes.
RUNS = RunRegistry()
