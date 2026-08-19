"""Human-readable renderings of a plan.

These are what `hepagent jfc plan show` prints and what the `/plan` chat command
sends, so they are the plan as most people first meet it.
"""

from __future__ import annotations

from plan_factory import make_loop_plan, make_node, make_plan

from hepagent.plan.report import to_mermaid, to_table
from hepagent.plan.schema import PlanEdge, PlanGate

# ------------------------------------------------------------------- mermaid


def test_a_node_is_drawn_as_a_box_labelled_with_its_label():
    assert 'a["A"]' in to_mermaid(make_plan())


def test_a_blocking_edge_is_solid_and_an_advisory_one_dotted():
    plan = make_plan(
        node_ids=("a", "b", "c"),
        edges=(("a", "b"), PlanEdge(upstream="a", downstream="c", kind="informs")),
    )
    diagram = to_mermaid(plan)
    assert "a -->|requires| b" in diagram
    assert "a -.->|informs| c" in diagram


def test_an_enabled_gate_shows_on_its_node():
    plan = make_plan(
        node_ids=("a", "b"),
        nodes=(make_node("a", gates=(PlanGate(name="human", when="after"),)), make_node("b")),
    )
    assert "⛋human" in to_mermaid(plan)


# ------------------------------------------------------------------- loops


def test_a_condition_is_drawn_as_a_diamond():
    assert 'converged{"Converged"}' in to_mermaid(make_loop_plan())


def test_the_loop_branch_says_it_loops_and_names_its_budget():
    """A loop should be legible as a loop in a chat window, not only in the editor."""
    assert "converged -.->|no — loop, max 4| propose" in to_mermaid(
        make_loop_plan(max_iterations=4)
    )


def test_the_forward_branch_is_labelled_yes_and_does_not_claim_to_loop():
    diagram = to_mermaid(make_loop_plan())
    assert "converged -.->|yes| inference" in diagram
    assert "yes — loop" not in diagram


# --------------------------------------------------------------------- table


def test_the_table_lists_every_node_in_execution_order():
    table = to_table(make_loop_plan())
    assert table.index("propose") < table.index("evaluate") < table.index("inference")


def test_an_empty_plan_says_so_rather_than_rendering_an_empty_table():
    assert "no nodes" in to_table(make_plan(node_ids=(), edges=()))
