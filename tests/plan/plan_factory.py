"""Builders shared by the plan test modules.

Kept out of `conftest.py` so the test files can import them directly — `tests/`
is not a package, so a relative import would not resolve.

Keep these minimal: a test that cares about a field should set it explicitly
rather than depend on a default chosen here.
"""

from __future__ import annotations

from hepagent.plan.schema import AnalysisPlan, PlanEdge, PlanNode


def make_node(node_id: str, **overrides) -> PlanNode:
    """A valid work node with everything but its id defaulted."""
    payload = {
        "id": node_id,
        "label": node_id.replace("_", " ").title(),
        "directory": f"{node_id}_dir",
        "artifact": f"{node_id.upper()}.md",
        "prompt": f"Do the {node_id} work.",
    }
    payload.update(overrides)
    return PlanNode(**payload)


def make_plan(node_ids=("a", "b"), edges=(("a", "b"),), **overrides) -> AnalysisPlan:
    """A valid plan over `node_ids`, wired by `edges` as (upstream, downstream).

    An entry in `edges` may be a 2-tuple or a `PlanEdge`, so a test that needs a
    non-default `kind` or `inject` can pass one through.
    """
    payload = {
        "name": "demo",
        "analysis_type": "measurement",
        "nodes": tuple(make_node(n) for n in node_ids),
        "edges": tuple(
            e if isinstance(e, PlanEdge) else PlanEdge(upstream=e[0], downstream=e[1])
            for e in edges
        ),
        "problem": "# Physics Prompt\n\nMeasure something.",
    }
    payload.update(overrides)
    return AnalysisPlan(**payload)
