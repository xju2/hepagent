"""Deterministic ingestion of a JFC analysis directory into its analysis graph.

Nothing here calls a model. Everything the builder writes is derived by parsing
files the pipeline already produces, reusing the pipeline's own parsers so the
graph cannot drift from what the agents actually read:

- `UPSTREAM_ARTIFACTS` / `PHASE_SPECS` (executor) define the dependency edges.
- `check_phase1_commitments` (commitment_checker) yields commitment nodes.
- `parse_verdict_from_adjudication` (review_gate) yields decision nodes.

Agents enrich the graph on top of this through the write-back tools in
`hepagent.tools.jfc.graph`; ingestion runs afterwards and merges, never clobbers.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from hepagent.agents.jfc.commitment_checker import check_phase1_commitments
from hepagent.agents.jfc.executor import PHASE_SPECS, UPSTREAM_ARTIFACTS
from hepagent.agents.jfc.review_gate import parse_verdict_from_adjudication
from hepagent.graph.schema import Edge, GraphSchemaError, Node, make_id
from hepagent.graph.store import AnalysisGraph

BUILDER = "builder"

# Phase execution order, mirrored from the orchestrator. Imported lazily in
# `rebuild` to avoid a circular import (the orchestrator imports this module).
_PHASE_ORDER: tuple[int | str, ...] = (1, 2, 3, "4a", "4b", "4c", 5)

_FIGURE_SUFFIXES = (".png", ".pdf", ".jpg", ".jpeg", ".svg")


@dataclass
class IngestReport:
    """What one ingestion pass added to the graph.

    Args:
        phase: The phase that was ingested, as a string key.
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


def phase_key(phase: int | str) -> str:
    """Normalise a phase identifier to its string key ("1", "4a", ...)."""
    return str(phase)


def phase_dir(phase: int | str) -> str:
    """Return the directory name for a phase, or "" if the phase is unknown."""
    spec = PHASE_SPECS.get(phase) or PHASE_SPECS.get(_coerce_phase(phase))
    return spec[0] if spec else ""


def artifact_rel_path(phase: int | str) -> str:
    """Return the phase's primary artifact path, relative to the analysis root."""
    spec = PHASE_SPECS.get(phase) or PHASE_SPECS.get(_coerce_phase(phase))
    if not spec:
        return ""
    directory, _template, artifact = spec
    return f"{directory}/outputs/{artifact}"


def _coerce_phase(phase: int | str) -> int | str:
    """Map "1" -> 1 so string and int phase keys both hit `PHASE_SPECS`."""
    try:
        return int(phase)
    except (TypeError, ValueError):
        return str(phase)


def _upstream_for(phase: int | str) -> list[str]:
    return UPSTREAM_ARTIFACTS.get(phase) or UPSTREAM_ARTIFACTS.get(_coerce_phase(phase)) or []


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


def bootstrap_graph(
    analysis_root: Path | str,
    analysis_name: str,
    analysis_type: str,
    physics_prompt: str = "",
) -> AnalysisGraph:
    """Create the initial graph for a freshly scaffolded analysis.

    Writes the problem node, the analysis root node, one *pending* artifact node
    per phase, and the `requires` chain between them. Pending artifact nodes are
    the plan: they carry the path the phase will write but do not yet assert that
    the file exists, so validation treats them as declarations rather than claims.

    Args:
        analysis_root: The analysis root directory.
        analysis_name: Short analysis identifier.
        analysis_type: "measurement" or "search".
        physics_prompt: The physics question, stored as node metadata.

    Returns:
        The populated `AnalysisGraph`.
    """
    root = Path(analysis_root)
    graph = AnalysisGraph.load(root)
    graph.ensure_dir()
    report = IngestReport(phase="bootstrap")

    problem_id = make_id("problem", analysis_name)
    _add_node(
        graph,
        report,
        Node(
            id=problem_id,
            type="problem",
            label=_first_line(physics_prompt) or f"{analysis_name} physics question",
            content_ref="prompt.md",
            metadata={"analysis_type": analysis_type},
            created_by=BUILDER,
        ),
    )

    root_id = make_id("analysis_root", analysis_name)
    _add_node(
        graph,
        report,
        Node(
            id=root_id,
            type="analysis_root",
            label=analysis_name,
            metadata={"analysis_type": analysis_type},
            created_by=BUILDER,
        ),
    )
    _add_edge(graph, report, Edge(src=root_id, dst=problem_id, type="requires", created_by=BUILDER))

    # One pending artifact node per phase, chained by `requires`.
    for phase in _PHASE_ORDER:
        rel = artifact_rel_path(phase)
        if not rel:
            continue
        key = phase_key(phase)
        node_id = make_id("artifact", rel)
        # Only declare the placeholder if the phase has not been ingested yet —
        # a pending plan must never overwrite an artifact already on disk.
        if not graph.has_node(node_id):
            _add_node(
                graph,
                report,
                Node(
                    id=node_id,
                    type="artifact",
                    label=Path(rel).name,
                    content_ref=rel,
                    status="pending",
                    phase=key,
                    created_by=BUILDER,
                ),
            )

        upstream = _upstream_for(phase)
        prerequisites = [u for u in upstream if u.endswith(".md") and "/outputs/" in u]
        if not prerequisites:
            _add_edge(
                graph,
                report,
                Edge(src=node_id, dst=problem_id, type="requires", phase=key, created_by=BUILDER),
            )
        for rel_upstream in prerequisites:
            _add_edge(
                graph,
                report,
                Edge(
                    src=node_id,
                    dst=make_id("artifact", rel_upstream),
                    type="requires",
                    phase=key,
                    created_by=BUILDER,
                ),
            )

    return graph


# ---------------------------------------------------------------- ingestion


def ingest_phase(analysis_root: Path | str, phase: int | str) -> IngestReport:
    """Ingest everything a completed phase left on disk.

    Records the primary artifact and its lineage, figures, machine-readable
    results, analysis scripts, and the current state of every commitment.

    Args:
        analysis_root: The analysis root directory.
        phase: Phase identifier (1, 2, 3, "4a", "4b", "4c", 5).
    """
    root = Path(analysis_root)
    key = phase_key(phase)
    graph = AnalysisGraph.load(root)
    graph.ensure_dir()
    report = IngestReport(phase=key)

    directory = phase_dir(phase)
    if not directory:
        report.skipped.append(f"Unknown phase '{key}' — nothing ingested")
        return report

    artifact_id = _ingest_artifact(graph, report, root, phase, key)
    _ingest_figures(graph, report, root, key, directory, artifact_id)
    _ingest_results(graph, report, root, key, directory, artifact_id)
    _ingest_scripts(graph, report, root, key, directory, artifact_id)
    _ingest_commitments(graph, report, root, key)
    return report


def _ingest_artifact(
    graph: AnalysisGraph,
    report: IngestReport,
    root: Path,
    phase: int | str,
    key: str,
) -> str | None:
    """Record the phase's primary artifact and its `derives_from` lineage."""
    rel = artifact_rel_path(phase)
    if not rel or not (root / rel).exists():
        report.skipped.append(f"Phase {key} artifact not on disk yet ({rel or 'unknown'})")
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

    for rel_upstream in _upstream_for(phase):
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


def _ingest_figures(
    graph: AnalysisGraph,
    report: IngestReport,
    root: Path,
    key: str,
    directory: str,
    artifact_id: str | None,
) -> None:
    """Record every figure the phase produced, linked back to its artifact."""
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
    key: str,
    directory: str,
    artifact_id: str | None,
) -> None:
    """Record machine-readable results as evidence supporting the artifact.

    `results/*.json` is the single source of truth for numbers quoted in the
    analysis note, so each file becomes an evidence node. Top-level keys are
    captured as metadata to make the evidence searchable without reopening it.
    """
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
    key: str,
    directory: str,
    artifact_id: str | None,
) -> None:
    """Record the analysis code that produced the phase output."""
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
    key: str,
) -> None:
    """Turn every COMMITMENTS.md row into a node, closed by an evidence edge.

    Reuses `check_phase1_commitments` so the graph and the Phase 4a gate can
    never disagree about what a commitment's status is.
    """
    result = check_phase1_commitments(root)
    strategy_rel = artifact_rel_path(1)
    strategy_id = make_id("artifact", strategy_rel) if strategy_rel else None

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
                phase="1",
                created_by=BUILDER,
            ),
        )

        if strategy_id and graph.has_node(strategy_id):
            _add_edge(
                graph,
                report,
                Edge(
                    src=strategy_id,
                    dst=commitment_id,
                    type="commits_to",
                    phase="1",
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


def ingest_review(analysis_root: Path | str, phase: int | str) -> IngestReport:
    """Ingest the review round for a phase: reviewer findings and the verdict.

    Reviewer documents become `review` nodes attached to the artifact by
    `reviewed_by`. `ADJUDICATION.md` becomes a `decision` node whose verdict
    drives the outgoing edge: PASS approves the artifact, ITERATE and ESCALATE
    invalidate it, and REGRESS(M) points at the phase-M artifact instead.

    Args:
        analysis_root: The analysis root directory.
        phase: Phase identifier whose review directory should be ingested.
    """
    root = Path(analysis_root)
    key = phase_key(phase)
    graph = AnalysisGraph.load(root)
    graph.ensure_dir()
    report = IngestReport(phase=key)

    directory = phase_dir(phase)
    if not directory:
        report.skipped.append(f"Unknown phase '{key}' — no review ingested")
        return report

    review_dir = root / directory / "review"
    if not review_dir.is_dir():
        return report

    artifact_rel = artifact_rel_path(phase)
    artifact_id = make_id("artifact", artifact_rel) if artifact_rel else None
    if artifact_id and not graph.has_node(artifact_id):
        artifact_id = None
        report.skipped.append(f"Phase {key} artifact node missing — reviews left unattached")

    for review_file in sorted(review_dir.glob("*.md")):
        rel = f"{directory}/review/{review_file.name}"

        # The adjudication node is written once, by `_ingest_verdict`, so its
        # label can carry the verdict. Writing it here first would append a
        # second revision on every ingestion pass.
        if review_file.name.upper().startswith("ADJUDICATION"):
            _ingest_verdict(graph, report, key, artifact_id, review_file, rel)
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

    # A regression additionally points at the earlier phase that caused it.
    origin_rel = artifact_rel_path(regression_origin)
    origin_id = make_id("artifact", origin_rel) if origin_rel else ""
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


def rebuild(analysis_root: Path | str) -> IngestReport:
    """Re-derive the deterministic graph for every phase from the files on disk.

    Safe to run repeatedly: node ids are content-addressed, so an unchanged
    directory produces an unchanged graph.

    Args:
        analysis_root: The analysis root directory.
    """
    root = Path(analysis_root)
    combined = IngestReport(phase="rebuild")
    for phase in _PHASE_ORDER:
        combined.merge(ingest_phase(root, phase))
        combined.merge(ingest_review(root, phase))
    return combined


def _first_line(text: str) -> str:
    """Return the first non-empty, non-heading line, trimmed for use as a label."""
    for line in (text or "").splitlines():
        stripped = line.strip().lstrip("#").strip()
        if stripped:
            return stripped[:120]
    return ""


def _json_keys(path: Path) -> list[str]:
    """Return the top-level keys of a JSON file, or [] if it is not a JSON object."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    return sorted(data)[:20] if isinstance(data, dict) else []
