"""Resolve a plan node id to its `PlanNode`, for the agent-facing JFC tools.

Tools are given an analysis root and a node id and have to find out where that
node's working directory is. They read `plan.json` rather than a module table, so
a renamed directory or an author-added node needs no code change.

Failures come back as an agent-readable string, never an exception: a tool that
raises loses the turn, whereas a tool that explains itself gets corrected.
"""

from __future__ import annotations

from pathlib import Path

from hepagent.plan.schema import AnalysisPlan, PlanNode
from hepagent.plan.store import load_plan


def resolve_node(analysis_root: str | Path, node_id: str) -> tuple[PlanNode | None, str]:
    """Look up a node in an analysis's plan.

    Returns:
        `(node, "")` on success, or `(None, message)` where `message` already
        starts with "Error:" and names the valid node ids.
    """
    root = Path(analysis_root)
    if not root.is_dir():
        return None, f"Error: analysis root not found: {analysis_root}"
    try:
        plan: AnalysisPlan = load_plan(root)
    except Exception as exc:  # noqa: BLE001 - reported to the agent, not raised
        return None, f"Error: could not read the analysis plan: {exc}"
    node = plan.node(str(node_id))
    if node is None:
        return None, (
            f"Error: unknown node '{node_id}'. Nodes in this analysis: {', '.join(plan.node_ids())}"
        )
    return node, ""
