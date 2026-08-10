"""Compile a plan into the seed of the provenance graph.

This is the join between the two documents. Everything downstream of it — the
planner's execution order, resumption, the validation rules — reads the graph,
not the plan, and therefore keeps working unchanged whatever shape the plan takes.

**The edge inversion happens here and nowhere else.** A plan edge reads in
data-flow order (``upstream`` produced it, ``downstream`` consumes it); a graph
``requires`` edge reads in dependency order and puts the dependent node in
``src``. So a plan edge *strategy → exploration* compiles to
``artifact:…/EXPLORATION.md --requires--> artifact:…/STRATEGY.md``.

The function is pure: it returns records and touches no disk. Callers decide how
to merge them, which is what lets `bootstrap_graph` keep its guarantee of never
overwriting a node that already exists.
"""

from __future__ import annotations

from pathlib import PurePosixPath

from hepagent.graph.schema import Edge, Node, make_id
from hepagent.plan.schema import AnalysisPlan, PlanNode

#: `created_by` stamped on everything derived from the plan.
COMPILER = "plan"


def problem_id(plan: AnalysisPlan) -> str:
    """Graph id of the problem node for this plan."""
    return make_id("problem", plan.name)


def root_id(plan: AnalysisPlan) -> str:
    """Graph id of the analysis-root node for this plan."""
    return make_id("analysis_root", plan.name)


def artifact_id(node: PlanNode) -> str:
    """Graph id of the artifact a plan node produces."""
    return make_id("artifact", node.artifact_path)


def plan_to_graph(plan: AnalysisPlan) -> tuple[list[Node], list[Edge]]:
    """Return the nodes and edges a plan implies, in a deterministic order.

    Produces the problem node, the analysis-root node, one **pending** artifact
    node per plan node, and the `requires` chain between them. Pending means
    "declared but not produced" — a plan, not a claim — which is exactly what the
    validation rules already exempt from the content and provenance checks.

    Nodes with no blocking prerequisite are wired to the problem node instead, so
    every artifact has a path back to the physics question.

    Args:
        plan: The plan to compile.

    Returns:
        `(nodes, edges)` ready to feed to `AnalysisGraph.add_node` /
        `add_edge`. Nodes are ordered so every edge endpoint is defined before
        the edge that uses it.
    """
    nodes: list[Node] = []
    edges: list[Edge] = []

    problem = problem_id(plan)
    nodes.append(
        Node(
            id=problem,
            type="problem",
            label=_first_line(plan.problem) or f"{plan.name} physics question",
            content_ref="prompt.md",
            metadata={"analysis_type": plan.analysis_type},
            created_by=COMPILER,
        )
    )

    root = root_id(plan)
    nodes.append(
        Node(
            id=root,
            type="analysis_root",
            label=plan.name,
            metadata={"analysis_type": plan.analysis_type, "template": plan.template},
            created_by=COMPILER,
        )
    )
    edges.append(Edge(src=root, dst=problem, type="requires", created_by=COMPILER))

    for plan_node in plan.nodes:
        nodes.append(
            Node(
                id=artifact_id(plan_node),
                type="artifact",
                label=PurePosixPath(plan_node.artifact_path).name,
                content_ref=plan_node.artifact_path,
                status="pending",
                phase=plan_node.id,
                created_by=COMPILER,
            )
        )

    known = plan.node_ids()
    for plan_node in plan.nodes:
        source = artifact_id(plan_node)
        prerequisites = [p for p in plan.prerequisites(plan_node.id) if p in known]
        if not prerequisites:
            edges.append(
                Edge(
                    src=source,
                    dst=problem,
                    type="requires",
                    phase=plan_node.id,
                    created_by=COMPILER,
                )
            )
            continue
        for upstream_id in prerequisites:
            upstream = plan.require_node(upstream_id)
            edges.append(
                Edge(
                    src=source,
                    dst=artifact_id(upstream),
                    type="requires",
                    phase=plan_node.id,
                    created_by=COMPILER,
                )
            )

    return nodes, edges


def execution_order(plan: AnalysisPlan) -> list[str]:
    """Return node ids in a runnable order, ties broken by declaration order.

    This is what the plan *says* should happen. The orchestrator does not read
    it — it re-derives readiness from the graph after every node so a regression
    can un-complete work — but it is what the editor renders, what
    ``plan show`` prints, and what pins the compiled topology in tests.

    A plan containing a cycle is reported by `validate_plan`; here the nodes
    caught in it are appended at the end in declaration order rather than
    dropped, so a caller always gets every node back.
    """
    remaining = list(plan.nodes)
    satisfied: set[str] = set()
    ordered: list[str] = []

    while remaining:
        ready = [n for n in remaining if all(p in satisfied for p in plan.prerequisites(n.id))]
        if not ready:
            ordered.extend(n.id for n in remaining)
            break
        for node in ready:
            ordered.append(node.id)
            satisfied.add(node.id)
        remaining = [n for n in remaining if n.id not in satisfied]

    return ordered


def _first_line(text: str, limit: int = 120) -> str:
    """First meaningful line of a markdown block, for use as a label."""
    for raw in (text or "").splitlines():
        line = raw.strip().lstrip("#").strip()
        if line:
            return line[:limit]
    return ""
