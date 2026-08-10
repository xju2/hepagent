"""Deterministic layered layout, computed in Python so the editor only draws."""

from __future__ import annotations

from plan_factory import make_plan

from hepagent.plan.layout import columns, height, layer, width
from hepagent.plan.templates import instantiate


def test_a_linear_plan_lays_out_left_to_right(plan):
    assert layer(plan) == {"a": (0, 0), "b": (1, 0)}


def test_every_node_sits_right_of_everything_it_depends_on(fan_plan):
    positions = layer(fan_plan)
    for node in fan_plan.nodes:
        for upstream in fan_plan.prerequisites(node.id):
            assert positions[upstream][0] < positions[node.id][0]


def test_a_fan_out_shares_a_column_and_splits_rows(fan_plan):
    positions = layer(fan_plan)
    assert positions["left"][0] == positions["right"][0] == 1
    assert {positions["left"][1], positions["right"][1]} == {0, 1}


def test_the_merge_node_lands_in_its_own_column(fan_plan):
    assert layer(fan_plan)["merge"] == (2, 0)


def test_column_is_the_longest_path_not_the_shortest():
    """`late` depends on both `early` and a two-step chain, so it belongs in
    column 3 — placing it at 1 would draw an edge pointing backwards."""
    plan = make_plan(
        node_ids=("early", "mid", "later", "late"),
        edges=(("early", "mid"), ("mid", "later"), ("later", "late"), ("early", "late")),
    )
    assert columns(plan)["late"] == 3


def test_layout_is_deterministic(fan_plan):
    assert layer(fan_plan) == layer(fan_plan)


def test_layout_ignores_dragged_positions_stored_on_the_node():
    """`layer` answers 'where would this go if nobody had moved it'; the editor
    prefers the stored coordinates itself."""
    from plan_factory import make_node

    plan = make_plan(
        node_ids=("a", "b"),
        edges=(("a", "b"),),
        nodes=(make_node("a", metadata={"x": 900, "y": 900}), make_node("b")),
    )
    assert layer(plan)["a"] == (0, 0)


def test_an_empty_plan_has_no_positions():
    plan = make_plan(node_ids=(), edges=())
    assert layer(plan) == {}
    assert width(plan) == 0
    assert height(plan) == 0


def test_disconnected_nodes_all_start_in_column_zero():
    plan = make_plan(node_ids=("a", "b", "c"), edges=())
    assert {position[0] for position in layer(plan).values()} == {0}
    assert height(plan) == 3


def test_a_cycle_still_produces_a_layout():
    """The editor has to be able to draw the cycle for the user to fix it."""
    plan = make_plan(node_ids=("a", "b"), edges=(("a", "b"), ("b", "a")))
    positions = layer(plan)
    assert set(positions) == {"a", "b"}


def test_width_and_height_describe_the_grid(fan_plan):
    assert width(fan_plan) == 3
    assert height(fan_plan) == 2


def test_the_default_template_lays_out_as_a_single_chain():
    plan = instantiate(
        "jfc-measurement", analysis_name="x", analysis_type="measurement", physics_prompt="P"
    )
    positions = layer(plan)
    assert width(plan) == 7
    assert height(plan) == 1
    assert positions["strategy"] == (0, 0)
    assert positions["documentation"] == (6, 0)


def test_a_channel_fan_out_keeps_the_shared_upstream_in_one_column():
    """The motivating multi-channel case: two selections between one exploration
    and one inference node."""
    plan = make_plan(
        node_ids=("exploration", "selection_ee", "selection_mumu", "inference"),
        edges=(
            ("exploration", "selection_ee"),
            ("exploration", "selection_mumu"),
            ("selection_ee", "inference"),
            ("selection_mumu", "inference"),
        ),
    )
    positions = layer(plan)
    assert positions["exploration"] == (0, 0)
    assert positions["selection_ee"][0] == positions["selection_mumu"][0] == 1
    assert positions["inference"][0] == 2
