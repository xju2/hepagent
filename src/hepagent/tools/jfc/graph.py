"""Agent-facing write-back tools for the analysis graph.

Deterministic ingestion (`hepagent.agents.jfc.graph_builder`) records what the
filesystem already shows: which artifact exists, which figure sits in which
directory, what a commitment table says. It cannot record *meaning* — that this
dataset is the one the selection was tuned on, that this closure test is what
resolves commitment D1.

These tools let an executor write that meaning down, bounded by a **write-back
contract**: each node may create only the node and edge types that belong to it.
A call outside the contract is refused with an explanation rather than silently
widening what the node is allowed to assert.

The contract is authored, not hardcoded: it is `PlanNode.contract`, edited in the
plan alongside everything else about the node.
"""

from __future__ import annotations

import json
from collections.abc import Collection
from pathlib import Path

from agents import function_tool
from hepagent.graph.query import ancestors, describe, to_table, unresolved_commitments
from hepagent.graph.schema import Edge, GraphSchemaError, Node, make_id
from hepagent.graph.store import AnalysisGraph
from hepagent.plan.schema import PlanNode
from hepagent.tools.jfc._resolve import resolve_node


def contract_for(node: PlanNode) -> tuple[frozenset[str], frozenset[str]]:
    """Return the (node types, edge types) a plan node may create.

    A node that declares no contract may read the graph but not write to it.
    """
    return frozenset(node.contract.node_types), frozenset(node.contract.edge_types)


def contract_summary(node: PlanNode) -> str:
    """Render a node's contract for injection into an executor prompt."""
    node_types, edge_types = contract_for(node)
    if not node_types:
        return f"Node '{node.id}' has no graph write-back allowance."
    return (
        f"Node '{node.id}' may create these node types: {', '.join(sorted(node_types))}.\n"
        f"Node '{node.id}' may create these edge types: {', '.join(sorted(edge_types))}."
    )


def _contract_error(node_id: str, kind: str, requested: str, allowed: Collection[str]) -> str:
    if not allowed:
        return f"Error: node '{node_id}' has no graph write-back allowance."
    return (
        f"Error: node '{node_id}' may not create {kind} '{requested}'. "
        f"Allowed {kind}s for this node: {', '.join(sorted(allowed))}."
    )


@function_tool
async def graph_add_node(
    analysis_root: str,
    node_id: str,
    node_type: str,
    label: str,
    content_ref: str = "",
    metadata_json: str = "",
) -> str:
    """
    Record a durable analysis object in the analysis graph.

    Use this for things the directory layout cannot express on its own: a dataset
    with its AMI tag and campaign, a selection method, a piece of evidence that
    closes a commitment. Figures and artifacts already on disk are picked up
    automatically — you do not need to add them by hand.

    Returns the id of the created graph node, or an error string starting with
    "Error:".

    Args:
        analysis_root: Absolute path to the analysis root directory.
        node_id: The plan node you are working on, e.g. "strategy" or
            "selection_ee". This is the id from the analysis plan, not a graph
            node id.
        node_type: Graph node type; must be permitted for your plan node (see
            your write-back contract). One of: commitment, dataset, method,
            evidence, figure, artifact.
        label: Short human-readable name, e.g. "mc23a ttbar PowhegPythia8".
        content_ref: Optional path to the backing file, relative to the
            analysis root.
        metadata_json: Optional JSON object of structured detail. Prefer the
            documented ATLAS keys where they apply: ami_tag, campaign,
            derivation, lumi_fb, reco_tag, trigger, region, syst_source,
            np_name, generator.
    """
    plan_node, error = resolve_node(analysis_root, node_id)
    if plan_node is None:
        return error

    allowed_nodes, _ = contract_for(plan_node)
    if node_type not in allowed_nodes:
        return _contract_error(plan_node.id, "node type", node_type, allowed_nodes)

    metadata: dict = {}
    if metadata_json.strip():
        try:
            parsed = json.loads(metadata_json)
        except json.JSONDecodeError as exc:
            return f"Error: metadata_json is not valid JSON: {exc}"
        if not isinstance(parsed, dict):
            return "Error: metadata_json must be a JSON object."
        metadata = parsed

    graph = AnalysisGraph.load(Path(analysis_root))
    graph.ensure_dir()
    graph_node_id = make_id(node_type, content_ref or label)
    try:
        graph.add_node(
            Node(
                id=graph_node_id,
                type=node_type,
                label=label,
                content_ref=content_ref or None,
                metadata=metadata,
                phase=plan_node.id,
                created_by=f"{plan_node.id}_executor",
            )
        )
    except GraphSchemaError as exc:
        return f"Error: {exc}"
    return graph_node_id


@function_tool
async def graph_add_edge(
    analysis_root: str,
    node_id: str,
    src_id: str,
    edge_type: str,
    dst_id: str,
    evidence_ref: str = "",
) -> str:
    """
    Link two nodes in the analysis graph.

    Both nodes must already exist — add them first with graph_add_node, or use
    the id of a node the builder created (run graph_query to list them).

    Edge direction matters:
      A derives_from B  — A was produced from B
      A supports B      — evidence A backs claim B
      A resolves B      — commitment A is closed by evidence B
      A downscopes B    — commitment A was narrowed, justified by B
      A commits_to B    — artifact A promises commitment B

    Returns a confirmation string, or an error starting with "Error:".

    Args:
        analysis_root: Absolute path to the analysis root directory.
        node_id: The plan node you are working on, e.g. "strategy". This is the
            id from the analysis plan, not a graph node id.
        src_id: Source graph node id, e.g. "commitment:D1".
        edge_type: Edge type; must be permitted for your plan node.
        dst_id: Target graph node id.
        evidence_ref: Path or short locator backing this link. Required for
            downscopes edges — a narrowed commitment must say why.
    """
    plan_node, error = resolve_node(analysis_root, node_id)
    if plan_node is None:
        return error

    _, allowed_edges = contract_for(plan_node)
    if edge_type not in allowed_edges:
        return _contract_error(plan_node.id, "edge type", edge_type, allowed_edges)

    if edge_type == "downscopes" and not evidence_ref.strip():
        return (
            "Error: a downscopes edge requires evidence_ref — record what was "
            "attempted and why the commitment could not be met in full."
        )

    graph = AnalysisGraph.load(Path(analysis_root))
    graph.ensure_dir()
    try:
        graph.add_edge(
            Edge(
                src=src_id,
                dst=dst_id,
                type=edge_type,
                evidence_ref=evidence_ref or None,
                phase=plan_node.id,
                created_by=f"{plan_node.id}_executor",
            )
        )
    except GraphSchemaError as exc:
        return f"Error: {exc}"
    return f"Linked {src_id} --{edge_type}--> {dst_id}"


@function_tool
async def graph_query(analysis_root: str, question: str, target: str = "") -> str:
    """
    Read the analysis graph.

    Returns a text answer, or an error string starting with "Error:".

    Args:
        analysis_root: Absolute path to the analysis root directory.
        question: One of:
            "nodes"       — list every node (filter by type with `target`)
            "provenance"  — what produced `target` and its full lineage
            "commitments" — commitments with no closing evidence yet
            "summary"     — node counts by type
        target: Node id, file path, or node type, depending on the question.
    """
    root = Path(analysis_root)
    if not root.is_dir():
        return f"Error: analysis root not found: {analysis_root}"

    graph = AnalysisGraph.load(root)
    if len(graph) == 0:
        return "The analysis graph is empty."

    kind = question.strip().lower()

    if kind == "nodes":
        return to_table(graph, node_type=target or None)

    if kind == "summary":
        counts = graph.summary()
        edges = counts.pop("_edges", 0)
        body = "\n".join(f"  {name:<14} {count}" for name, count in sorted(counts.items()))
        return f"Analysis graph:\n{body}\n  {'edges':<14} {edges}"

    if kind == "commitments":
        open_items = unresolved_commitments(graph)
        if not open_items:
            return "Every commitment has closing evidence."
        lines = ["Commitments with no resolves/downscopes edge:"]
        lines += [f"  {n.id}  {n.label}" for n in open_items]
        return "\n".join(lines)

    if kind == "provenance":
        if not target:
            return "Error: provenance requires a target node id or file path."
        node = graph.get_node(target)
        if node is None:
            matches = graph.find_by_content_ref(target)
            node = matches[0] if matches else None
        if node is None:
            return f"No node found for '{target}'."
        lineage = ancestors(graph, node.id)
        return describe(graph, node) + (
            "" if lineage else "\n  (no lineage recorded for this node yet)"
        )

    return (
        f"Error: unknown question '{question}'. "
        f"Valid questions: nodes, provenance, commitments, summary."
    )
