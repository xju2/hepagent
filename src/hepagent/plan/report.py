"""Human-readable renderings of an analysis plan.

The plan is the document a physicist argues with before any agent runs, so it has
to be readable in a terminal and in a chat window as well as in the editor. These
renderings are read-only views — nothing here changes a plan.
"""

from __future__ import annotations

import re

from hepagent.plan.layout import layer
from hepagent.plan.schema import AnalysisPlan, PlanNode

_UNSAFE = re.compile(r"[^A-Za-z0-9_]")


def _mermaid_id(node_id: str) -> str:
    """Mermaid identifiers may not contain punctuation."""
    return _UNSAFE.sub("_", node_id)


def to_mermaid(plan: AnalysisPlan) -> str:
    """Render the plan as a Mermaid `graph LR` block.

    Blocking `requires` edges are solid; advisory `informs` edges are dotted, so
    the picture shows at a glance what actually constrains the order.
    """
    lines = ["graph LR"]
    for node in plan.nodes:
        label = f"{node.label}".replace('"', "'")
        gates = "".join(f" ⛋{gate.name}" for gate in node.gates if gate.enabled)
        lines.append(f'    {_mermaid_id(node.id)}["{label}{gates}"]')
    for edge in plan.edges:
        arrow = "-->" if edge.kind == "requires" else "-.->"
        lines.append(
            f"    {_mermaid_id(edge.upstream)} {arrow}|{edge.kind}| {_mermaid_id(edge.downstream)}"
        )
    return "\n".join(lines)


def _reviewers(node: PlanNode) -> str:
    names = list(node.reviewers)
    if node.arbiter:
        names.append("+arbiter")
    return ", ".join(names) or "-"


def to_table(plan: AnalysisPlan) -> str:
    """Render the plan as a text table, in execution order.

    Columns are sized to their contents rather than clipped: an artifact path a
    reader cannot see in full is the one thing this view exists to show.
    """
    if not plan.nodes:
        return "(the plan declares no nodes)"

    positions = layer(plan)
    ordered = sorted(plan.nodes, key=lambda n: (positions.get(n.id, (0, 0)), n.id))

    ids = max(len(n.id) for n in plan.nodes) + 2
    artifacts = max([len(n.artifact_path) for n in plan.nodes] + [len("ARTIFACT")]) + 2

    lines = [
        f"{plan.name}  ({plan.analysis_type}, "
        f"template {plan.template or '(authored)'}, rev {plan.revision})",
        "",
        f"{'COL':<4} {'NODE':<{ids}} {'ARTIFACT':<{artifacts}} REVIEWERS",
        "-" * (ids + artifacts + 40),
    ]
    for node in ordered:
        column = positions.get(node.id, (0, 0))[0]
        lines.append(
            f"{column:<4} {node.id:<{ids}} {node.artifact_path:<{artifacts}} {_reviewers(node)}"
        )

    if plan.edges:
        arcs = max(len(f"{e.upstream} -> {e.downstream}") for e in plan.edges) + 2
        lines += ["", f"{'EDGE':<{arcs}} {'KIND':<10} INJECT", "-" * (arcs + 20)]
        for edge in plan.edges:
            arc = f"{edge.upstream} -> {edge.downstream}"
            lines.append(f"{arc:<{arcs}} {edge.kind:<10} {edge.inject}")
    return "\n".join(lines)
