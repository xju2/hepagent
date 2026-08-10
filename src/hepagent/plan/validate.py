"""Consistency rules over an `AnalysisPlan`.

These run before any agent work starts and on every editor save, which makes
them the cheapest place in the system to catch a structural mistake: a cycle, a
node two others would overwrite, an edge pointing at something that was deleted.

The severity model is deliberately the same as `hepagent.graph.validation`, and
so are the report types — an `error` states something that is false about the
plan regardless of context and blocks the run; a `warning` is a judgement call
about how the plan is shaped and is shown but never blocks. `report.blocking`
splits the two, and the editor's Approve button reads exactly that.
"""

from __future__ import annotations

from collections.abc import Collection
from pathlib import PurePosixPath

from hepagent.graph.schema import EDGE_TYPES, NODE_TYPES
from hepagent.graph.validation import GraphFinding, GraphValidationReport
from hepagent.plan.schema import (
    EDGE_KINDS,
    GATES,
    INJECT_MODES,
    ROLES,
    AnalysisPlan,
)

PLAN_REPORT_TITLE = "Plan validation"


def validate_plan(
    plan: AnalysisPlan,
    *,
    known_reviewers: Collection[str] | None = None,
    known_roles: Collection[str] = ROLES,
) -> GraphValidationReport:
    """Run every rule over `plan`.

    Args:
        plan: The plan to check.
        known_reviewers: Reviewer names the runtime can actually build. Left as
            ``None`` the names are only checked for being non-empty — this module
            stays domain-agnostic, so the JFC reviewer registry is passed in by
            the caller rather than imported here.
        known_roles: Agent roles the runtime can build.

    Returns:
        A report whose `blocking` findings must be cleared before the plan runs.
    """
    findings: list[GraphFinding] = []
    findings += rule_unique_ids(plan)
    findings += rule_unique_outputs(plan)
    findings += rule_edge_endpoints(plan)
    findings += rule_acyclic(plan)
    findings += rule_single_entry(plan)
    findings += rule_known_vocabulary(plan, known_reviewers, known_roles)
    findings += rule_contracts(plan)
    findings += rule_note_artifacts(plan)
    findings += rule_reachable(plan)
    return GraphValidationReport(findings=findings, title=PLAN_REPORT_TITLE)


# ------------------------------------------------------------------- P1 / P2


def rule_unique_ids(plan: AnalysisPlan) -> list[GraphFinding]:
    """P1 — node ids are unique. Duplicates make every lookup ambiguous."""
    seen: set[str] = set()
    findings: list[GraphFinding] = []
    for node in plan.nodes:
        if node.id in seen:
            findings.append(
                GraphFinding(
                    rule="P1-ids",
                    severity="error",
                    message=f"Duplicate node id '{node.id}'.",
                    node_id=node.id,
                )
            )
        seen.add(node.id)
    return findings


def rule_unique_outputs(plan: AnalysisPlan) -> list[GraphFinding]:
    """P2 — no two nodes write the same artifact, and none escapes the root.

    Two nodes sharing an artifact path would collapse to one graph node and
    silently overwrite each other's work.
    """
    findings: list[GraphFinding] = []
    by_path: dict[str, str] = {}
    for node in plan.nodes:
        problem = _unsafe_relative(node.directory)
        if problem:
            findings.append(
                GraphFinding(
                    rule="P2-outputs",
                    severity="error",
                    message=f"Node '{node.id}' directory '{node.directory}' {problem}.",
                    node_id=node.id,
                )
            )
        if not node.artifact or "/" in node.artifact:
            findings.append(
                GraphFinding(
                    rule="P2-outputs",
                    severity="error",
                    message=(
                        f"Node '{node.id}' artifact must be a bare filename, got '{node.artifact}'."
                    ),
                    node_id=node.id,
                )
            )
            continue
        path = node.artifact_path
        owner = by_path.get(path)
        if owner is not None:
            findings.append(
                GraphFinding(
                    rule="P2-outputs",
                    severity="error",
                    message=f"Nodes '{owner}' and '{node.id}' both write '{path}'.",
                    node_id=node.id,
                )
            )
        else:
            by_path[path] = node.id
    return findings


# ------------------------------------------------------------------- P3 / P4


def rule_edge_endpoints(plan: AnalysisPlan) -> list[GraphFinding]:
    """P3 — every edge connects two declared nodes, and no edge is duplicated."""
    known = set(plan.node_ids())
    findings: list[GraphFinding] = []
    seen: set[tuple[str, str, str]] = set()
    for edge in plan.edges:
        for role, endpoint in (("upstream", edge.upstream), ("downstream", edge.downstream)):
            if endpoint not in known:
                findings.append(
                    GraphFinding(
                        rule="P3-edges",
                        severity="error",
                        message=(
                            f"Edge {edge.upstream} -> {edge.downstream} names "
                            f"{role} '{endpoint}', which is not a node in this plan."
                        ),
                        node_id=endpoint,
                    )
                )
        if edge.key in seen:
            findings.append(
                GraphFinding(
                    rule="P3-edges",
                    severity="error",
                    message=(f"Duplicate {edge.kind} edge {edge.upstream} -> {edge.downstream}."),
                    node_id=edge.downstream,
                )
            )
        seen.add(edge.key)
    return findings


def rule_acyclic(plan: AnalysisPlan) -> list[GraphFinding]:
    """P4 — the blocking dependency graph is acyclic.

    A cycle is the one structural error that would hang the orchestrator rather
    than fail it: every node in the cycle waits forever for another.
    """
    cycle = _find_cycle(plan)
    if not cycle:
        return []
    return [
        GraphFinding(
            rule="P4-acyclic",
            severity="error",
            message="Dependency cycle: " + " -> ".join(cycle),
            node_id=cycle[0],
        )
    ]


def _find_cycle(plan: AnalysisPlan) -> list[str]:
    """Return one cycle in the `requires` graph as a node-id path, or []."""
    known = set(plan.node_ids())
    prerequisites = {
        node.id: [p for p in plan.prerequisites(node.id) if p in known] for node in plan.nodes
    }

    WHITE, GREY, BLACK = 0, 1, 2
    colour = dict.fromkeys(prerequisites, WHITE)
    stack: list[str] = []

    def visit(node_id: str) -> list[str]:
        colour[node_id] = GREY
        stack.append(node_id)
        for upstream in prerequisites.get(node_id, ()):
            if colour.get(upstream) == GREY:
                start = stack.index(upstream)
                return [*stack[start:], upstream]
            if colour.get(upstream) == WHITE:
                found = visit(upstream)
                if found:
                    return found
        stack.pop()
        colour[node_id] = BLACK
        return []

    for node_id in prerequisites:
        if colour[node_id] == WHITE:
            found = visit(node_id)
            if found:
                return found
    return []


# -------------------------------------------------------------------- P5 / P9


def rule_single_entry(plan: AnalysisPlan) -> list[GraphFinding]:
    """P5 — the plan has exactly one starting point.

    Several entry nodes are legal — parallel calibrations, say — but usually mean
    an edge was forgotten, so this is advisory rather than blocking.
    """
    if not plan.nodes:
        return [
            GraphFinding(
                rule="P5-entry",
                severity="error",
                message="Plan declares no nodes; there is nothing to run.",
            )
        ]
    entries = plan.entry_nodes()
    if not entries:
        # Every node has a prerequisite, which means they are all in a cycle.
        # P4 reports the cycle itself; this says what it costs.
        return [
            GraphFinding(
                rule="P5-entry",
                severity="error",
                message="No node can start: every node has an unmet prerequisite.",
            )
        ]
    if len(entries) > 1:
        names = ", ".join(sorted(n.id for n in entries))
        return [
            GraphFinding(
                rule="P5-entry",
                severity="warning",
                message=f"{len(entries)} nodes start with no prerequisite ({names}).",
            )
        ]
    return []


def rule_reachable(plan: AnalysisPlan) -> list[GraphFinding]:
    """P9 — every node is downstream of some entry node.

    An unreachable node is one whose only prerequisites sit in a cycle; it would
    never run. Advisory, because P4 already blocks on the cycle itself.
    """
    order_reachable: set[str] = set()
    frontier = [n.id for n in plan.entry_nodes()]
    order_reachable.update(frontier)
    while frontier:
        current = frontier.pop()
        for edge in plan.downstream_edges(current):
            if edge.downstream not in order_reachable:
                order_reachable.add(edge.downstream)
                frontier.append(edge.downstream)

    return [
        GraphFinding(
            rule="P9-reachable",
            severity="warning",
            message=f"Node '{node.id}' is not reachable from any starting node.",
            node_id=node.id,
        )
        for node in plan.nodes
        if node.id not in order_reachable
    ]


# -------------------------------------------------------------------- P6 / P7 / P8


def rule_known_vocabulary(
    plan: AnalysisPlan,
    known_reviewers: Collection[str] | None,
    known_roles: Collection[str],
) -> list[GraphFinding]:
    """P6 — roles, gates, reviewers, edge kinds and inject modes are recognised."""
    findings: list[GraphFinding] = []
    reviewers = set(known_reviewers) if known_reviewers is not None else None

    for node in plan.nodes:
        if node.role not in known_roles:
            findings.append(_unknown("P6-vocabulary", node.id, "role", node.role, known_roles))
        for gate in node.gates:
            if gate.name not in GATES:
                findings.append(_unknown("P6-vocabulary", node.id, "gate", gate.name, GATES))
        for path in node.context_paths:
            problem = _unsafe_relative(path)
            if problem:
                findings.append(
                    GraphFinding(
                        rule="P6-vocabulary",
                        severity="error",
                        message=f"Node '{node.id}' reads context path '{path}', which {problem}.",
                        node_id=node.id,
                    )
                )
        if node.max_iterations < 1:
            findings.append(
                GraphFinding(
                    rule="P6-vocabulary",
                    severity="error",
                    message=(
                        f"Node '{node.id}' max_iterations must be at least 1, "
                        f"got {node.max_iterations}."
                    ),
                    node_id=node.id,
                )
            )
        for reviewer in node.reviewers:
            if not reviewer.strip():
                findings.append(
                    GraphFinding(
                        rule="P6-vocabulary",
                        severity="error",
                        message=f"Node '{node.id}' declares an empty reviewer name.",
                        node_id=node.id,
                    )
                )
            elif reviewers is not None and reviewer not in reviewers:
                findings.append(
                    _unknown("P6-vocabulary", node.id, "reviewer", reviewer, sorted(reviewers))
                )
        if node.arbiter and not node.reviewers:
            findings.append(
                GraphFinding(
                    rule="P6-vocabulary",
                    severity="warning",
                    message=(
                        f"Node '{node.id}' runs an arbiter but declares no reviewers; "
                        f"there will be nothing to adjudicate."
                    ),
                    node_id=node.id,
                )
            )

    for edge in plan.edges:
        if edge.kind not in EDGE_KINDS:
            findings.append(
                _unknown("P6-vocabulary", edge.downstream, "edge kind", edge.kind, EDGE_KINDS)
            )
        if edge.inject not in INJECT_MODES:
            findings.append(
                _unknown("P6-vocabulary", edge.downstream, "inject mode", edge.inject, INJECT_MODES)
            )
    return findings


def rule_contracts(plan: AnalysisPlan) -> list[GraphFinding]:
    """P7 — graph write-back contracts name real graph node and edge types."""
    findings: list[GraphFinding] = []
    for node in plan.nodes:
        for node_type in node.contract.node_types:
            if node_type not in NODE_TYPES:
                findings.append(
                    _unknown("P7-contract", node.id, "graph node type", node_type, NODE_TYPES)
                )
        for edge_type in node.contract.edge_types:
            if edge_type not in EDGE_TYPES:
                findings.append(
                    _unknown("P7-contract", node.id, "graph edge type", edge_type, EDGE_TYPES)
                )
    return findings


def rule_note_artifacts(plan: AnalysisPlan) -> list[GraphFinding]:
    """P8 — a node that writes an analysis note produces markdown.

    The note writer emits markdown and the typesetter compiles it with pandoc;
    neither works on any other extension. What is checked is the *effective* note
    file, which is `note_artifact` when the note is a separate document and the
    primary artifact otherwise.
    """
    findings: list[GraphFinding] = []
    for node in plan.nodes:
        if node.note_artifact and "/" in node.note_artifact:
            findings.append(
                GraphFinding(
                    rule="P8-notes",
                    severity="error",
                    message=(
                        f"Node '{node.id}' note_artifact must be a bare filename, "
                        f"got '{node.note_artifact}'."
                    ),
                    node_id=node.id,
                )
            )
        if node.note_artifact and not node.produces_note:
            findings.append(
                GraphFinding(
                    rule="P8-notes",
                    severity="warning",
                    message=(
                        f"Node '{node.id}' names a note_artifact but does not set "
                        f"produces_note, so no note will be written."
                    ),
                    node_id=node.id,
                )
            )
        if node.produces_note and not node.note_path.lower().endswith(".md"):
            findings.append(
                GraphFinding(
                    rule="P8-notes",
                    severity="error",
                    message=(
                        f"Node '{node.id}' writes an analysis note to "
                        f"'{node.note_path}', which is not markdown."
                    ),
                    node_id=node.id,
                )
            )
    return findings


# ------------------------------------------------------------------- helpers


def _unknown(
    rule: str, node_id: str, kind: str, value: str, allowed: Collection[str]
) -> GraphFinding:
    return GraphFinding(
        rule=rule,
        severity="error",
        message=(
            f"Node '{node_id}' declares unknown {kind} '{value}'. "
            f"Valid: {', '.join(sorted(allowed))}."
        ),
        node_id=node_id,
    )


def _unsafe_relative(path: str) -> str:
    """Return why `path` is not a safe analysis-root-relative path, or ""."""
    if not path or not path.strip():
        return "is empty"
    candidate = PurePosixPath(path.replace("\\", "/"))
    if candidate.is_absolute():
        return "is an absolute path"
    if ".." in candidate.parts:
        return "escapes the analysis root"
    return ""
