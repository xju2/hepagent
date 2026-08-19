"""Record types for the analysis plan."""

from __future__ import annotations

import pytest
from plan_factory import make_condition, make_loop_plan, make_node, make_plan

from hepagent.plan.schema import (
    BRANCH_KINDS,
    EDGE_KINDS,
    EXHAUSTION,
    GATE_TIMINGS,
    GATES,
    INJECT_MODES,
    NODE_KINDS,
    ROLES,
    AnalysisPlan,
    ConditionMetric,
    PlanCondition,
    PlanContract,
    PlanEdge,
    PlanGate,
    PlanNode,
    PlanSchemaError,
)

# ------------------------------------------------------------------ node ids


def test_node_id_must_be_a_slug():
    for bad in ("Strategy", "4a", "with space", "trailing/slash", ""):
        with pytest.raises(PlanSchemaError):
            make_node(bad)


def test_node_id_accepts_letters_digits_underscores_and_hyphens():
    for good in ("strategy", "selection_ee", "calib-btag", "phase4a"):
        assert make_node(good).id == good


def test_unknown_node_kind_is_rejected():
    with pytest.raises(PlanSchemaError):
        make_node("a", kind="wishful")


# ------------------------------------------------------------------ artifacts


def test_artifact_path_is_rooted_at_the_node_directory():
    node = make_node("selection", directory="phase3_selection", artifact="SELECTION.md")
    assert node.artifact_path == "phase3_selection/outputs/SELECTION.md"


# ---------------------------------------------------------------------- edges


def test_edge_endpoints_must_be_non_empty():
    with pytest.raises(PlanSchemaError):
        PlanEdge(upstream="", downstream="b")
    with pytest.raises(PlanSchemaError):
        PlanEdge(upstream="a", downstream="")


def test_self_edges_are_rejected():
    with pytest.raises(PlanSchemaError):
        PlanEdge(upstream="a", downstream="a")


def test_edge_key_is_the_supersession_identity():
    a = PlanEdge(upstream="a", downstream="b", inject="full")
    b = PlanEdge(upstream="a", downstream="b", inject="none")
    assert a.key == b.key == ("a", "b", "requires")


def test_edge_names_endpoints_in_data_flow_order():
    """`upstream`/`downstream` exist so a plan edge can never be confused with a
    graph `requires` edge, whose `src` is the dependent node."""
    assert not hasattr(PlanEdge(upstream="a", downstream="b"), "src")


# ---------------------------------------------------------------------- gates


def test_gate_timing_must_be_known():
    with pytest.raises(PlanSchemaError):
        PlanGate(name="human", when="whenever")


def test_gates_at_returns_only_enabled_gates_for_that_position():
    node = make_node(
        "a",
        gates=(
            PlanGate(name="commitments", when="before"),
            PlanGate(name="human", when="after"),
            PlanGate(name="codesign", when="after", enabled=False),
        ),
    )
    assert [g.name for g in node.gates_at("before")] == ["commitments"]
    assert [g.name for g in node.gates_at("after")] == ["human"]


# ------------------------------------------------------------------- lookups


def test_prerequisites_ignore_informs_edges():
    plan = make_plan(
        node_ids=("a", "b"),
        edges=(),
    )
    plan = AnalysisPlan(
        name=plan.name,
        analysis_type=plan.analysis_type,
        nodes=plan.nodes,
        edges=(PlanEdge(upstream="a", downstream="b", kind="informs"),),
    )
    assert plan.prerequisites("b") == []
    assert [e.kind for e in plan.upstream_edges("b")] == ["informs"]


def test_entry_nodes_are_those_with_no_blocking_prerequisite(fan_plan):
    assert [n.id for n in fan_plan.entry_nodes()] == ["root"]


def test_require_node_names_the_missing_id(plan):
    with pytest.raises(PlanSchemaError, match="nope"):
        plan.require_node("nope")


def test_plan_name_must_be_non_empty():
    with pytest.raises(PlanSchemaError):
        AnalysisPlan(name="", analysis_type="measurement")


# ------------------------------------------------------------- serialisation


def test_plan_round_trips_through_dict(fan_plan):
    restored = AnalysisPlan.from_dict(fan_plan.to_dict())
    assert restored == fan_plan


def test_node_round_trips_with_every_field_populated():
    node = make_node(
        "inference",
        kind="work",
        role="executor",
        context_paths=("COMMITMENTS.md",),
        reviewers=("critical", "plot"),
        arbiter=True,
        produces_note=True,
        gates=(PlanGate(name="commitments", when="before"),),
        contract=PlanContract(node_types=("evidence",), edge_types=("supports",)),
        max_iterations=5,
        model="cborg:some-model",
        tools=("read_file",),
        skills=("nyx",),
        mcp_servers=("lep-corpus",),
        metadata={"x": 3, "y": 1},
    )
    assert PlanNode.from_dict(node.to_dict()) == node


def test_capabilities_default_to_empty_on_a_plan_that_predates_them():
    """An older `plan.json` names no skills or MCP servers; that is not an error."""
    payload = make_node("a").to_dict()
    del payload["skills"], payload["mcp_servers"]
    restored = PlanNode.from_dict(payload)
    assert restored.skills == () and restored.mcp_servers == ()


def test_from_dict_ignores_unknown_fields():
    """A plan written by a newer build must still load in an older one."""
    payload = make_node("a").to_dict() | {"invented_later": True}
    assert PlanNode.from_dict(payload).id == "a"


def test_tools_none_and_empty_are_distinct():
    """None means 'the default set for this role'; () means 'no tools at all'."""
    assert PlanNode.from_dict(make_node("a", tools=None).to_dict()).tools is None
    assert PlanNode.from_dict(make_node("a", tools=()).to_dict()).tools == ()


def test_to_dict_is_json_serialisable(fan_plan):
    import json

    assert json.loads(json.dumps(fan_plan.to_dict()))["name"] == "demo"


# ------------------------------------------------------------- vocabularies


def test_vocabularies_are_non_empty_and_disjoint_where_they_should_be():
    assert set(NODE_KINDS) == {"work", "gate", "condition"}
    assert "executor" in ROLES
    assert set(GATES) == {"commitments", "human", "codesign"}
    assert set(GATE_TIMINGS) == {"before", "after"}
    assert set(EDGE_KINDS) == {"requires", "informs", "on_true", "on_false"}
    assert set(BRANCH_KINDS) < set(EDGE_KINDS)
    assert set(INJECT_MODES) == {"full", "summary", "none"}
    assert set(EXHAUSTION) == {"true", "false", "escalate"}


# ----------------------------------------------------------------- conditions


def test_a_condition_round_trips_through_json():
    import json

    plan = make_loop_plan()
    restored = AnalysisPlan.from_dict(json.loads(json.dumps(plan.to_dict())))
    assert restored == plan
    assert restored.node("converged").condition.metric.compare == "improvement_below"


def test_a_node_without_a_condition_serialises_it_as_null():
    assert make_node("a").to_dict()["condition"] is None


def test_a_metric_missing_its_source_or_key_is_dropped_rather_than_half_built():
    """Half a metric would silently compare against nothing."""
    assert ConditionMetric.from_dict({"key": "significance"}) is None
    assert ConditionMetric.from_dict({"source": "a.json"}) is None
    assert ConditionMetric.from_dict(None) is None


def test_a_condition_block_survives_without_a_metric():
    """The language-only form is a legitimate condition."""
    condition = PlanCondition.from_dict({"question": "converged?", "max_iterations": 2})
    assert condition.metric is None
    assert condition.max_iterations == 2


# -------------------------------------------------- forward / back branches


def test_a_branch_pointing_at_an_ancestor_is_a_back_branch():
    plan = make_loop_plan()
    assert sorted(plan.back_branch_keys()) == [("converged", "propose", "on_false")]


def test_a_back_branch_is_not_a_prerequisite_so_the_loop_head_can_still_start():
    """Counting it would make every node in the loop wait on the loop."""
    plan = make_loop_plan()
    assert plan.prerequisites("propose") == []
    assert [n.id for n in plan.entry_nodes()] == ["propose"]


def test_a_forward_branch_blocks_its_target():
    plan = make_loop_plan()
    assert plan.prerequisites("inference") == ["converged"]


def test_a_branch_into_a_sibling_subtree_is_forward_not_back():
    """Only an ancestor of the condition makes a cycle; a sibling does not."""
    plan = make_plan(
        node_ids=("root", "left", "right"),
        edges=(
            ("root", "left"),
            ("left", "check"),
            PlanEdge(upstream="check", downstream="right", kind="on_true"),
            PlanEdge(upstream="check", downstream="left", kind="on_false"),
        ),
        nodes=(
            make_node("root"),
            make_node("left"),
            make_condition("check"),
            make_node("right"),
        ),
    )
    assert sorted(plan.back_branch_keys()) == [("check", "left", "on_false")]
    assert plan.prerequisites("right") == ["check"]


def test_a_nested_loops_outer_back_branch_is_found_through_the_inner_forward_one():
    """The outer loop only closes through the inner loop's exit branch.

    Classifying against `requires` edges alone misses it and puts a real cycle
    into the blocking graph, which is what P4 would then report.
    """
    plan = make_plan(
        node_ids=("outer_head", "inner_head", "inner_tail"),
        edges=(
            ("outer_head", "inner_head"),
            ("inner_head", "inner_tail"),
            ("inner_tail", "inner_check"),
            PlanEdge(upstream="inner_check", downstream="inner_head", kind="on_false"),
            PlanEdge(upstream="inner_check", downstream="outer_check", kind="on_true"),
            PlanEdge(upstream="outer_check", downstream="outer_head", kind="on_false"),
            PlanEdge(upstream="outer_check", downstream="done", kind="on_true"),
        ),
        nodes=(
            make_node("outer_head"),
            make_node("inner_head"),
            make_node("inner_tail"),
            make_condition("inner_check"),
            make_condition("outer_check"),
            make_node("done"),
        ),
    )
    assert sorted(plan.back_branch_keys()) == [
        ("inner_check", "inner_head", "on_false"),
        ("outer_check", "outer_head", "on_false"),
    ]
    assert plan.prerequisites("outer_head") == []
    assert plan.prerequisites("done") == ["outer_check"]


def test_branch_classification_survives_a_plain_requires_cycle():
    """The editor has to draw an invalid plan for the user to fix it."""
    plan = make_plan(node_ids=("a", "b"), edges=(("a", "b"), ("b", "a")))
    assert plan.back_branch_keys() == frozenset()


def test_branch_edges_lists_only_what_leaves_a_condition():
    plan = make_loop_plan()
    assert {e.kind for e in plan.branch_edges("converged")} == {"on_true", "on_false"}
    assert plan.branch_edges("propose") == []
