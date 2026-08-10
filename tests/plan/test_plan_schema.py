"""Record types for the analysis plan."""

from __future__ import annotations

import pytest
from plan_factory import make_node, make_plan

from hepagent.plan.schema import (
    EDGE_KINDS,
    GATE_TIMINGS,
    GATES,
    INJECT_MODES,
    NODE_KINDS,
    ROLES,
    AnalysisPlan,
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
        metadata={"x": 3, "y": 1},
    )
    assert PlanNode.from_dict(node.to_dict()) == node


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
    assert set(NODE_KINDS) == {"work", "gate"}
    assert "executor" in ROLES
    assert set(GATES) == {"commitments", "human", "codesign"}
    assert set(GATE_TIMINGS) == {"before", "after"}
    assert set(EDGE_KINDS) == {"requires", "informs"}
    assert set(INJECT_MODES) == {"full", "summary", "none"}
