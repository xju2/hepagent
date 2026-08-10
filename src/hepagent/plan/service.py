"""Stdlib-only façade over the plan, shared by the CLI and the web editor.

Neither the FastAPI router nor the Typer commands should know how a plan is
stored, laid out or validated — they only translate. Everything they need is
here, which is also what lets the editor's behaviour be tested in CI without
FastAPI or a browser.

The approval gate is the handshake that makes "review before any agentic work
begins" real: the orchestrator awaits it, and the editor releases it.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Collection
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from hepagent.plan import layout, store
from hepagent.plan.compile import execution_order
from hepagent.plan.schema import AnalysisPlan
from hepagent.plan.validate import validate_plan

#: Names the directory analyses live in. The editor is launched from a CLI that
#: knows where they are; the browser only ever sends an analysis name.
BASE_DIR_ENV = "HEPAGENT_ANALYSES_DIR"


def analyses_dir(base_dir: str | Path | None = None) -> Path:
    """Resolve the directory analyses live in.

    Lives here rather than in the web layer so the `/plan` chat command can find
    analyses without importing FastAPI — CI installs neither web extra.
    """
    return Path(base_dir or os.environ.get(BASE_DIR_ENV) or "analyses").resolve()


def resolve_analysis(name: str, base_dir: str | Path | None = None) -> Path:
    """Resolve an analysis name to its root, refusing anything that escapes.

    The name arrives from a URL and the editor *writes* through it, so a `../`
    would otherwise reach any plan the server user can touch.

    Raises:
        ValueError: if the name escapes the base directory.
        FileNotFoundError: if no such analysis exists.
    """
    base = analyses_dir(base_dir)
    root = (base / name).resolve()
    if root != base and base not in root.parents:
        raise ValueError(f"Invalid analysis name: {name!r}")
    if not root.is_dir():
        raise FileNotFoundError(f"No analysis named {name!r} in {base}")
    return root


def list_planned(base_dir: str | Path | None = None) -> list[str]:
    """Names of the analyses under `base_dir` that carry a plan."""
    base = analyses_dir(base_dir)
    if not base.is_dir():
        return []
    return [child.name for child in sorted(base.iterdir()) if store.has_plan(child)]


@dataclass(frozen=True)
class PlanView:
    """Everything the editor needs to draw and judge a plan in one payload.

    Args:
        plan: The plan document, as JSON-ready data.
        layout: ``{node_id: [column, row]}`` from the auto-layout. The editor
            prefers a node's stored ``metadata.x``/``y`` when the user has
            dragged it and falls back to this.
        order: Node ids in the order the plan says they run.
        findings: Validation findings, most severe first.
        blocking: True when a finding stops the plan from running.
        approved: True when this plan has been released to the orchestrator.
    """

    plan: dict[str, Any]
    layout: dict[str, list[int]]
    order: list[str]
    findings: list[dict[str, Any]] = field(default_factory=list)
    blocking: bool = False
    approved: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "plan": self.plan,
            "layout": self.layout,
            "order": self.order,
            "findings": self.findings,
            "blocking": self.blocking,
            "approved": self.approved,
        }


def build_view(
    plan: AnalysisPlan,
    *,
    known_reviewers: Collection[str] | None = None,
    approved: bool = False,
) -> PlanView:
    """Render a plan into the payload the editor consumes."""
    report = validate_plan(plan, known_reviewers=known_reviewers)
    findings = [
        {
            "rule": f.rule,
            "severity": f.severity,
            "message": f.message,
            "node_id": f.node_id,
        }
        for f in sorted(report.findings, key=lambda f: (f.severity != "error", f.rule))
    ]
    return PlanView(
        plan=plan.to_dict(),
        layout={node_id: [column, row] for node_id, (column, row) in layout.layer(plan).items()},
        order=execution_order(plan),
        findings=findings,
        blocking=bool(report.blocking),
        approved=approved,
    )


def get_plan_view(
    analysis_root: Path | str,
    *,
    known_reviewers: Collection[str] | None = None,
    gate: PlanApprovalGate | None = None,
) -> PlanView:
    """Load the plan for an analysis and render it for the editor.

    Raises:
        store.PlanNotFoundError: if the analysis has no plan yet.
    """
    plan = store.load_plan(analysis_root)
    approved = (gate or APPROVAL_GATE).is_approved(analysis_root)
    return build_view(plan, known_reviewers=known_reviewers, approved=approved)


def put_plan(
    analysis_root: Path | str,
    payload: dict[str, Any],
    *,
    known_reviewers: Collection[str] | None = None,
    gate: PlanApprovalGate | None = None,
) -> PlanView:
    """Save an edited plan and return the fresh view.

    A plan with blocking findings is still **saved** — a user must be able to
    park half-finished work — but the view reports `blocking`, and
    :func:`approve` refuses to release it while that stands.

    Raises:
        store.PlanFormatError: if the payload is not readable as a plan.
    """
    plan = store.plan_from_dict(payload, source="submitted plan")
    written = store.save_plan(analysis_root, plan)
    # Editing a plan withdraws any approval it already had: what the
    # orchestrator was released to run is no longer what is on disk.
    (gate or APPROVAL_GATE).revoke(analysis_root)
    return build_view(written, known_reviewers=known_reviewers, approved=False)


def approve(
    analysis_root: Path | str,
    *,
    known_reviewers: Collection[str] | None = None,
    gate: PlanApprovalGate | None = None,
) -> PlanView:
    """Release the plan to the orchestrator.

    Refuses while any blocking finding stands — the editor disables its button
    for the same reason, but a direct API call must not be able to bypass it.

    Raises:
        PlanNotApprovable: if the plan has blocking findings.
    """
    plan = store.load_plan(analysis_root)
    view = build_view(plan, known_reviewers=known_reviewers)
    if view.blocking:
        raise PlanNotApprovable(
            "The plan has "
            f"{sum(1 for f in view.findings if f['severity'] == 'error')} blocking "
            "finding(s) and cannot be run until they are resolved."
        )
    (gate or APPROVAL_GATE).approve(analysis_root)
    return build_view(plan, known_reviewers=known_reviewers, approved=True)


class PlanNotApprovable(ValueError):
    """Raised when a plan is released for a run while still structurally broken."""


class PlanApprovalGate:
    """One approval latch per analysis root.

    The orchestrator awaits :meth:`wait` before running the first node; the
    editor calls :meth:`approve` when the user is satisfied. Roots are keyed by
    resolved path so a relative and an absolute reference to the same analysis
    are the same latch.

    The `asyncio.Event` for a root is created lazily on first use, which keeps
    the module-level singleton safe to import before any loop is running.
    """

    def __init__(self) -> None:
        self._events: dict[str, asyncio.Event] = {}

    def _key(self, analysis_root: Path | str) -> str:
        return str(Path(analysis_root).resolve())

    def _event(self, analysis_root: Path | str) -> asyncio.Event:
        key = self._key(analysis_root)
        event = self._events.get(key)
        if event is None:
            event = asyncio.Event()
            self._events[key] = event
        return event

    def approve(self, analysis_root: Path | str) -> None:
        """Release anything waiting on this analysis."""
        self._event(analysis_root).set()

    def revoke(self, analysis_root: Path | str) -> None:
        """Withdraw approval, so the next wait blocks again."""
        self._event(analysis_root).clear()

    def is_approved(self, analysis_root: Path | str) -> bool:
        """True when this analysis has been released."""
        return self._event(analysis_root).is_set()

    async def wait(self, analysis_root: Path | str, timeout: float | None = None) -> bool:
        """Block until the plan is approved.

        Args:
            analysis_root: The analysis to wait on.
            timeout: Seconds to wait, or None to wait indefinitely.

        Returns:
            True if the plan was approved, False if the wait timed out.
        """
        event = self._event(analysis_root)
        if timeout is None:
            await event.wait()
            return True
        try:
            await asyncio.wait_for(event.wait(), timeout)
        except TimeoutError:
            return False
        return True

    def reset(self) -> None:
        """Forget every latch. For tests."""
        self._events.clear()


#: Process-wide gate. The web server and the orchestrator share one process when
#: the editor is served from `hepagent web`, which is what makes this work.
APPROVAL_GATE = PlanApprovalGate()
