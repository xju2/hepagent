"""Derive execution order from the analysis graph.

The orchestrator does not walk the plan. It asks this module what to run next,
and the answer comes from the graph's `requires` edges — which `bootstrap_graph`
compiles from the plan, so what a node depends on is authored once and read back
as structure. Asking the *graph* rather than the plan is what makes a regression
work without special casing: an `invalidates` edge un-completes a node, and that
node becomes runnable again on the next question.

The plan is consulted only for the two things the graph cannot answer: which of
several equally ready nodes to prefer (declaration order), and what to run when
the graph is missing or unreadable.
"""

from __future__ import annotations

from collections.abc import Collection, Iterable
from dataclasses import dataclass, field
from pathlib import Path

from hepagent.graph import query
from hepagent.graph.store import AnalysisGraph
from hepagent.plan.schema import AnalysisPlan
from hepagent.plan.store import resolve_plan


@dataclass
class PhaseReadiness:
    """Why a node can or cannot run yet.

    Args:
        phase: Plan node id, e.g. "selection".
        ready: True when every prerequisite node is complete.
        blocked_by: Prerequisite node ids that are not complete yet.
        complete: True when the node has already passed review.
    """

    phase: str
    ready: bool
    blocked_by: list[str] = field(default_factory=list)
    complete: bool = False


def phase_dependencies(graph: AnalysisGraph) -> dict[str, list[str]]:
    """Map each node to the nodes it depends on, read from `requires` edges.

    Only artifact-to-artifact prerequisites count; an artifact that merely
    requires the problem node has no upstream work.
    """
    dependencies: dict[str, list[str]] = {}
    for node in graph.nodes(type="artifact"):
        if not node.phase:
            continue
        upstream: set[str] = set()
        for edge in graph.out_edges(node.id, type="requires"):
            target = graph.get_node(edge.dst)
            if target is not None and target.type == "artifact" and target.phase:
                if target.phase != node.phase:
                    upstream.add(target.phase)
        dependencies[node.phase] = sorted(upstream)
    return dependencies


def phase_readiness(
    graph: AnalysisGraph,
    completed: Collection[str],
    plan: AnalysisPlan | None = None,
) -> list[PhaseReadiness]:
    """Report, for every node in the graph, whether it can run now."""
    done = {str(p) for p in completed}
    dependencies = phase_dependencies(graph)
    rank = _ranker(plan)

    readiness: list[PhaseReadiness] = []
    for phase in sorted(dependencies, key=rank):
        blocked = [p for p in dependencies[phase] if p not in done]
        readiness.append(
            PhaseReadiness(
                phase=phase,
                ready=not blocked,
                blocked_by=sorted(blocked, key=rank),
                complete=phase in done,
            )
        )
    return readiness


def ready_phases(
    graph: AnalysisGraph,
    completed: Collection[str],
    plan: AnalysisPlan | None = None,
) -> list[str]:
    """Nodes whose prerequisites are all met and which have not passed yet."""
    return [r.phase for r in phase_readiness(graph, completed, plan) if r.ready and not r.complete]


def next_phase(
    graph: AnalysisGraph,
    completed: Collection[str],
    skip: Iterable[str] = (),
    plan: AnalysisPlan | None = None,
) -> str | None:
    """Return the next node to expand, or None when nothing is runnable.

    Falls back to the plan's declaration order when the graph carries no node
    structure, so an analysis whose graph is missing still runs.

    Args:
        graph: The loaded analysis graph.
        completed: Node ids that have already passed review.
        skip: Node ids to exclude — used to avoid re-offering a node that just
            ran without completing.
        plan: The analysis plan, for tiebreaks and the fallback order.
    """
    done = {str(p) for p in completed}
    excluded = {str(p) for p in skip}
    rank = _ranker(plan)

    candidates = [p for p in ready_phases(graph, done, plan) if p not in excluded]
    if not candidates:
        if phase_dependencies(graph):
            return None  # the graph knows about nodes; none are runnable
        # No node structure in the graph at all — fall back to the plan.
        for node_id in plan.node_ids() if plan is not None else ():
            if node_id not in done and node_id not in excluded:
                return node_id
        return None

    return min(candidates, key=rank)


def is_consistent_checkpoint(graph: AnalysisGraph, phase: str) -> bool:
    """True when a node's output exists and nothing has invalidated it.

    A node whose artifact is on disk but which a reviewer has since rejected is
    not a checkpoint worth resuming from — the rejection has to be addressed.
    """
    for node in graph.nodes(type="artifact", phase=phase):
        if not query.is_realized(graph, node):
            return False
        if query.rejections(graph, node.id):
            return False
        return True
    return False


def last_consistent_phase(
    graph: AnalysisGraph,
    completed: Collection[str],
    plan: AnalysisPlan | None = None,
) -> str | None:
    """The latest node that both passed review and still holds up in the graph."""
    rank = _ranker(plan)
    latest: str | None = None
    for phase in sorted({str(p) for p in completed}, key=rank):
        if is_consistent_checkpoint(graph, phase):
            latest = phase
    return latest


def resume_point(
    analysis_root: Path | str,
    completed: Collection[str],
    plan: AnalysisPlan | None = None,
) -> str | None:
    """Pick where to restart an interrupted analysis.

    Resumes at the first node whose prerequisites are satisfied by the most
    recent consistent checkpoint — not simply at "the node after the last one
    that finished", which would skip past work a later review invalidated.

    Args:
        analysis_root: The analysis root directory.
        completed: Node ids recorded as complete in the orchestration state.
        plan: The analysis plan. Read from `plan.json` when omitted.

    Returns:
        A node id, or None when nothing can be determined.
    """
    try:
        plan = resolve_plan(analysis_root, plan)
    except Exception:  # noqa: BLE001 - resumption must not fail on a missing plan
        plan = None

    first = plan.node_ids()[0] if plan is not None and plan.nodes else None

    try:
        graph = AnalysisGraph.load(analysis_root)
    except Exception:  # noqa: BLE001 - resumption must not fail on a broken graph
        return first

    rank = _ranker(plan)
    checkpoint = last_consistent_phase(graph, completed, plan)
    trustworthy = (
        {p for p in (str(c) for c in completed) if rank(p) <= rank(checkpoint)}
        if checkpoint is not None
        else set()
    )
    return next_phase(graph, trustworthy, plan=plan) or first


def _ranker(plan: AnalysisPlan | None):
    """Return a sort key placing nodes in the plan's declaration order.

    Nodes the plan does not declare sort after the ones it does, alphabetically,
    so a graph carrying a node the plan has since dropped still orders stably.
    """
    order = {node_id: index for index, node_id in enumerate(plan.node_ids())} if plan else {}
    size = len(order)

    def rank(phase: str | None) -> tuple[int, str]:
        key = str(phase)
        return (order.get(key, size), key)

    return rank
