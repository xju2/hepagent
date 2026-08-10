"""Derive execution order from the analysis graph instead of a hardcoded list.

The orchestrator used to walk a literal `PHASE_ORDER`. It now asks this module
what to run next, and the answer comes from the graph's `requires` edges — which
`bootstrap_graph` builds from `UPSTREAM_ARTIFACTS`, the same map the executors
read their upstream artifacts from. The resulting order is identical to the old
literal; it is simply derived rather than asserted, so a change to what a phase
depends on changes what runs when.

`PHASE_ORDER` survives only as a tiebreak between phases that are equally ready,
and as a fallback when the graph is missing or unreadable.
"""

from __future__ import annotations

from collections.abc import Collection, Iterable
from dataclasses import dataclass, field
from pathlib import Path

from hepagent.graph import query
from hepagent.graph.store import AnalysisGraph

# Canonical ordering, used only to break ties and as a fallback.
PHASE_ORDER: list[int | str] = [1, 2, 3, "4a", "4b", "4c", 5]

_PHASE_RANK: dict[str, int] = {str(p): i for i, p in enumerate(PHASE_ORDER)}


@dataclass
class PhaseReadiness:
    """Why a phase can or cannot run yet.

    Args:
        phase: Phase key, e.g. "3" or "4a".
        ready: True when every prerequisite phase is complete.
        blocked_by: Prerequisite phase keys that are not complete yet.
        complete: True when the phase has already passed review.
    """

    phase: str
    ready: bool
    blocked_by: list[str] = field(default_factory=list)
    complete: bool = False


def phase_dependencies(graph: AnalysisGraph) -> dict[str, list[str]]:
    """Map each phase to the phases it depends on, read from `requires` edges.

    Only artifact-to-artifact prerequisites count as phase dependencies; an
    artifact that merely requires the problem node has no upstream phase.
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
        dependencies[node.phase] = sorted(upstream, key=_rank)
    return dependencies


def phase_readiness(
    graph: AnalysisGraph,
    completed: Collection[str],
) -> list[PhaseReadiness]:
    """Report, for every phase in the graph, whether it can run now."""
    done = {str(p) for p in completed}
    dependencies = phase_dependencies(graph)

    readiness: list[PhaseReadiness] = []
    for phase in sorted(dependencies, key=_rank):
        blocked = [p for p in dependencies[phase] if p not in done]
        readiness.append(
            PhaseReadiness(
                phase=phase,
                ready=not blocked,
                blocked_by=blocked,
                complete=phase in done,
            )
        )
    return readiness


def ready_phases(graph: AnalysisGraph, completed: Collection[str]) -> list[str]:
    """Phases whose prerequisites are all met and which have not passed yet."""
    return [r.phase for r in phase_readiness(graph, completed) if r.ready and not r.complete]


def next_phase(
    graph: AnalysisGraph,
    completed: Collection[str],
    skip: Iterable[str] = (),
) -> str | None:
    """Return the next phase to expand, or None when nothing is runnable.

    Falls back to the canonical order when the graph carries no phase structure,
    so an analysis whose graph is missing still runs.

    Args:
        graph: The loaded analysis graph.
        completed: Phase keys that have already passed review.
        skip: Phase keys to exclude — used to avoid re-offering a phase that
            just ran without completing.
    """
    done = {str(p) for p in completed}
    excluded = {str(p) for p in skip}

    candidates = [p for p in ready_phases(graph, done) if p not in excluded]
    if not candidates:
        if phase_dependencies(graph):
            return None  # the graph knows about phases; none are runnable
        # No phase structure in the graph at all — fall back to the literal order.
        for phase in PHASE_ORDER:
            key = str(phase)
            if key not in done and key not in excluded:
                return key
        return None

    return min(candidates, key=_rank)


def is_consistent_checkpoint(graph: AnalysisGraph, phase: str) -> bool:
    """True when a phase's output exists and nothing has invalidated it.

    A phase whose artifact is on disk but which a reviewer has since rejected is
    not a checkpoint worth resuming from — the rejection has to be addressed.
    """
    for node in graph.nodes(type="artifact", phase=phase):
        if not query.is_realized(graph, node):
            return False
        if query.rejections(graph, node.id):
            return False
        return True
    return False


def last_consistent_phase(graph: AnalysisGraph, completed: Collection[str]) -> str | None:
    """The latest phase that both passed review and still holds up in the graph."""
    done = {str(p) for p in completed}
    latest: str | None = None
    for phase in sorted(done, key=_rank):
        if is_consistent_checkpoint(graph, phase):
            latest = phase
    return latest


def resume_point(analysis_root: Path | str, completed: Collection[str]) -> str:
    """Pick where to restart an interrupted analysis.

    Resumes at the first phase whose prerequisites are satisfied by the most
    recent consistent checkpoint — not simply at "the phase after the last one
    that finished", which would skip past work a later review invalidated.

    Args:
        analysis_root: The analysis root directory.
        completed: Phase keys recorded as complete in the orchestration state.

    Returns:
        A phase key. Falls back to "1" when nothing can be determined.
    """
    try:
        graph = AnalysisGraph.load(analysis_root)
    except Exception:  # noqa: BLE001 - resumption must not fail on a broken graph
        return "1"

    checkpoint = last_consistent_phase(graph, completed)
    trustworthy = (
        {p for p in (str(c) for c in completed) if _rank(p) <= _rank(checkpoint)}
        if checkpoint is not None
        else set()
    )
    return next_phase(graph, trustworthy) or "1"


def _rank(phase: str | int | None) -> int:
    """Sort key placing unknown phases after the canonical ones."""
    return _PHASE_RANK.get(str(phase), len(PHASE_ORDER))
