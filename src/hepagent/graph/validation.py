"""Consistency rules over an `AnalysisGraph`.

These are the checks that turn the graph from a passive record into a gate: a
commitment nobody closed, a plot with no lineage, a figure reference pointing at
a file that is not there. Each rule returns findings; the caller decides whether
to block.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from hepagent.graph import query
from hepagent.graph.schema import check_edge_domain
from hepagent.graph.store import AnalysisGraph

Severity = str  # "error" | "warning"

# Severity is the gate. An "error" states something that is false about the
# analysis regardless of context — an edge pointing at a node that does not
# exist, a commitment nobody closed, a note referencing a figure that was never
# produced — and blocks the phase in code. A "warning" is a judgement call about
# what a phase was meant to produce by now, and goes to the arbiter to weigh.
BLOCKING_SEVERITY = "error"


@dataclass(frozen=True)
class GraphFinding:
    """One consistency problem found in the graph.

    Args:
        rule: Short rule identifier, e.g. "R2-commitments".
        severity: "error" (blocking) or "warning" (advisory).
        message: Human-readable description.
        node_id: The node the finding is about, when applicable.
    """

    rule: str
    severity: Severity
    message: str
    node_id: str | None = None


@dataclass
class GraphValidationReport:
    """Aggregate result of running the rule set.

    Args:
        findings: Every problem the rules reported.
        title: Heading `to_markdown` renders under. The plan rules in
            `hepagent.plan.validate` reuse this report type and override it.
    """

    findings: list[GraphFinding] = field(default_factory=list)
    title: str = "Graph validation"

    @property
    def errors(self) -> list[GraphFinding]:
        return [f for f in self.findings if f.severity == "error"]

    @property
    def warnings(self) -> list[GraphFinding]:
        return [f for f in self.findings if f.severity == "warning"]

    @property
    def blocking(self) -> list[GraphFinding]:
        """Findings that stop a phase from passing review, whatever the arbiter said."""
        return [f for f in self.findings if f.severity == BLOCKING_SEVERITY]

    @property
    def advisory(self) -> list[GraphFinding]:
        """Findings the arbiter should weigh but that do not force a verdict."""
        return [f for f in self.findings if f.severity != BLOCKING_SEVERITY]

    @property
    def ok(self) -> bool:
        """True when no blocking findings were raised."""
        return not self.errors

    def to_markdown(self) -> str:
        """Render the report for a reviewer agent or a CLI reader."""
        if not self.findings:
            return f"## {self.title}\n\nNo findings. Everything checked is internally consistent.\n"

        lines = [f"## {self.title}", ""]
        lines.append(f"{len(self.errors)} error(s), {len(self.warnings)} warning(s).")
        lines.append("")
        lines.append("| Severity | Rule | Node | Finding |")
        lines.append("|----------|------|------|---------|")
        for finding in self.findings:
            node = finding.node_id or "—"
            message = finding.message.replace("|", "\\|")
            lines.append(f"| {finding.severity} | {finding.rule} | `{node}` | {message} |")
        return "\n".join(lines) + "\n"


# --------------------------------------------------------------------- rules


def rule_schema_integrity(graph: AnalysisGraph) -> list[GraphFinding]:
    """R1 — every edge has existing endpoints and a legal type domain."""
    findings: list[GraphFinding] = []
    for edge in graph.edges():
        src = graph.get_node(edge.src)
        dst = graph.get_node(edge.dst)
        if src is None:
            findings.append(
                GraphFinding(
                    "R1-schema",
                    "error",
                    f"Edge '{edge.type}' references missing source node '{edge.src}'",
                    edge.src,
                )
            )
            continue
        if dst is None:
            findings.append(
                GraphFinding(
                    "R1-schema",
                    "error",
                    f"Edge '{edge.type}' references missing target node '{edge.dst}'",
                    edge.dst,
                )
            )
            continue
        domain_error = check_edge_domain(edge, src.type, dst.type)
        if domain_error:
            findings.append(GraphFinding("R1-schema", "error", domain_error, edge.src))
    return findings


def rule_commitments_closed(graph: AnalysisGraph) -> list[GraphFinding]:
    """R2 — every commitment is resolved or downscoped.

    **Timing-dependent, and deliberately absent from `REVIEW_RULES`.** A
    commitment is declared by the strategy node and closed by evidence that
    later nodes produce, so "not yet closed" is the *expected* state for most of
    an analysis. Running this at every node review made the strategy review
    impossible to pass: its own commitments were open by construction, the
    finding was an error, and the gate downgraded PASS to ITERATE until the
    iteration limit ran out — before any node that could close them had run.

    The `commitments` gate is where closure is genuinely due, and it runs this
    rule through `validate_commitments`.
    """
    return [
        GraphFinding(
            "R2-commitments",
            "error",
            f"Commitment '{node.label}' has no resolves/downscopes edge to closing evidence",
            node.id,
        )
        for node in query.unresolved_commitments(graph)
    ]


def rule_downscopes_justified(graph: AnalysisGraph) -> list[GraphFinding]:
    """R2b — a narrowed commitment records why it was narrowed.

    Unlike R2 this is timing-independent: a downscope with no cited evidence is
    wrong the moment it is written, whatever else has yet to run. It therefore
    stays in the review rule set.
    """
    findings: list[GraphFinding] = []
    for edge in graph.edges(type="downscopes"):
        if not edge.evidence_ref:
            node = graph.get_node(edge.src)
            label = node.label if node else edge.src
            findings.append(
                GraphFinding(
                    "R2b-downscope",
                    "error",
                    f"Commitment '{label}' was downscoped without a documented reason",
                    edge.src,
                )
            )
    return findings


def rule_provenance(graph: AnalysisGraph) -> list[GraphFinding]:
    """R3 — artifacts and figures record where they came from."""
    findings: list[GraphFinding] = []
    for node in query.orphan_claims(graph):
        # A pending node is a plan, not a claim: it carries `requires` edges
        # describing what it will need, and gains `derives_from` once produced.
        if node.status == "pending":
            continue
        # An entry artifact legitimately has no upstream artifact to derive from.
        # Which artifacts those are is authored in the plan and reaches the graph
        # as `requires` edges, so the graph alone can answer it.
        if node.type == "artifact" and _is_entry_artifact(graph, node):
            continue
        findings.append(
            GraphFinding(
                "R3-provenance",
                "error" if node.type == "figure" else "warning",
                f"{node.type.capitalize()} '{node.label}' has no derives_from lineage",
                node.id,
            )
        )
    return findings


def _is_entry_artifact(graph: AnalysisGraph, node) -> bool:
    """True when nothing upstream of this artifact produced another artifact.

    The first node of a plan starts from the physics prompt, not from a previous
    result, so demanding `derives_from` lineage of it would be noise. A branching
    plan can have several such nodes; this asks the graph rather than assuming
    there is exactly one.
    """
    for edge in graph.out_edges(node.id, type="requires"):
        target = graph.get_node(edge.dst)
        if target is not None and target.type == "artifact":
            return False
    return True


def rule_content_exists(graph: AnalysisGraph) -> list[GraphFinding]:
    """R4 — file-backed nodes point at files that are actually on disk."""
    findings: list[GraphFinding] = []
    for node in graph.nodes():
        if node.type not in ("artifact", "figure", "evidence"):
            continue
        # Pending nodes name a file the pipeline has not written yet.
        if node.status == "pending":
            continue
        if not node.content_ref:
            findings.append(
                GraphFinding(
                    "R4-content",
                    "warning",
                    f"{node.type.capitalize()} '{node.label}' records no content_ref",
                    node.id,
                )
            )
            continue
        if not (Path(graph.root) / node.content_ref).exists():
            findings.append(
                GraphFinding(
                    "R4-content",
                    "error",
                    f"{node.type.capitalize()} '{node.label}' points at missing file "
                    f"'{node.content_ref}'",
                    node.id,
                )
            )
    return findings


def rule_no_silent_deletion(graph: AnalysisGraph) -> list[GraphFinding]:
    """R5 — a commitment recorded earlier in the log is still present today.

    The append-only log makes this checkable: any commitment id that appears in
    `nodes.jsonl` history but is absent from the current graph was dropped
    rather than resolved or downscoped.
    """
    findings: list[GraphFinding] = []
    historical: dict[str, str] = {}
    for record in graph.node_history():
        if record.get("type") == "commitment":
            node_id = record.get("id")
            if isinstance(node_id, str):
                historical[node_id] = str(record.get("label", node_id))

    for node_id, label in sorted(historical.items()):
        if not graph.has_node(node_id):
            findings.append(
                GraphFinding(
                    "R5-no-deletion",
                    "error",
                    f"Commitment '{label}' appears in the graph history but not in the "
                    f"current graph — commitments may not be silently deleted",
                    node_id,
                )
            )
    return findings


def rule_figure_references(graph: AnalysisGraph) -> list[GraphFinding]:
    """R6 — every figure an analysis note points at is a declared figure node.

    A note that references `figures/mjj.png` when no such figure was produced
    renders as a broken image in the PDF and, worse, describes a plot nobody
    can trace. Both markdown images and raw `\\includegraphics` are checked.
    """
    findings: list[GraphFinding] = []
    root = Path(graph.root)

    for note in _analysis_notes(root):
        try:
            content = note.read_text(encoding="utf-8")
        except OSError:
            continue
        note_rel = query.relative_to_root(root, note)

        for reference in _figure_references(content):
            resolved = _resolve_reference(root, note, reference)
            if resolved is None:
                findings.append(
                    GraphFinding(
                        "R6-figure-refs",
                        "error",
                        f"{note_rel} references '{reference}', which is not a file "
                        f"under the analysis root",
                    )
                )
                continue

            rel = query.relative_to_root(root, resolved)
            if not resolved.exists():
                findings.append(
                    GraphFinding(
                        "R6-figure-refs",
                        "error",
                        f"{note_rel} references figure '{rel}', which does not exist on disk",
                    )
                )
                continue
            if not graph.find_by_content_ref(rel):
                findings.append(
                    GraphFinding(
                        "R6-figure-refs",
                        "error",
                        f"{note_rel} references figure '{rel}', which has no node in the "
                        f"graph — it cannot be traced to what produced it",
                    )
                )
    return findings


# Markdown images and raw LaTeX includes, the two forms the note writer emits.
_MD_IMAGE = re.compile(r"!\[[^\]]*\]\(\s*([^)\s]+)")
_TEX_INCLUDE = re.compile(r"\\includegraphics(?:\[[^\]]*\])?\{([^}]+)\}")
_IMAGE_SUFFIXES = (".png", ".pdf", ".jpg", ".jpeg", ".svg", ".eps")


def _analysis_notes(root: Path) -> list[Path]:
    """Return every analysis-note markdown file in the analysis directory.

    Which files those are is authored: the notes belong to the plan nodes marked
    `produces_note`. An analysis with no readable plan falls back to the shipped
    naming convention so a partially-scaffolded directory still validates.
    """
    if not root.is_dir():
        return []
    try:
        from hepagent.plan.store import load_plan

        plan = load_plan(root)
    except Exception:  # noqa: BLE001 - validation must not fail on a missing plan
        return sorted(root.glob("*/outputs/ANALYSIS_NOTE_*.md"))

    notes = [root / node.note_path for node in plan.nodes if node.produces_note]
    return sorted({note for note in notes if note.is_file()})


def _figure_references(content: str) -> list[str]:
    """Extract figure paths referenced from note markdown, de-duplicated in order."""
    found = _MD_IMAGE.findall(content) + _TEX_INCLUDE.findall(content)
    seen: dict[str, None] = {}
    for reference in found:
        cleaned = reference.strip().strip("\"'")
        if cleaned and not cleaned.startswith(("http://", "https://", "data:")):
            seen.setdefault(cleaned, None)
    return list(seen)


def _resolve_reference(root: Path, note: Path, reference: str) -> Path | None:
    """Resolve a note-relative or root-relative figure reference to a real path.

    Returns None when the reference escapes the analysis root, which is itself
    a finding — a note must not depend on files outside the analysis.
    """
    candidate = Path(reference)
    if not candidate.suffix:
        candidate = candidate.with_suffix(".png")

    options = [note.parent / candidate, root / candidate]
    if candidate.is_absolute():
        options.insert(0, candidate)

    for option in options:
        try:
            resolved = option.resolve()
        except OSError:
            continue
        try:
            resolved.relative_to(root.resolve())
        except ValueError:
            continue
        if resolved.exists() or resolved.suffix.lower() in _IMAGE_SUFFIXES:
            return resolved
    return None


ALL_RULES = (
    rule_schema_integrity,
    rule_commitments_closed,
    rule_downscopes_justified,
    rule_provenance,
    rule_content_exists,
    rule_no_silent_deletion,
    rule_figure_references,
)

#: Rules a node's own review may be blocked by.
#:
#: `rule_commitments_closed` is the one omission, and the omission is the point:
#: an open commitment says something about work that has not happened yet, not
#: about the node under review. Blocking on it made every review before the
#: `commitments` gate unpassable. This changes *which rules apply at which
#: checkpoint*, not what a severity means — invariant 7 of `docs/GRAPH.md` still
#: holds, and any error among these rules still blocks.
REVIEW_RULES = tuple(rule for rule in ALL_RULES if rule is not rule_commitments_closed)


def validate(graph: AnalysisGraph, rules=ALL_RULES) -> GraphValidationReport:
    """Run every rule and collect the findings.

    Args:
        graph: The loaded analysis graph.
        rules: Rule callables to run; defaults to `ALL_RULES`.
    """
    report = GraphValidationReport()
    for rule in rules:
        report.findings.extend(rule(graph))
    return report


def validate_commitments(graph: AnalysisGraph) -> GraphValidationReport:
    """Run only the commitment rule — used by the `commitments` gate."""
    return validate(graph, rules=(rule_commitments_closed,))


def validate_gating(graph: AnalysisGraph) -> GraphValidationReport:
    """Return only the findings that block a phase from passing review."""
    return GraphValidationReport(validate(graph).blocking)
