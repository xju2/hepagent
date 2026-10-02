"""What an analysis has established so far, for the progress panel.

The run log answers *what is happening right now*; this answers *what does the
analysis know*. The two are different questions with different lifetimes — the
log lives in memory and dies with the server, whereas this is read from the
analysis directory and is the same before a run starts, while it runs, and long
after it finished.

Everything here is derived. Nothing is stored, nothing is cached, and no field
is authored: node progress comes from whether the node's artifact is on disk,
and the physics content comes from the documents the executors wrote. That is
what lets the panel be correct after a server restart, after `jfc resume`, and
for an analysis this process never ran at all.

It is also the extension point. The strategy contributes the process inventory;
a later step contributes its selection, its yields, its fit result. Each becomes
another section built the same way — read the artifact the step already writes,
never a second copy of it kept for the panel's benefit.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from hepagent.agents.jfc.processes import (
    BACKGROUND_CATEGORIES,
    ProcessInventory,
    find_inventory,
    inventory_path,
)
from hepagent.plan.schema import AnalysisPlan
from hepagent.plan.store import load_plan

#: Order the panel lists processes in: the signal, then the backgrounds worst
#: first (irreducible cannot be selected away), then the observed data.
SECTION_ORDER: tuple[str, ...] = ("signal", *BACKGROUND_CATEGORIES, "data")


def _node_progress(analysis_root: Path, plan: AnalysisPlan) -> list[dict[str, Any]]:
    """One row per plan node: what it produces and whether it has produced it.

    "Done" here means *the artifact exists*, which is a claim about the
    filesystem and nothing more. Whether it passed review is a graph question and
    the run log's question; conflating the two would let a rejected artifact show
    as a finished step.
    """
    rows = []
    for node in plan.nodes:
        artifact = analysis_root / node.artifact_path
        figures_dir = analysis_root / node.outputs_dir / "figures"
        figures = (
            sum(1 for p in figures_dir.iterdir() if p.is_file()) if figures_dir.is_dir() else 0
        )
        rows.append(
            {
                "id": node.id,
                "label": node.label,
                "kind": node.kind,
                "artifact": node.artifact_path,
                "produced": artifact.is_file(),
                "figures": figures,
            }
        )
    return rows


def _process_sections(inventory: ProcessInventory | None) -> list[dict[str, Any]]:
    """The inventory grouped the way a physicist reads it.

    A section is emitted even when empty, because "no instrumental background
    identified" is itself a statement about the strategy and the reader has to
    be able to see that it was considered.
    """
    sections = []
    for name in SECTION_ORDER:
        if inventory is None:
            members: list = []
        elif name in BACKGROUND_CATEGORIES:
            members = inventory.by_category(name)
        else:
            members = inventory.by_role(name)
        sections.append(
            {
                "name": name,
                "kind": "background" if name in BACKGROUND_CATEGORIES else name,
                "processes": [p.to_dict() for p in members],
            }
        )
    return sections


def analysis_state(analysis_root: Path | str, plan: AnalysisPlan | None = None) -> dict[str, Any]:
    """Everything the progress panel renders, as JSON-able data.

    Raises nothing a caller has to handle beyond a missing or unreadable plan:
    each section degrades to "not established yet" on its own, so one
    half-written file cannot blank the panel.
    """
    root = Path(analysis_root)
    if plan is None:
        plan = load_plan(root)

    inventory, owner = find_inventory(root, plan)
    sections = _process_sections(inventory)

    return {
        "name": plan.name,
        "analysis_type": plan.analysis_type,
        "nodes": _node_progress(root, plan),
        "processes": {
            "recorded": inventory is not None,
            "node_id": owner.id if owner else "",
            "path": str(inventory_path(root, owner).relative_to(root)) if owner else "",
            "updated_at": inventory.updated_at if inventory else "",
            "count": len(inventory.processes) if inventory else 0,
            "datasets": [d.to_dict() for d in inventory.datasets()] if inventory else [],
            "sections": sections,
            "notes": inventory.notes if inventory else "",
        },
    }
