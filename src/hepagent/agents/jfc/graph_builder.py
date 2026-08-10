"""Deterministic ingestion of a JFC analysis directory into its analysis graph.

Nothing here calls a model. Everything the builder writes is derived by parsing
files the pipeline already produces, reusing the pipeline's own parsers so the
graph cannot drift from what the agents actually read:

- the **analysis plan** (`plan.json`) defines the nodes and the dependency edges;
- `check_phase1_commitments` (commitment_checker) yields commitment nodes;
- `parse_verdict_from_adjudication` (review_gate) yields decision nodes.

The plan replaced the hardcoded `PHASE_SPECS` / `UPSTREAM_ARTIFACTS` tables, so
what a node produces and what it depends on is now authored rather than declared
in code — but the compiled graph is the same shape, and `plan.compile` is the one
place the plan's data-flow edges become the graph's dependency edges.

Agents enrich the graph on top of this through the write-back tools in
`hepagent.tools.jfc.graph`; ingestion runs afterwards and merges, never clobbers.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from hepagent.agents.jfc.commitment_checker import check_phase1_commitments
from hepagent.agents.jfc.review_gate import parse_verdict_from_adjudication
from hepagent.graph.schema import Edge, GraphSchemaError, Node, make_id
from hepagent.graph.store import AnalysisGraph
from hepagent.plan.compile import plan_to_graph
from hepagent.plan.schema import AnalysisPlan, PlanNode
from hepagent.plan.store import resolve_plan

BUILDER = "builder"

_FIGURE_SUFFIXES = (".png", ".pdf", ".jpg", ".jpeg", ".svg")


@dataclass
class IngestReport:
    """What one ingestion pass added to the graph.

    Args:
        phase: The plan node that was ingested, by id.
        nodes: Ids of nodes created or updated.
        edges: `(src, type, dst)` triples created.
        skipped: Human-readable notes about things that could not be linked.
    """

    phase: str = ""
    nodes: list[str] = field(default_factory=list)
    edges: list[tuple[str, str, str]] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)

    def merge(self, other: IngestReport) -> IngestReport:
        self.nodes.extend(other.nodes)
        self.edges.extend(other.edges)
        self.skipped.extend(other.skipped)
        return self

    def summary(self) -> str:
        return f"{len(self.nodes)} node(s), {len(self.edges)} edge(s)"


# ------------------------------------------------------------------ helpers


def commitment_owner(plan: AnalysisPlan) -> PlanNode | None:
    """Return the node that declares the analysis's commitments.

    That is the node whose write-back contract lets it create `commits_to`
    edges — Phase 1 in the shipped templates, but a plan is free to put it
    elsewhere. Falls back to the first starting node so a plan that never
    declared a contract still attaches its commitments somewhere sensible.
    """
    for node in plan.nodes:
        if "commits_to" in node.contract.edge_types:
            return node
    entries = plan.entry_nodes()
    return entries[0] if entries else None


def _add_node(graph: AnalysisGraph, report: IngestReport, node: Node) -> Node:
    graph.add_node(node)
    report.nodes.append(node.id)
    return node


def _add_edge(graph: AnalysisGraph, report: IngestReport, edge: Edge) -> bool:
    """Add an edge, recording rather than raising when it cannot be linked."""
    try:
        graph.add_edge(edge)
    except GraphSchemaError as exc:
        report.skipped.append(str(exc))
        return False
    report.edges.append((edge.src, edge.type, edge.dst))
    return True


# ---------------------------------------------------------------- bootstrap


def bootstrap_graph(analysis_root: Path | str, plan: AnalysisPlan | None = None) -> AnalysisGraph:
    """Seed the graph for an analysis from its plan.

    Writes the problem node, the analysis-root node, one *pending* artifact node
    per plan node, and the `requires` chain between them. Pending artifact nodes
    are the declaration: they carry the path a node will write without yet
    asserting the file exists, so validation treats them as plans rather than
    claims.

    The whole shape comes from `plan.compile.plan_to_graph`; this function only
    decides what to merge. A pending placeholder never overwrites an artifact
    that has already been ingested.

    Args:
        analysis_root: The analysis root directory.
        plan: The analysis plan. Read from `plan.json` when omitted.

    Returns:
        The populated `AnalysisGraph`.
    """
    root = Path(analysis_root)
    plan = resolve_plan(root, plan)
    graph = AnalysisGraph.load(root)
    graph.ensure_dir()
    report = IngestReport(phase="bootstrap")

    nodes, edges = plan_to_graph(plan)

    for node in nodes:
        # A pending artifact placeholder must never downgrade a real one.
        if node.status == "pending" and graph.has_node(node.id):
            continue
        _add_node(graph, report, node)

    for edge in edges:
        _add_edge(graph, report, edge)

    return graph


# ---------------------------------------------------------------- ingestion


def ingest_node(
    analysis_root: Path | str,
    node_id: str,
    plan: AnalysisPlan | None = None,
) -> IngestReport:
    """Ingest everything a completed plan node left on disk.

    Records the primary artifact and its lineage, figures, machine-readable
    results, analysis scripts, and the current state of every commitment.

    Args:
        analysis_root: The analysis root directory.
        node_id: Id of the plan node to ingest.
        plan: The analysis plan. Read from `plan.json` when omitted.
    """
    root = Path(analysis_root)
    report = IngestReport(phase=str(node_id))

    try:
        plan = resolve_plan(root, plan)
    except Exception as exc:  # noqa: BLE001 - reported, not raised into the orchestrator
        report.skipped.append(f"Could not read the analysis plan: {exc}")
        return report

    node = plan.node(str(node_id))
    if node is None:
        report.skipped.append(f"Unknown plan node '{node_id}' — nothing ingested")
        return report

    graph = AnalysisGraph.load(root)
    graph.ensure_dir()

    artifact_id = _ingest_artifact(graph, report, root, plan, node)
    _ingest_figures(graph, report, root, node, artifact_id)
    _ingest_results(graph, report, root, node, artifact_id)
    _ingest_scripts(graph, report, root, node, artifact_id)
    _ingest_commitments(graph, report, root, plan, node)
    return report


def _ingest_artifact(
    graph: AnalysisGraph,
    report: IngestReport,
    root: Path,
    plan: AnalysisPlan,
    node: PlanNode,
) -> str | None:
    """Record the node's primary artifact and its `derives_from` lineage."""
    key = node.id
    rel = node.artifact_path
    if not (root / rel).exists():
        report.skipped.append(f"Node {key} artifact not on disk yet ({rel})")
        return None

    artifact_id = make_id("artifact", rel)
    _add_node(
        graph,
        report,
        Node(
            id=artifact_id,
            type="artifact",
            label=Path(rel).name,
            content_ref=rel,
            status="active",
            phase=key,
            created_by=BUILDER,
        ),
    )

    for rel_upstream in _lineage_paths(plan, node):
        if not (root / rel_upstream).exists():
            continue
        upstream_id = make_id("artifact", rel_upstream)
        if not graph.has_node(upstream_id):
            _add_node(
                graph,
                report,
                Node(
                    id=upstream_id,
                    type="artifact",
                    label=Path(rel_upstream).name,
                    content_ref=rel_upstream,
                    created_by=BUILDER,
                ),
            )
        _add_edge(
            graph,
            report,
            Edge(
                src=artifact_id,
                dst=upstream_id,
                type="derives_from",
                evidence_ref=rel_upstream,
                phase=key,
                created_by=BUILDER,
            ),
        )
    return artifact_id


def _lineage_paths(plan: AnalysisPlan, node: PlanNode) -> list[str]:
    """Artifacts a node's output derives from: its upstreams plus its context files.

    Both count as lineage. Only `requires` edges order execution, but an
    `informs` upstream and an ambient context file still fed the work, and
    provenance should say so.
    """
    paths: list[str] = []
    for edge in plan.upstream_edges(node.id):
        upstream = plan.node(edge.upstream)
        if upstream is not None and upstream.artifact_path not in paths:
            paths.append(upstream.artifact_path)
    for rel in node.context_paths:
        if rel not in paths:
            paths.append(rel)
    return paths


def _ingest_figures(
    graph: AnalysisGraph,
    report: IngestReport,
    root: Path,
    node: PlanNode,
    artifact_id: str | None,
) -> None:
    """Record every figure the node produced, linked back to its artifact."""
    key, directory = node.id, node.directory
    figures_dir = root / directory / "outputs" / "figures"
    if not figures_dir.is_dir():
        return
    for figure in sorted(figures_dir.iterdir()):
        if not figure.is_file() or figure.suffix.lower() not in _FIGURE_SUFFIXES:
            continue
        rel = f"{directory}/outputs/figures/{figure.name}"
        figure_id = make_id("figure", rel)
        _add_node(
            graph,
            report,
            Node(
                id=figure_id,
                type="figure",
                label=figure.name,
                content_ref=rel,
                phase=key,
                created_by=BUILDER,
            ),
        )
        if artifact_id:
            _add_edge(
                graph,
                report,
                Edge(
                    src=figure_id,
                    dst=artifact_id,
                    type="derives_from",
                    phase=key,
                    created_by=BUILDER,
                ),
            )


def _ingest_results(
    graph: AnalysisGraph,
    report: IngestReport,
    root: Path,
    node: PlanNode,
    artifact_id: str | None,
) -> None:
    """Record machine-readable results as evidence supporting the artifact.

    `results/*.json` is the single source of truth for numbers quoted in the
    analysis note, so each file becomes an evidence node. Top-level keys are
    captured as metadata to make the evidence searchable without reopening it.
    """
    key, directory = node.id, node.directory
    outputs = root / directory / "outputs"
    candidates = sorted(outputs.glob("results/*.json")) + sorted(outputs.glob("*.json"))
    for result in candidates:
        if not result.is_file():
            continue
        rel = str(result.relative_to(root))
        evidence_id = make_id("evidence", rel)
        _add_node(
            graph,
            report,
            Node(
                id=evidence_id,
                type="evidence",
                label=result.name,
                content_ref=rel,
                metadata={"keys": _json_keys(result)},
                phase=key,
                created_by=BUILDER,
            ),
        )
        if artifact_id:
            _add_edge(
                graph,
                report,
                Edge(
                    src=evidence_id,
                    dst=artifact_id,
                    type="supports",
                    evidence_ref=rel,
                    phase=key,
                    created_by=BUILDER,
                ),
            )


def _ingest_scripts(
    graph: AnalysisGraph,
    report: IngestReport,
    root: Path,
    node: PlanNode,
    artifact_id: str | None,
) -> None:
    """Record the analysis code that produced the node's output."""
    key, directory = node.id, node.directory
    src_dir = root / directory / "src"
    if not src_dir.is_dir():
        return
    for script in sorted(src_dir.rglob("*.py")):
        rel = str(script.relative_to(root))
        execution_id = make_id("execution", rel)
        _add_node(
            graph,
            report,
            Node(
                id=execution_id,
                type="execution",
                label=script.name,
                content_ref=rel,
                phase=key,
                created_by=BUILDER,
            ),
        )
        if artifact_id:
            _add_edge(
                graph,
                report,
                Edge(
                    src=artifact_id,
                    dst=execution_id,
                    type="derives_from",
                    evidence_ref=rel,
                    phase=key,
                    created_by=BUILDER,
                ),
            )


def _ingest_commitments(
    graph: AnalysisGraph,
    report: IngestReport,
    root: Path,
    plan: AnalysisPlan,
    node: PlanNode,
) -> None:
    """Turn every COMMITMENTS.md row into a node, closed by an evidence edge.

    Reuses `check_phase1_commitments` so the graph and the commitment gate can
    never disagree about what a commitment's status is. Commitments belong to the
    node that declares them — the one whose contract allows `commits_to` — not to
    whichever node happens to be ingesting.
    """
    key = node.id
    result = check_phase1_commitments(root)
    owner = commitment_owner(plan)
    owner_key = owner.id if owner else key
    owner_id = make_id("artifact", owner.artifact_path) if owner else None

    for item in result.pending + result.resolved + result.downscoped:
        commitment_id = make_id("commitment", item.id)
        _add_node(
            graph,
            report,
            Node(
                id=commitment_id,
                type="commitment",
                label=f"{item.id} {item.text}".strip(),
                content_ref="COMMITMENTS.md",
                metadata={"phase_resolved": item.phase_resolved},
                status=item.status,
                phase=owner_key,
                created_by=BUILDER,
            ),
        )

        if owner_id and graph.has_node(owner_id):
            _add_edge(
                graph,
                report,
                Edge(
                    src=owner_id,
                    dst=commitment_id,
                    type="commits_to",
                    phase=owner_key,
                    created_by=BUILDER,
                ),
            )

        if item.status == "pending" or not item.evidence:
            continue

        evidence_id = make_id("evidence", f"commitment/{item.id}")
        _add_node(
            graph,
            report,
            Node(
                id=evidence_id,
                type="evidence",
                label=item.evidence,
                metadata={"commitment": item.id},
                phase=item.phase_resolved or key,
                created_by=BUILDER,
            ),
        )
        _add_edge(
            graph,
            report,
            Edge(
                src=commitment_id,
                dst=evidence_id,
                type="resolves" if item.status == "resolved" else "downscopes",
                evidence_ref=item.evidence,
                phase=item.phase_resolved or key,
                created_by=BUILDER,
            ),
        )


def ingest_review(
    analysis_root: Path | str,
    node_id: str,
    plan: AnalysisPlan | None = None,
) -> IngestReport:
    """Ingest the review round for a node: reviewer findings and the verdict.

    Reviewer documents become `review` nodes attached to the artifact by
    `reviewed_by`. `ADJUDICATION.md` becomes a `decision` node whose verdict
    drives the outgoing edge: PASS approves the artifact, ITERATE and ESCALATE
    invalidate it, and REGRESS(M) points at node M's artifact instead.

    Args:
        analysis_root: The analysis root directory.
        node_id: Id of the plan node whose review directory should be ingested.
        plan: The analysis plan. Read from `plan.json` when omitted.
    """
    root = Path(analysis_root)
    report = IngestReport(phase=str(node_id))

    try:
        plan = resolve_plan(root, plan)
    except Exception as exc:  # noqa: BLE001 - reported, not raised into the orchestrator
        report.skipped.append(f"Could not read the analysis plan: {exc}")
        return report

    node = plan.node(str(node_id))
    if node is None:
        report.skipped.append(f"Unknown plan node '{node_id}' — no review ingested")
        return report

    key, directory = node.id, node.directory
    graph = AnalysisGraph.load(root)
    graph.ensure_dir()

    review_dir = root / directory / "review"
    if not review_dir.is_dir():
        return report

    artifact_id: str | None = make_id("artifact", node.artifact_path)
    if not graph.has_node(artifact_id):
        artifact_id = None
        report.skipped.append(f"Node {key} artifact node missing — reviews left unattached")

    for review_file in sorted(review_dir.glob("*.md")):
        rel = f"{directory}/review/{review_file.name}"

        # The adjudication node is written once, by `_ingest_verdict`, so its
        # label can carry the verdict. Writing it here first would append a
        # second revision on every ingestion pass.
        if review_file.name.upper().startswith("ADJUDICATION"):
            _ingest_verdict(graph, report, plan, key, artifact_id, review_file, rel)
            continue

        node_id = make_id("review", rel)
        _add_node(
            graph,
            report,
            Node(
                id=node_id,
                type="review",
                label=review_file.name,
                content_ref=rel,
                phase=key,
                created_by=BUILDER,
            ),
        )
        if artifact_id:
            _add_edge(
                graph,
                report,
                Edge(
                    src=artifact_id,
                    dst=node_id,
                    type="reviewed_by",
                    evidence_ref=rel,
                    phase=key,
                    created_by=BUILDER,
                ),
            )

    return report


def _ingest_verdict(
    graph: AnalysisGraph,
    report: IngestReport,
    plan: AnalysisPlan,
    key: str,
    artifact_id: str | None,
    adjudication_path: Path,
    rel: str,
) -> None:
    """Record the adjudication as a decision node and the edge its verdict implies."""
    verdict, cat_a, _cat_b, regression_origin = parse_verdict_from_adjudication(adjudication_path)
    findings = "; ".join(cat_a[:3])
    decision_id = make_id("decision", rel)

    _add_node(
        graph,
        report,
        Node(
            id=decision_id,
            type="decision",
            label=f"{adjudication_path.name} — {verdict}",
            content_ref=rel,
            metadata={"verdict": verdict, "category_a": cat_a},
            phase=key,
            created_by=BUILDER,
        ),
    )

    if artifact_id is None:
        return  # decision recorded, but there is nothing to attach it to

    if verdict == "PASS":
        _add_edge(
            graph,
            report,
            Edge(
                src=artifact_id,
                dst=decision_id,
                type="approved_by",
                evidence_ref=rel,
                phase=key,
                created_by=BUILDER,
            ),
        )
        return

    # ITERATE, ESCALATE and REGRESS all reject the artifact under review.
    _add_edge(
        graph,
        report,
        Edge(
            src=decision_id,
            dst=artifact_id,
            type="invalidates",
            evidence_ref=findings or rel,
            phase=key,
            created_by=BUILDER,
        ),
    )

    if verdict != "REGRESS" or regression_origin is None:
        return

    # A regression additionally points at the earlier node that caused it.
    origin = plan.node(str(regression_origin))
    origin_id = make_id("artifact", origin.artifact_path) if origin else ""
    if not origin_id or not graph.has_node(origin_id):
        report.skipped.append(f"REGRESS({regression_origin}) target artifact is not in the graph")
        return
    _add_edge(
        graph,
        report,
        Edge(
            src=decision_id,
            dst=origin_id,
            type="regresses_to",
            evidence_ref=findings or rel,
            phase=key,
            created_by=BUILDER,
        ),
    )


def rebuild(analysis_root: Path | str, plan: AnalysisPlan | None = None) -> IngestReport:
    """Re-derive the deterministic graph for every plan node from files on disk.

    Safe to run repeatedly: node ids are content-addressed, so an unchanged
    directory produces an unchanged graph.

    Args:
        analysis_root: The analysis root directory.
        plan: The analysis plan. Read from `plan.json` when omitted.
    """
    root = Path(analysis_root)
    combined = IngestReport(phase="rebuild")

    try:
        plan = resolve_plan(root, plan)
    except Exception as exc:  # noqa: BLE001 - reported, not raised into the orchestrator
        combined.skipped.append(f"Could not read the analysis plan: {exc}")
        return combined

    for node in plan.nodes:
        combined.merge(ingest_node(root, node.id, plan))
        combined.merge(ingest_review(root, node.id, plan))
    return combined


def _json_keys(path: Path) -> list[str]:
    """Return the top-level keys of a JSON file, or [] if it is not a JSON object."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    return sorted(data)[:20] if isinstance(data, dict) else []
