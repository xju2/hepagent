"""Graph renderings for agent prompts.

Reviewers need to know what the graph says before they can check a claim
against it; the note writer needs the same view to write from. Both get it from
here — a bounded, human-readable slice of the graph rather than the raw JSONL.

Everything is deterministic and read-only. Nothing here calls a model.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from hepagent.graph import query
from hepagent.graph.schema import Node
from hepagent.graph.store import AnalysisGraph

_MAX_EVIDENCE_FILES = 24
_MAX_VALUES_PER_FILE = 40


def evidence_digest(graph: AnalysisGraph, phase: str | None = None) -> str:
    """Render the machine-readable results the prose must agree with.

    `results/*.json` is the single source of truth for every number quoted in an
    analysis note. Laying those values out flat is what lets a reviewer check
    number consistency, and the note writer quote them correctly the first time.

    Args:
        graph: The loaded analysis graph.
        phase: Restrict to evidence produced by one phase, when given.
    """
    nodes = [
        node
        for node in graph.nodes(type="evidence", phase=phase)
        if node.content_ref and node.content_ref.endswith(".json")
    ]
    if not nodes:
        return "No machine-readable results are recorded in the graph."

    lines = [
        "The following values are the single source of truth. Any number quoted",
        "in prose must match them exactly.",
        "",
    ]
    for node in nodes[:_MAX_EVIDENCE_FILES]:
        lines.append(f"### {node.content_ref}")
        values = _flatten_json(Path(graph.root) / node.content_ref)
        if not values:
            lines.append("  (unreadable or not a JSON object)")
        for key, value in values[:_MAX_VALUES_PER_FILE]:
            lines.append(f"  {key} = {value}")
        if len(values) > _MAX_VALUES_PER_FILE:
            lines.append(f"  ... {len(values) - _MAX_VALUES_PER_FILE} more value(s)")
        lines.append("")

    if len(nodes) > _MAX_EVIDENCE_FILES:
        lines.append(f"... {len(nodes) - _MAX_EVIDENCE_FILES} more results file(s) not shown.")
    return "\n".join(lines).rstrip() + "\n"


def figure_manifest(graph: AnalysisGraph, phase: str | None = None) -> str:
    """List the figures that exist, with the path a note must reference.

    A note may only reference figures on this list. Anything else is a broken
    image in the PDF and an untraceable claim in the graph.

    Args:
        graph: The loaded analysis graph.
        phase: Restrict to figures produced by one phase, when given.
    """
    figures = graph.nodes(type="figure", phase=phase)
    if not figures:
        return "No figures are recorded in the graph."

    lines = ["Reference figures by these exact paths (relative to the analysis root):", ""]
    for node in figures:
        produced_by = query.ancestors(graph, node.id)
        origin = produced_by[0].label if produced_by else "unknown"
        exists = "" if (Path(graph.root) / (node.content_ref or "")).exists() else "  [MISSING]"
        lines.append(f"- `{node.content_ref}` — produced by {origin}{exists}")
    return "\n".join(lines) + "\n"


def commitment_ledger(graph: AnalysisGraph) -> str:
    """Render every commitment with the evidence that closed it, or its absence."""
    commitments = graph.nodes(type="commitment")
    if not commitments:
        return "No commitments are recorded in the graph."

    lines = ["| ID | Commitment | Status | Closed by |", "|----|-----------|--------|-----------|"]
    for node in commitments:
        closing = graph.out_edges(node.id, type="resolves") + graph.out_edges(
            node.id, type="downscopes"
        )
        if closing:
            edge = closing[0]
            target = graph.get_node(edge.dst)
            closed_by = (
                f"{edge.type}: {edge.evidence_ref or (target.label if target else edge.dst)}"
            )
        else:
            closed_by = "**nothing — still open**"
        label = node.label.replace("|", "\\|")
        lines.append(f"| {node.id} | {label} | {node.status} | {closed_by} |")
    return "\n".join(lines) + "\n"


def provenance_brief(graph: AnalysisGraph, phase: str) -> str:
    """Summarise what a phase produced and what it was derived from."""
    nodes = graph.nodes(phase=phase)
    if not nodes:
        return f"The graph records nothing for phase {phase} yet."

    lines = [f"Nodes recorded for phase {phase}:", ""]
    for node in nodes:
        if node.status == "pending":
            continue
        lineage = query.ancestors(graph, node.id)
        origin = ", ".join(a.label for a in lineage[:3]) or "no recorded lineage"
        lines.append(f"- {node.type}: {node.label} ← {origin}")

    rejected = [n for n in nodes if query.rejections(graph, n.id)]
    if rejected:
        lines.append("")
        lines.append("Previously rejected in this phase:")
        for node in rejected:
            for rejection in query.rejections(graph, node.id):
                lines.append(f"- {node.label} invalidated by {rejection.label}")
    return "\n".join(lines) + "\n"


def phase_brief(graph: AnalysisGraph, phase: str) -> str:
    """The full graph slice an agent working on `phase` should see."""
    return "\n\n".join(
        [
            "## GRAPH: what this phase has produced",
            provenance_brief(graph, phase),
            "## GRAPH: commitments",
            commitment_ledger(graph),
            "## GRAPH: figures available",
            figure_manifest(graph),
            "## GRAPH: machine-readable results",
            evidence_digest(graph),
        ]
    )


def note_brief(graph: AnalysisGraph) -> str:
    """The graph slice the note writer must write from.

    Deliberately whole-analysis rather than per-phase: a note draws figures and
    numbers from every phase that came before it.
    """
    return "\n\n".join(
        [
            "## GRAPH: figures you may reference",
            figure_manifest(graph),
            "## GRAPH: numbers you must quote exactly",
            evidence_digest(graph),
            "## GRAPH: commitments to account for",
            commitment_ledger(graph),
        ]
    )


def _flatten_json(path: Path) -> list[tuple[str, Any]]:
    """Flatten a JSON object into dotted key/value pairs for prompt display."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(data, dict):
        return []

    flat: list[tuple[str, Any]] = []

    def walk(prefix: str, value: Any) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                walk(f"{prefix}.{key}" if prefix else str(key), child)
        elif isinstance(value, list):
            # Long arrays are bin contents; show the shape, not every entry.
            if len(value) > 8:
                flat.append((f"{prefix}[{len(value)}]", f"{value[:4]} ... {value[-2:]}"))
            else:
                flat.append((prefix, value))
        else:
            flat.append((prefix, value))

    walk("", data)
    return flat


def node_summary(nodes: list[Node]) -> str:
    """One-line-per-node rendering used in progress messages and errors."""
    return "; ".join(f"{n.type}:{n.label}" for n in nodes) or "(none)"
