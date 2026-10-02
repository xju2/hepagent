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
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from hepagent.plan import layout, store
from hepagent.plan.compile import execution_order, problem_node as compile_problem_node
from hepagent.plan.schema import AnalysisPlan
from hepagent.plan.validate import PlanVocabulary, validate_plan

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
        catalog: The names the editor may offer, keyed ``reviewers``, ``tools``,
            ``skills``, ``mcp_servers``. Only catalogs the caller actually knows
            appear; the editor falls back to what the plan already uses for the
            rest, so a browser never invents a name the validator would reject.
    """

    plan: dict[str, Any]
    layout: dict[str, list[int]]
    order: list[str]
    findings: list[dict[str, Any]] = field(default_factory=list)
    blocking: bool = False
    approved: bool = False
    catalog: dict[str, list[str]] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "plan": self.plan,
            "layout": self.layout,
            "order": self.order,
            "findings": self.findings,
            "blocking": self.blocking,
            "approved": self.approved,
            "catalog": self.catalog,
        }


def grid(plan: AnalysisPlan) -> dict[str, list[int]]:
    """The auto-layout as JSON-ready ``{node_id: [column, row]}``."""
    return {node_id: [column, row] for node_id, (column, row) in layout.layer(plan).items()}


def preview_layout(payload: dict[str, Any]) -> dict[str, list[int]]:
    """Auto-layout a plan document that has not been saved, without touching disk.

    The editor needs this because a plan grows nodes and edges in the browser
    long before anyone saves: the layout that came with the last view is stale
    the moment a node is added, and re-deriving it in JavaScript would fork the
    one layering algorithm `plan show` and the editor are meant to share.

    Raises:
        store.PlanFormatError: if the payload is not readable as a plan.
    """
    return grid(store.plan_from_dict(payload, source="submitted plan"))


def predefined_nodes(analysis_root: Path | str | None = None) -> list[dict[str, Any]]:
    """The nodes a user may drop into a plan, ready to insert.

    Today's library is the nodes of the built-in JFC templates, resolved for the
    analysis they are being offered to; more sources can be appended here later
    without the editor or the route learning anything new.

    Kept off `PlanView` on purpose: the catalog is only wanted when the user
    opens the picker, while a view is rebuilt on every save.

    Args:
        analysis_root: The analysis the nodes are offered to. Its plan name is
            substituted into the prompts; a root with no readable plan falls
            back to the directory name, and `None` to a neutral placeholder.
    """
    from hepagent.plan.templates import predefined_nodes as library

    name = "analysis"
    if analysis_root is not None:
        root = Path(analysis_root)
        name = root.name or name
        try:
            name = store.load_plan(root).name or name
        except (store.PlanNotFoundError, store.PlanFormatError):
            pass
    return library(analysis_name=name)


def problem_history(analysis_root: Path | str) -> list[dict[str, Any]]:
    """Every wording the physics prompt has had, newest first.

    Reads `plan.history/` — the prompt is a field of the plan, so its history is
    the plan's history, and nothing extra had to be recorded to get it. Only the
    revisions where the wording actually *changed* are returned: a plan is saved
    on every node drag, and a list of forty identical prompts tells a reader
    nothing.

    Returns:
        ``[{"revision": int, "updated_at": str, "problem": str,
        "current": bool}]``, newest first. Empty when the analysis has no plan.
    """
    root = Path(analysis_root)
    try:
        current = store.load_plan(root)
    except (store.PlanNotFoundError, store.PlanFormatError):
        return []

    entries: list[dict[str, Any]] = []
    for revision in store.list_revisions(root):
        try:
            archived = store.load_revision(root, revision)
        except (store.PlanNotFoundError, store.PlanFormatError):
            continue
        entries.append(
            {
                "revision": archived.revision,
                "updated_at": archived.updated_at,
                "problem": archived.problem,
                "current": False,
            }
        )
    entries.append(
        {
            "revision": current.revision,
            "updated_at": current.updated_at,
            "problem": current.problem,
            "current": True,
        }
    )

    changed: list[dict[str, Any]] = []
    for entry in entries:
        if changed and changed[-1]["problem"].strip() == entry["problem"].strip():
            # Same wording, newer revision: keep the entry that introduced it,
            # but let the reader see it is still what the plan says today.
            changed[-1]["current"] = changed[-1]["current"] or entry["current"]
            continue
        changed.append(entry)
    return list(reversed(changed))


def record_problem(analysis_root: Path | str, plan: AnalysisPlan) -> bool:
    """Write the plan's physics question into the provenance graph.

    The graph is append-only, so re-adding the problem node with a new wording
    keeps the superseded record: `graph/nodes.jsonl` ends up carrying every
    question the analysis was ever asked, in order, and every artifact ingested
    afterwards descends from the wording current at the time.

    Only writes to a graph that already exists — an analysis that has never run
    is seeded from the plan anyway, by `bootstrap_graph`.

    Returns:
        True when the graph was touched. Never raises: graph work must not be
        able to stop a user saving their plan.
    """
    try:
        from hepagent.graph.store import AnalysisGraph

        graph_dir = Path(analysis_root) / "graph"
        if not graph_dir.is_dir():
            return False
        graph = AnalysisGraph.load(analysis_root)
        graph.add_node(compile_problem_node(plan))
        return True
    except Exception:  # noqa: BLE001 - provenance is a record, not a gate
        return False


def catalog_of(vocabulary: PlanVocabulary | None) -> dict[str, list[str]]:
    """The vocabulary as sorted JSON-ready lists, omitting the unknown catalogs.

    An absent key and an empty list say different things to the editor: the
    first means "nobody told the server what exists here", the second means
    "there are none". The editor offers what the plan already uses in the first
    case and an empty dropdown in the second.
    """
    if vocabulary is None:
        return {}
    return {
        name: sorted(values)
        for name, values in (
            ("reviewers", vocabulary.reviewers),
            ("tools", vocabulary.tools),
            ("skills", vocabulary.skills),
            ("mcp_servers", vocabulary.mcp_servers),
            ("platforms", vocabulary.platforms),
        )
        if values is not None
    }


def build_view(
    plan: AnalysisPlan,
    *,
    vocabulary: PlanVocabulary | None = None,
    approved: bool = False,
) -> PlanView:
    """Render a plan into the payload the editor consumes."""
    report = validate_plan(plan, vocabulary=vocabulary)
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
        layout=grid(plan),
        order=execution_order(plan),
        findings=findings,
        blocking=bool(report.blocking),
        approved=approved,
        catalog=catalog_of(vocabulary),
    )


def get_plan_view(
    analysis_root: Path | str,
    *,
    vocabulary: PlanVocabulary | None = None,
    gate: PlanApprovalGate | None = None,
) -> PlanView:
    """Load the plan for an analysis and render it for the editor.

    Raises:
        store.PlanNotFoundError: if the analysis has no plan yet.
    """
    plan = store.load_plan(analysis_root)
    approved = (gate or APPROVAL_GATE).is_approved(analysis_root)
    return build_view(plan, vocabulary=vocabulary, approved=approved)


def put_plan(
    analysis_root: Path | str,
    payload: dict[str, Any],
    *,
    vocabulary: PlanVocabulary | None = None,
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
    # `save_plan` mirrored the question into `prompt.md`; this puts the same
    # wording in the provenance graph, so an edited prompt is traceable there too.
    record_problem(analysis_root, written)
    # Editing a plan withdraws any approval it already had: what the
    # orchestrator was released to run is no longer what is on disk.
    (gate or APPROVAL_GATE).revoke(analysis_root)
    return build_view(written, vocabulary=vocabulary, approved=False)


def approve(
    analysis_root: Path | str,
    *,
    vocabulary: PlanVocabulary | None = None,
    gate: PlanApprovalGate | None = None,
) -> PlanView:
    """Release the plan to the orchestrator.

    Refuses while any blocking finding stands — the editor disables its button
    for the same reason, but a direct API call must not be able to bypass it.

    Raises:
        PlanNotApprovable: if the plan has blocking findings.
    """
    plan = store.load_plan(analysis_root)
    view = build_view(plan, vocabulary=vocabulary)
    if view.blocking:
        raise PlanNotApprovable(
            "The plan has "
            f"{sum(1 for f in view.findings if f['severity'] == 'error')} blocking "
            "finding(s) and cannot be run until they are resolved."
        )
    (gate or APPROVAL_GATE).approve(analysis_root)
    return build_view(plan, vocabulary=vocabulary, approved=True)


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
        self._waiting: dict[str, int] = {}

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

    def waiting(self, analysis_root: Path | str) -> int:
        """How many runs are blocked on this latch right now.

        Approval means two different things depending on this number. With a run
        already waiting — `jfc run --review-plan` — approving *releases* it, and
        starting a second run would run the analysis twice. With nobody waiting —
        the plan page opened on its own — approving releases nothing, so the page
        has to start the run itself.
        """
        return self._waiting.get(self._key(analysis_root), 0)

    async def wait(self, analysis_root: Path | str, timeout: float | None = None) -> bool:
        """Block until the plan is approved.

        Args:
            analysis_root: The analysis to wait on.
            timeout: Seconds to wait, or None to wait indefinitely.

        Returns:
            True if the plan was approved, False if the wait timed out.
        """
        event = self._event(analysis_root)
        key = self._key(analysis_root)
        self._waiting[key] = self._waiting.get(key, 0) + 1
        try:
            if timeout is None:
                await event.wait()
                return True
            try:
                await asyncio.wait_for(event.wait(), timeout)
            except TimeoutError:
                return False
            return True
        finally:
            self._waiting[key] = max(0, self._waiting.get(key, 0) - 1)

    def reset(self) -> None:
        """Forget every latch. For tests."""
        self._events.clear()
        self._waiting.clear()


#: Process-wide gate. The web server and the orchestrator share one process when
#: the editor is served from `hepagent web`, which is what makes this work.
APPROVAL_GATE = PlanApprovalGate()
