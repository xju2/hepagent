"""Builders shared by the plan test modules.

Kept out of `conftest.py` so the test files can import them directly — `tests/`
is not a package, so a relative import would not resolve.

Keep these minimal: a test that cares about a field should set it explicitly
rather than depend on a default chosen here.
"""

from __future__ import annotations

from hepagent.plan.schema import (
    AnalysisPlan,
    ConditionMetric,
    PlanCondition,
    PlanEdge,
    PlanNode,
)


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


def make_condition(node_id: str = "converged", **condition_fields) -> PlanNode:
    """A condition node with a bounded, metric-based test."""
    fields = {
        "metric": ConditionMetric(
            source="evaluate_dir/outputs/results/optimization.json",
            key="significance",
            compare="improvement_below",
            value=0.02,
        ),
        "max_iterations": 3,
    }
    fields.update(condition_fields)
    return make_node(
        node_id,
        kind="condition",
        artifact="CONDITION.md",
        reviewers=(),
        condition=PlanCondition(**fields),
    )


def make_loop_plan(**condition_fields) -> AnalysisPlan:
    """The canonical optimization loop: propose → evaluate → converged? ↺.

    ``on_false`` points back at ``propose`` (the loop) and ``on_true`` forward at
    ``inference`` (the exit), which is the shape every loop test wants.
    """
    return make_plan(
        node_ids=("propose", "evaluate", "inference"),
        edges=(
            ("propose", "evaluate"),
            ("evaluate", "converged"),
            PlanEdge(upstream="converged", downstream="propose", kind="on_false"),
            PlanEdge(upstream="converged", downstream="inference", kind="on_true"),
        ),
        nodes=(
            make_node("propose"),
            make_node("evaluate"),
            make_condition(**condition_fields),
            make_node("inference"),
        ),
    )
