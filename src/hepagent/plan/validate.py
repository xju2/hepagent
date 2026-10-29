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
from dataclasses import dataclass
from pathlib import PurePosixPath

from hepagent.graph.schema import EDGE_TYPES, NODE_TYPES
from hepagent.graph.validation import GraphFinding, GraphValidationReport
from hepagent.plan.schema import (
    BRANCH_KINDS,
    COMPARISONS,
    EDGE_KINDS,
    EXHAUSTION,
    GATES,
    INJECT_MODES,
    ROLES,
    AnalysisPlan,
    PlanNode,
)

PLAN_REPORT_TITLE = "Plan validation"


@dataclass(frozen=True)
class PlanVocabulary:
    """The names the runtime can actually build, for the rules to check against.

    This module stays domain-agnostic: it never imports the JFC reviewer
    registry, the function-tool list or the skill directory. The caller that
    knows about them fills this in — see `hepagent.agents.jfc.capabilities`.

    Every field is independently optional, and ``None`` means *do not check*
    rather than *nothing is valid*: a caller that only knows the reviewers must
    not turn every tool name in the plan into a blocking finding. Names are
    still checked for being non-empty either way.

    Args:
        reviewers: Reviewer factory names.
        tools: Function-tool names a node may allowlist.
        skills: Skill names that exist on disk.
        mcp_servers: MCP server names this installation has configured.
        platforms: Model providers this installation has configured. Only the
            provider half of a node's ``"provider:model"`` override is checked —
            which models a provider serves is a network question, and a plan
            must stay validatable offline.
    """

    reviewers: Collection[str] | None = None
    tools: Collection[str] | None = None
    skills: Collection[str] | None = None
    mcp_servers: Collection[str] | None = None
    platforms: Collection[str] | None = None


def validate_plan(
    plan: AnalysisPlan,
    *,
    vocabulary: PlanVocabulary | None = None,
    known_roles: Collection[str] = ROLES,
) -> GraphValidationReport:
    """Run every rule over `plan`.

    Args:
        plan: The plan to check.
        vocabulary: Names the runtime can build, see `PlanVocabulary`. Omitted,
            every catalog is unchecked.
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
    findings += rule_known_vocabulary(plan, vocabulary or PlanVocabulary(), known_roles)
    findings += rule_contracts(plan)
    findings += rule_note_artifacts(plan)
    findings += rule_reachable(plan)
    findings += rule_conditions(plan)
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

    An *unbounded* cycle is the one structural error that would hang the
    orchestrator rather than fail it: every node in the cycle waits forever for
    another.

    A loop authored through a condition node is not such a cycle. This walks
    `plan.prerequisites`, which counts `requires` edges and forward branches but
    not back branches, so the subgraph checked here is exactly the one that has
    to be acyclic. Termination of the loops themselves is P10's job — a back
    branch is legal only where a bounded condition node emits it.
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


def _named_set_findings(
    node: PlanNode,
    kind: str,
    declared: Collection[str],
    known: Collection[str] | None,
) -> list[GraphFinding]:
    """P6 over one of a node's name lists: tools, skills, MCP servers.

    An empty name is always wrong; an unrecognised one only when the caller
    supplied a catalog to recognise it against.
    """
    findings: list[GraphFinding] = []
    catalog = set(known) if known is not None else None
    for name in declared:
        if not name.strip():
            findings.append(
                GraphFinding(
                    rule="P6-vocabulary",
                    severity="error",
                    message=f"Node '{node.id}' declares an empty {kind} name.",
                    node_id=node.id,
                )
            )
        elif catalog is not None and name not in catalog:
            findings.append(_unknown("P6-vocabulary", node.id, kind, name, sorted(catalog)))
    return findings


def _model_findings(node: PlanNode, platforms: Collection[str] | None) -> list[GraphFinding]:
    """P6 over a node's model override.

    ``None`` means "use the run's model" and is the common case. A spec that is
    present carries a platform only when it has a colon in it: a bare name is a
    model on whatever platform the run chose, which is the CLI's own reading.
    """
    spec = node.model
    if spec is None:
        return []
    if not spec.strip():
        return [
            GraphFinding(
                rule="P6-vocabulary",
                severity="error",
                message=(
                    f"Node '{node.id}' declares an empty model override; "
                    f"use null to inherit the run's model."
                ),
                node_id=node.id,
            )
        ]
    platform, sep, _ = spec.partition(":")
    if not sep:
        return []
    if not platform:
        return [
            GraphFinding(
                rule="P6-vocabulary",
                severity="error",
                message=f"Node '{node.id}' model override '{spec}' names no platform.",
                node_id=node.id,
            )
        ]
    if platforms is not None and platform not in platforms:
        return [_unknown("P6-vocabulary", node.id, "model platform", platform, sorted(platforms))]
    return []


def rule_known_vocabulary(
    plan: AnalysisPlan,
    vocabulary: PlanVocabulary,
    known_roles: Collection[str],
) -> list[GraphFinding]:
    """P6 — roles, gates, reviewers, capabilities, edge kinds and injects are known."""
    findings: list[GraphFinding] = []
    reviewers = set(vocabulary.reviewers) if vocabulary.reviewers is not None else None

    for node in plan.nodes:
        findings += _named_set_findings(node, "function tool", node.tools or (), vocabulary.tools)
        findings += _named_set_findings(node, "skill", node.skills, vocabulary.skills)
        findings += _named_set_findings(
            node, "MCP server", node.mcp_servers, vocabulary.mcp_servers
        )
        findings += _model_findings(node, vocabulary.platforms)
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


# ------------------------------------------------------------------- P10


#: A bound above this is legal but almost certainly not what the author meant —
#: every iteration re-runs the whole loop body against a model.
_LOUD_ITERATION_BOUND = 20


def rule_conditions(plan: AnalysisPlan) -> list[GraphFinding]:
    """P10 — condition nodes are well formed, and every loop is bounded.

    This is what makes a cycle safe to allow. Only a condition node may emit a
    branch, only a branch may point backwards, and a condition node that points
    backwards has a finite iteration budget — so "the plan terminates" is a
    per-node check rather than a property nobody can verify.
    """
    findings: list[GraphFinding] = []
    conditions = {node.id for node in plan.nodes if node.kind == "condition"}
    back = plan.back_branch_keys()

    for node in plan.nodes:
        findings += _condition_node_findings(plan, node, back)

    for edge in plan.edges:
        if edge.kind in BRANCH_KINDS and edge.upstream not in conditions:
            findings.append(
                GraphFinding(
                    rule="P10-conditions",
                    severity="error",
                    message=(
                        f"Edge {edge.upstream} --{edge.kind}--> {edge.downstream} leaves "
                        f"'{edge.upstream}', which is not a condition node. Only a node with "
                        f"kind 'condition' may branch."
                    ),
                    node_id=edge.upstream,
                )
            )

    findings += _loop_budget_findings(plan, back)
    return findings


def _condition_node_findings(
    plan: AnalysisPlan, node: PlanNode, back: Collection[tuple[str, str, str]]
) -> list[GraphFinding]:
    """Everything P10 has to say about one node."""
    findings: list[GraphFinding] = []
    is_condition = node.kind == "condition"

    if node.condition is not None and not is_condition:
        findings.append(
            GraphFinding(
                rule="P10-conditions",
                severity="error",
                message=(
                    f"Node '{node.id}' declares a condition but its kind is "
                    f"'{node.kind}'; nothing would ever evaluate it."
                ),
                node_id=node.id,
            )
        )
    if not is_condition:
        return findings

    if node.condition is None:
        findings.append(
            GraphFinding(
                rule="P10-conditions",
                severity="error",
                message=f"Condition node '{node.id}' declares no condition to evaluate.",
                node_id=node.id,
            )
        )

    branches = plan.branch_edges(node.id)
    if not branches:
        findings.append(
            GraphFinding(
                rule="P10-conditions",
                severity="error",
                message=(
                    f"Condition node '{node.id}' has no on_true or on_false edge, "
                    f"so there is nowhere for it to route."
                ),
                node_id=node.id,
            )
        )
    elif len({edge.kind for edge in branches}) == 1:
        only = branches[0].kind
        findings.append(
            GraphFinding(
                rule="P10-conditions",
                severity="warning",
                message=(
                    f"Condition node '{node.id}' declares only an {only} branch; "
                    f"the other outcome ends that path of the analysis."
                ),
                node_id=node.id,
            )
        )

    blocking_successors = [e.downstream for e in plan.downstream_edges(node.id, kind="requires")]
    if blocking_successors:
        findings.append(
            GraphFinding(
                rule="P10-conditions",
                severity="error",
                message=(
                    f"Condition node '{node.id}' feeds "
                    f"{', '.join(sorted(blocking_successors))} with a 'requires' edge. "
                    f"A condition's successors are its branches — that node would run "
                    f"whichever way the condition went."
                ),
                node_id=node.id,
            )
        )

    findings += _condition_block_findings(node, back)
    return findings


def _condition_block_findings(
    node: PlanNode, back: Collection[tuple[str, str, str]]
) -> list[GraphFinding]:
    """Check the condition's own fields: budget, vocabulary, metric source."""
    condition = node.condition
    if condition is None:
        return []

    findings: list[GraphFinding] = []
    loops = any(key[0] == node.id for key in back)

    if condition.max_iterations < 1:
        findings.append(
            GraphFinding(
                rule="P10-conditions",
                severity="error",
                message=(
                    f"Condition node '{node.id}' has max_iterations "
                    f"{condition.max_iterations}; a loop needs a budget of at least 1."
                ),
                node_id=node.id,
            )
        )
    elif loops and condition.max_iterations > _LOUD_ITERATION_BOUND:
        findings.append(
            GraphFinding(
                rule="P10-conditions",
                severity="warning",
                message=(
                    f"Condition node '{node.id}' may loop {condition.max_iterations} times; "
                    f"each pass re-runs the whole loop body."
                ),
                node_id=node.id,
            )
        )

    if condition.on_exhaustion not in EXHAUSTION:
        findings.append(
            _unknown(
                "P10-conditions", node.id, "exhaustion action", condition.on_exhaustion, EXHAUSTION
            )
        )

    if loops and not condition.metric and not condition.question.strip():
        findings.append(
            GraphFinding(
                rule="P10-conditions",
                severity="warning",
                message=(
                    f"Condition node '{node.id}' declares neither a metric nor a question, "
                    f"so its loop always runs to its {condition.max_iterations}-iteration budget."
                ),
                node_id=node.id,
            )
        )

    metric = condition.metric
    if metric is not None:
        if metric.compare not in COMPARISONS:
            findings.append(
                _unknown("P10-conditions", node.id, "comparison", metric.compare, COMPARISONS)
            )
        problem = _unsafe_relative(metric.source)
        if problem:
            findings.append(
                GraphFinding(
                    rule="P10-conditions",
                    severity="error",
                    message=(
                        f"Condition node '{node.id}' reads its metric from "
                        f"'{metric.source}', which {problem}."
                    ),
                    node_id=node.id,
                )
            )

    if node.reviewers or node.arbiter or node.produces_note:
        findings.append(
            GraphFinding(
                rule="P10-conditions",
                severity="warning",
                message=(
                    f"Condition node '{node.id}' declares reviewers, an arbiter or a note. "
                    f"A condition is evaluated, not executed and reviewed, so none of them run."
                ),
                node_id=node.id,
            )
        )
    if node.tools is not None or node.skills or node.mcp_servers:
        findings.append(
            GraphFinding(
                rule="P10-conditions",
                severity="warning",
                message=(
                    f"Condition node '{node.id}' declares tools, skills or MCP servers. "
                    f"A condition evaluates a test rather than running an executor, "
                    f"so none of them are attached."
                ),
                node_id=node.id,
            )
        )
    return findings


def _loop_budget_findings(
    plan: AnalysisPlan, back: Collection[tuple[str, str, str]]
) -> list[GraphFinding]:
    """Warn when nested loops multiply into a body-execution count nobody chose.

    Two loops in sequence cost the sum of their budgets; two nested loops cost
    the product. The second is easy to author by accident and expensive to
    discover at runtime.
    """
    if len(back) < 2:
        return []

    bounds: dict[str, int] = {}
    bodies: dict[str, set[str]] = {}
    for condition_id, target, _kind in back:
        node = plan.node(condition_id)
        if node is None or node.condition is None:
            continue
        bounds[condition_id] = max(node.condition.max_iterations, 1)
        bodies.setdefault(condition_id, set()).update(_loop_body(plan, target, condition_id))

    findings: list[GraphFinding] = []
    for outer, outer_body in bodies.items():
        nested = [inner for inner, body in bodies.items() if inner != outer and body < outer_body]
        if not nested:
            continue
        worst = bounds.get(outer, 1)
        for inner in nested:
            worst *= bounds.get(inner, 1)
        findings.append(
            GraphFinding(
                rule="P10-conditions",
                severity="warning",
                message=(
                    f"Loop '{outer}' encloses {', '.join(sorted(nested))}; in the worst case "
                    f"its body runs {worst} times."
                ),
                node_id=outer,
            )
        )
    return findings


def _loop_body(plan: AnalysisPlan, head: str, condition_id: str) -> set[str]:
    """Nodes on a blocking path from a loop's head to the condition that closes it."""
    forward = _reachable(plan, head, downstream=True)
    backward = _reachable(plan, condition_id, downstream=False)
    return (forward & backward) | {head, condition_id}


def _reachable(plan: AnalysisPlan, start: str, *, downstream: bool) -> set[str]:
    """Everything reachable from `start` along blocking edges, including itself."""
    seen = {start}
    frontier = [start]
    while frontier:
        current = frontier.pop()
        neighbours = (
            [n.id for n in plan.nodes if current in plan.prerequisites(n.id)]
            if downstream
            else plan.prerequisites(current)
        )
        for node_id in neighbours:
            if node_id not in seen:
                seen.add(node_id)
                frontier.append(node_id)
    return seen


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
