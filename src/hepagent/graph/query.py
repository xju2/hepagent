"""Traversal helpers over an `AnalysisGraph`.

These answer the questions the graph exists to make answerable without reading
prompt history: what produced this plot, what evidence backs this claim, what did
a reviewer reject, which commitments are still open, and what is ready to run next.
"""

from __future__ import annotations

from pathlib import Path

from hepagent.graph.schema import Node
from hepagent.graph.store import AnalysisGraph

# Node types whose existence is defined by a file on disk.
_FILE_BACKED: frozenset[str] = frozenset({"artifact", "figure", "evidence", "review", "decision"})


def ancestors(graph: AnalysisGraph, node_id: str, max_depth: int = 16) -> list[Node]:
    """Return the lineage of `node_id`, nearest first.

    Follows `derives_from` outward transitively: ``A derives_from B`` means A was
    produced from B, so B is an ancestor of A.
    """
    return _walk(graph, node_id, edge_type="derives_from", reverse=False, max_depth=max_depth)


def descendants(graph: AnalysisGraph, node_id: str, max_depth: int = 16) -> list[Node]:
    """Return everything transitively derived from `node_id`, nearest first."""
    return _walk(graph, node_id, edge_type="derives_from", reverse=True, max_depth=max_depth)


def evidence_for(graph: AnalysisGraph, node_id: str) -> list[Node]:
    """Return nodes that `supports` the given node."""
    return _resolve(graph, (e.src for e in graph.in_edges(node_id, type="supports")))


def rejections(graph: AnalysisGraph, node_id: str) -> list[Node]:
    """Return reviews or decisions that `invalidates` the given node."""
    return _resolve(graph, (e.src for e in graph.in_edges(node_id, type="invalidates")))


def reviews_of(graph: AnalysisGraph, node_id: str) -> list[Node]:
    """Return review and decision nodes attached to the given node."""
    ids = [e.dst for e in graph.out_edges(node_id, type="reviewed_by")]
    ids += [e.dst for e in graph.out_edges(node_id, type="approved_by")]
    return _resolve(graph, ids)


def what_produced(graph: AnalysisGraph, content_ref: str) -> tuple[Node | None, list[Node]]:
    """Answer "what produced this file?".

    Returns the node backing `content_ref` (or None) together with its lineage,
    nearest ancestor first.

    Args:
        graph: The loaded analysis graph.
        content_ref: A path — absolute, analysis-root-relative, or a bare
            filename. Matching falls back to suffix comparison.
    """
    matches = graph.find_by_content_ref(content_ref)
    if not matches:
        return None, []
    node = matches[0]
    return node, ancestors(graph, node.id)


def unresolved_commitments(graph: AnalysisGraph) -> list[Node]:
    """Return commitment nodes with no `resolves` or `downscopes` edge.

    A commitment already marked resolved or downscoped on the node itself is
    still reported when it carries no closing edge — the edge is what records
    *which* evidence closed it.
    """
    open_commitments: list[Node] = []
    for node in graph.nodes(type="commitment"):
        closed = graph.out_edges(node.id, type="resolves") or graph.out_edges(
            node.id, type="downscopes"
        )
        if not closed:
            open_commitments.append(node)
    return open_commitments


def orphan_claims(graph: AnalysisGraph) -> list[Node]:
    """Return artifacts and figures with no recorded lineage.

    These are the nodes whose provenance path is broken: a plot nobody can trace
    to the artifact that made it, or an artifact with no upstream.
    """
    orphans: list[Node] = []
    for node_type in ("artifact", "figure"):
        for node in graph.nodes(type=node_type):
            if not graph.out_edges(node.id, type="derives_from"):
                orphans.append(node)
    return sorted(orphans, key=lambda n: n.id)


def is_realized(graph: AnalysisGraph, node: Node) -> bool:
    """True when the node's backing content actually exists.

    File-backed node types need their `content_ref` present on disk; everything
    else counts as realized once its status is not `pending`.
    """
    if node.type in _FILE_BACKED:
        if not node.content_ref:
            return False
        return (graph.root / node.content_ref).exists()
    return node.status != "pending"


def frontier(graph: AnalysisGraph) -> list[Node]:
    """Return unrealized nodes whose `requires` prerequisites are all realized.

    This is the set the orchestrator could expand next. It is computed but not
    yet wired into phase selection — `PHASE_ORDER` still drives execution.
    """
    ready: list[Node] = []
    for node in graph.nodes():
        if is_realized(graph, node):
            continue
        prereq_ids = [e.dst for e in graph.out_edges(node.id, type="requires")]
        prereqs = [graph.get_node(pid) for pid in prereq_ids]
        if all(p is not None and is_realized(graph, p) for p in prereqs):
            ready.append(node)
    return sorted(ready, key=lambda n: n.id)


def to_mermaid(graph: AnalysisGraph, node_type: str | None = None, max_nodes: int = 120) -> str:
    """Render the graph as a Mermaid `graph LR` block.

    Args:
        graph: The loaded analysis graph.
        node_type: Restrict to a single node type when given.
        max_nodes: Stop after this many nodes; the caller is told what was cut.
    """
    nodes = graph.nodes(type=node_type)
    truncated = len(nodes) > max_nodes
    nodes = nodes[:max_nodes]
    included = {n.id for n in nodes}

    lines = ["graph LR"]
    for node in nodes:
        label = f"{node.type}: {node.label}".replace('"', "'")
        lines.append(f'    {_mermaid_id(node.id)}["{label}"]')
    for edge in graph.edges():
        if edge.src in included and edge.dst in included:
            lines.append(f"    {_mermaid_id(edge.src)} -->|{edge.type}| {_mermaid_id(edge.dst)}")
    if truncated:
        lines.append(f"    %% truncated at {max_nodes} nodes of {len(graph)}")
    return "\n".join(lines)


def to_table(graph: AnalysisGraph, node_type: str | None = None) -> str:
    """Render nodes as a fixed-width text table for terminal output."""
    nodes = graph.nodes(type=node_type)
    if not nodes:
        return "(graph is empty)"
    lines = [f"{'TYPE':<14} {'PHASE':<6} {'STATUS':<11} {'LABEL':<38} CONTENT"]
    lines.append("-" * 100)
    for node in nodes:
        lines.append(
            f"{node.type:<14} {(node.phase or '-'):<6} {node.status:<11} "
            f"{node.label[:38]:<38} {node.content_ref or '-'}"
        )
    return "\n".join(lines)


def describe(graph: AnalysisGraph, node: Node) -> str:
    """Render one node with its lineage, evidence and review history."""
    lines = [
        f"{node.id}",
        f"  type      : {node.type}",
        f"  label     : {node.label}",
        f"  phase     : {node.phase or '-'}",
        f"  status    : {node.status}",
        f"  content   : {node.content_ref or '-'}",
        f"  created_by: {node.created_by} at {node.created_at}",
    ]
    if node.metadata:
        lines.append(f"  metadata  : {node.metadata}")

    lineage = ancestors(graph, node.id)
    lines.append("\n  derives from:")
    if lineage:
        lines.extend(f"    - {a.type}: {a.label}" for a in lineage)
    else:
        lines.append("    (nothing recorded)")

    supporting = evidence_for(graph, node.id)
    if supporting:
        lines.append("\n  supported by:")
        lines.extend(f"    - {s.type}: {s.label}" for s in supporting)

    rejected_by = rejections(graph, node.id)
    if rejected_by:
        lines.append("\n  invalidated by:")
        lines.extend(f"    - {r.type}: {r.label}" for r in rejected_by)

    return "\n".join(lines)


def _walk(
    graph: AnalysisGraph,
    node_id: str,
    edge_type: str,
    reverse: bool,
    max_depth: int,
) -> list[Node]:
    """Breadth-first traversal along one edge type, returning nodes nearest first."""
    seen: set[str] = {node_id}
    ordered: list[Node] = []
    frontier_ids = [node_id]
    for _ in range(max_depth):
        next_ids: list[str] = []
        for current in frontier_ids:
            edges = (
                graph.in_edges(current, type=edge_type)
                if reverse
                else graph.out_edges(current, type=edge_type)
            )
            for edge in edges:
                neighbour = edge.src if reverse else edge.dst
                if neighbour in seen:
                    continue
                seen.add(neighbour)
                node = graph.get_node(neighbour)
                if node is not None:
                    ordered.append(node)
                    next_ids.append(neighbour)
        if not next_ids:
            break
        frontier_ids = next_ids
    return ordered


def _resolve(graph: AnalysisGraph, ids) -> list[Node]:
    nodes = [graph.get_node(i) for i in ids]
    return sorted((n for n in nodes if n is not None), key=lambda n: n.id)


def _mermaid_id(node_id: str) -> str:
    """Mermaid node ids may not contain ':' or '/'."""
    return node_id.replace(":", "__").replace("/", "_").replace(".", "_").replace("-", "_")


def relative_to_root(root: Path, path: Path | str) -> str:
    """Return `path` expressed relative to `root` when possible, else unchanged."""
    candidate = Path(path)
    try:
        return str(candidate.resolve().relative_to(Path(root).resolve()))
    except (ValueError, OSError):
        return str(candidate)
