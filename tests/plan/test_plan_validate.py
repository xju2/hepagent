"""Structural rules over a plan. Severity is the gate, exactly as for the graph."""

from __future__ import annotations

import dataclasses

import pytest
from plan_factory import make_condition, make_loop_plan, make_node, make_plan

from hepagent.plan.schema import ConditionMetric, PlanContract, PlanEdge, PlanGate
from hepagent.plan.validate import PlanVocabulary, validate_plan


def rules(report) -> set[str]:
    return {f.rule for f in report.findings}


def messages(report) -> str:
    return " | ".join(f.message for f in report.findings)


def test_a_well_formed_plan_has_no_findings(fan_plan):
    report = validate_plan(fan_plan)
    assert report.findings == []
    assert report.ok


def test_the_report_is_titled_for_the_plan(plan):
    assert "## Plan validation" in validate_plan(plan).to_markdown()


# ------------------------------------------------------------------- P1 / P2


def test_duplicate_node_ids_are_blocking():
    plan = make_plan(node_ids=("a",), edges=(), nodes=(make_node("a"), make_node("a")))
    report = validate_plan(plan)
    assert "P1-ids" in rules(report)
    assert report.blocking


def test_two_nodes_writing_the_same_artifact_is_blocking():
    plan = make_plan(
        node_ids=("a", "b"),
        edges=(("a", "b"),),
        nodes=(
            make_node("a", directory="shared", artifact="OUT.md"),
            make_node("b", directory="shared", artifact="OUT.md"),
        ),
    )
    report = validate_plan(plan)
    assert "P2-outputs" in rules(report)
    assert "both write" in messages(report)


def test_a_directory_escaping_the_analysis_root_is_blocking():
    plan = make_plan(node_ids=("a",), edges=(), nodes=(make_node("a", directory="../elsewhere"),))
    assert "escapes the analysis root" in messages(validate_plan(plan))


def test_an_absolute_directory_is_blocking():
    plan = make_plan(node_ids=("a",), edges=(), nodes=(make_node("a", directory="/etc"),))
    assert "absolute path" in messages(validate_plan(plan))


def test_an_artifact_with_a_path_separator_is_blocking():
    plan = make_plan(node_ids=("a",), edges=(), nodes=(make_node("a", artifact="sub/OUT.md"),))
    assert "bare filename" in messages(validate_plan(plan))


# ------------------------------------------------------------------- P3 / P4


def test_an_edge_naming_an_unknown_node_is_blocking():
    plan = make_plan(node_ids=("a",), edges=(("ghost", "a"),))
    report = validate_plan(plan)
    assert "P3-edges" in rules(report)
    assert "ghost" in messages(report)


def test_a_duplicated_edge_is_blocking():
    plan = make_plan(node_ids=("a", "b"), edges=(("a", "b"), ("a", "b")))
    assert "Duplicate" in messages(validate_plan(plan))


def test_the_same_pair_with_different_kinds_is_not_a_duplicate():
    plan = make_plan(
        node_ids=("a", "b"),
        edges=(
            PlanEdge(upstream="a", downstream="b", kind="requires"),
            PlanEdge(upstream="a", downstream="b", kind="informs"),
        ),
    )
    assert "P3-edges" not in rules(validate_plan(plan))


def test_a_two_node_cycle_is_blocking_and_names_the_cycle():
    plan = make_plan(node_ids=("a", "b"), edges=(("a", "b"), ("b", "a")))
    report = validate_plan(plan)
    assert "P4-acyclic" in rules(report)
    assert "a" in messages(report) and "b" in messages(report)
    assert report.blocking


def test_a_longer_cycle_is_found():
    plan = make_plan(node_ids=("a", "b", "c"), edges=(("a", "b"), ("b", "c"), ("c", "a")))
    assert "P4-acyclic" in rules(validate_plan(plan))


def test_a_diamond_is_not_a_cycle(fan_plan):
    assert "P4-acyclic" not in rules(validate_plan(fan_plan))


def test_informs_edges_cannot_create_a_cycle():
    """Only blocking dependencies order execution, so an `informs` back-edge is legal."""
    plan = make_plan(
        node_ids=("a", "b"),
        edges=(
            PlanEdge(upstream="a", downstream="b"),
            PlanEdge(upstream="b", downstream="a", kind="informs"),
        ),
    )
    assert "P4-acyclic" not in rules(validate_plan(plan))


# ------------------------------------------------------------------- P5 / P9


def test_an_empty_plan_is_blocking():
    plan = make_plan(node_ids=(), edges=())
    report = validate_plan(plan)
    assert "P5-entry" in rules(report)
    assert report.blocking


def test_several_entry_nodes_are_advisory_not_blocking():
    plan = make_plan(node_ids=("a", "b", "c"), edges=(("a", "c"), ("b", "c")))
    report = validate_plan(plan)
    assert "P5-entry" in rules(report)
    assert not report.blocking


def test_a_plan_that_is_entirely_a_cycle_reports_that_nothing_can_start():
    plan = make_plan(node_ids=("a", "b"), edges=(("a", "b"), ("b", "a")))
    assert "No node can start" in messages(validate_plan(plan))


def test_an_unreachable_node_is_advisory():
    plan = make_plan(
        node_ids=("a", "b", "c", "d"),
        edges=(("a", "b"), ("c", "d"), ("d", "c")),
    )
    report = validate_plan(plan)
    assert "P9-reachable" in rules(report)
    assert {f.node_id for f in report.findings if f.rule == "P9-reachable"} == {"c", "d"}


# ------------------------------------------------------------------- P6


def test_an_unknown_role_is_blocking():
    plan = make_plan(node_ids=("a",), edges=(), nodes=(make_node("a", role="wizard"),))
    assert "unknown role" in messages(validate_plan(plan))


def test_an_unknown_gate_is_blocking():
    plan = make_plan(
        node_ids=("a",),
        edges=(),
        nodes=(make_node("a", gates=(PlanGate(name="vibes"),)),),
    )
    assert "unknown gate" in messages(validate_plan(plan))


def test_reviewer_names_are_only_checked_when_a_registry_is_supplied():
    plan = make_plan(node_ids=("a",), edges=(), nodes=(make_node("a", reviewers=("nope",)),))
    assert validate_plan(plan).ok
    assert not validate_plan(plan, vocabulary=PlanVocabulary(reviewers={"critical", "plot"})).ok


def test_a_known_reviewer_passes_the_registry_check():
    plan = make_plan(node_ids=("a",), edges=(), nodes=(make_node("a", reviewers=("critical",)),))
    assert validate_plan(plan, vocabulary=PlanVocabulary(reviewers={"critical", "plot"})).ok


def test_an_empty_reviewer_name_is_blocking():
    plan = make_plan(node_ids=("a",), edges=(), nodes=(make_node("a", reviewers=("  ",)),))
    assert not validate_plan(plan).ok


@pytest.mark.parametrize(
    ("field", "catalog", "label"),
    [
        ("tools", "tools", "function tool"),
        ("skills", "skills", "skill"),
        ("mcp_servers", "mcp_servers", "MCP server"),
    ],
)
def test_capability_names_are_only_checked_against_a_catalog(field, catalog, label):
    plan = make_plan(node_ids=("a",), edges=(), nodes=(make_node("a", **{field: ("nope",)}),))
    assert validate_plan(plan).ok
    report = validate_plan(plan, vocabulary=PlanVocabulary(**{catalog: {"real"}}))
    assert f"unknown {label} 'nope'" in messages(report)
    assert report.blocking


@pytest.mark.parametrize("field", ["tools", "skills", "mcp_servers"])
def test_a_known_capability_name_passes(field):
    plan = make_plan(node_ids=("a",), edges=(), nodes=(make_node("a", **{field: ("real",)}),))
    assert validate_plan(plan, vocabulary=PlanVocabulary(**{field: {"real"}})).ok


@pytest.mark.parametrize("field", ["tools", "skills", "mcp_servers"])
def test_an_empty_capability_name_is_always_blocking(field):
    plan = make_plan(node_ids=("a",), edges=(), nodes=(make_node("a", **{field: ("  ",)}),))
    assert not validate_plan(plan).ok


def test_an_empty_tool_allowlist_is_not_an_unknown_name():
    """`tools=()` says "no tools", which is a choice rather than a mistake."""
    plan = make_plan(node_ids=("a",), edges=(), nodes=(make_node("a", tools=()),))
    assert validate_plan(plan, vocabulary=PlanVocabulary(tools={"real"})).ok


def test_a_model_override_naming_an_unknown_platform_is_blocking():
    plan = make_plan(node_ids=("a",), edges=(), nodes=(make_node("a", model="nowhere:big"),))
    assert validate_plan(plan).ok
    report = validate_plan(plan, vocabulary=PlanVocabulary(platforms={"cborg"}))
    assert "unknown model platform 'nowhere'" in messages(report)
    assert report.blocking


@pytest.mark.parametrize("spec", ["cborg:big", "cborg:", "big"])
def test_a_model_override_the_runtime_can_read_passes(spec):
    """`platform:model`, `platform:` and a bare model name are all legal specs."""
    plan = make_plan(node_ids=("a",), edges=(), nodes=(make_node("a", model=spec),))
    assert validate_plan(plan, vocabulary=PlanVocabulary(platforms={"cborg"})).ok


@pytest.mark.parametrize("spec", ["", "   ", ":big"])
def test_a_model_override_with_no_platform_and_no_name_is_blocking(spec):
    """Null is how a node inherits the run's model; a blank string is a slip."""
    plan = make_plan(node_ids=("a",), edges=(), nodes=(make_node("a", model=spec),))
    assert not validate_plan(plan, vocabulary=PlanVocabulary(platforms={"cborg"})).ok


def test_an_arbiter_with_no_reviewers_is_advisory():
    plan = make_plan(node_ids=("a",), edges=(), nodes=(make_node("a", arbiter=True),))
    report = validate_plan(plan)
    assert "nothing to adjudicate" in messages(report)
    assert not report.blocking


def test_zero_max_iterations_is_blocking():
    plan = make_plan(node_ids=("a",), edges=(), nodes=(make_node("a", max_iterations=0),))
    assert "max_iterations" in messages(validate_plan(plan))


def test_an_unknown_inject_mode_is_blocking():
    plan = make_plan(
        node_ids=("a", "b"),
        edges=(PlanEdge(upstream="a", downstream="b", inject="telepathy"),),
    )
    assert "unknown inject mode" in messages(validate_plan(plan))


def test_an_unknown_edge_kind_is_blocking():
    plan = make_plan(
        node_ids=("a", "b"),
        edges=(PlanEdge(upstream="a", downstream="b", kind="suggests"),),
    )
    assert "unknown edge kind" in messages(validate_plan(plan))


def test_a_context_path_escaping_the_root_is_blocking():
    plan = make_plan(
        node_ids=("a",),
        edges=(),
        nodes=(make_node("a", context_paths=("../../secrets.md",)),),
    )
    assert "escapes the analysis root" in messages(validate_plan(plan))


# ------------------------------------------------------------------- P7 / P8


def test_a_contract_naming_an_unknown_graph_node_type_is_blocking():
    plan = make_plan(
        node_ids=("a",),
        edges=(),
        nodes=(make_node("a", contract=PlanContract(node_types=("hypothesis",))),),
    )
    assert "unknown graph node type" in messages(validate_plan(plan))


def test_a_contract_naming_an_unknown_graph_edge_type_is_blocking():
    plan = make_plan(
        node_ids=("a",),
        edges=(),
        nodes=(make_node("a", contract=PlanContract(edge_types=("implies",))),),
    )
    assert "unknown graph edge type" in messages(validate_plan(plan))


def test_a_real_contract_passes():
    plan = make_plan(
        node_ids=("a",),
        edges=(),
        nodes=(
            make_node(
                "a",
                contract=PlanContract(node_types=("evidence", "figure"), edge_types=("supports",)),
            ),
        ),
    )
    assert validate_plan(plan).ok


def test_a_note_writing_node_must_produce_markdown():
    plan = make_plan(
        node_ids=("a",),
        edges=(),
        nodes=(make_node("a", produces_note=True, artifact="RESULTS.json"),),
    )
    report = validate_plan(plan)
    assert "P8-notes" in rules(report)
    assert report.blocking


def test_a_note_writing_node_with_markdown_passes():
    plan = make_plan(
        node_ids=("a",),
        edges=(),
        nodes=(make_node("a", produces_note=True, artifact="NOTE.md"),),
    )
    assert validate_plan(plan).ok


# --------------------------------------------------------- shipped templates


def test_every_shipped_template_validates():
    from hepagent.plan.templates import instantiate, list_templates

    for name in list_templates():
        plan = instantiate(
            name, analysis_name="demo", analysis_type="measurement", physics_prompt="Do physics."
        )
        report = validate_plan(plan)
        assert report.findings == [], f"{name}: {messages(report)}"


# ------------------------------------------------------------------- P10


def test_a_bounded_loop_validates_clean():
    """The whole point: a cycle through a bounded condition node is legal."""
    report = validate_plan(make_loop_plan())
    assert report.findings == []
    assert not report.blocking


def test_a_loop_with_no_iteration_budget_is_blocking():
    report = validate_plan(make_loop_plan(max_iterations=0))
    assert "P10-conditions" in rules(report)
    assert report.blocking


def test_a_loop_that_can_only_time_out_is_advisory():
    """Running to the budget every time is wasteful, not wrong."""
    report = validate_plan(make_loop_plan(metric=None, question=""))
    assert "P10-conditions" in rules(report)
    assert not report.blocking


def test_a_very_large_budget_is_advisory():
    report = validate_plan(make_loop_plan(max_iterations=500))
    assert "P10-conditions" in rules(report)
    assert not report.blocking


def test_a_plain_requires_cycle_is_still_blocking_even_beside_a_condition():
    """P10 legalises loops through conditions, not loops in general."""
    plan = make_plan(
        node_ids=("a", "b"),
        edges=(("a", "b"), ("b", "a")),
        nodes=(make_node("a"), make_node("b"), make_condition()),
    )
    report = validate_plan(plan)
    assert "P4-acyclic" in rules(report)
    assert report.blocking


def test_only_a_condition_node_may_branch():
    plan = make_plan(
        node_ids=("a", "b"),
        edges=(PlanEdge(upstream="a", downstream="b", kind="on_true"),),
    )
    report = validate_plan(plan)
    assert "P10-conditions" in rules(report)
    assert "not a condition node" in messages(report)
    assert report.blocking


def test_a_condition_node_needs_a_condition_to_evaluate():
    plan = make_plan(
        node_ids=("a",),
        edges=(("a", "check"),),
        nodes=(make_node("a"), make_node("check", kind="condition", artifact="CHECK.md")),
    )
    report = validate_plan(plan)
    assert "P10-conditions" in rules(report)
    assert report.blocking


def test_a_condition_on_a_work_node_is_blocking():
    """Nothing would ever evaluate it."""
    plan = make_plan(
        node_ids=("a", "b"),
        nodes=(make_node("a", condition=make_condition().condition), make_node("b")),
    )
    report = validate_plan(plan)
    assert "P10-conditions" in rules(report)
    assert report.blocking


def test_a_condition_may_not_feed_a_requires_edge():
    """That node would run whichever way the condition went."""
    plan = make_plan(
        node_ids=("propose", "evaluate", "inference"),
        edges=(
            ("propose", "evaluate"),
            ("evaluate", "converged"),
            PlanEdge(upstream="converged", downstream="propose", kind="on_false"),
            ("converged", "inference"),
        ),
        nodes=(
            make_node("propose"),
            make_node("evaluate"),
            make_condition(),
            make_node("inference"),
        ),
    )
    report = validate_plan(plan)
    assert "P10-conditions" in rules(report)
    assert "its branches" in messages(report)
    assert report.blocking


def test_a_condition_with_nowhere_to_route_is_blocking():
    plan = make_plan(
        node_ids=("propose",),
        edges=(("propose", "converged"),),
        nodes=(make_node("propose"), make_condition()),
    )
    report = validate_plan(plan)
    assert "P10-conditions" in rules(report)
    assert report.blocking


def test_one_branch_only_is_advisory():
    plan = make_plan(
        node_ids=("propose", "evaluate"),
        edges=(
            ("propose", "evaluate"),
            ("evaluate", "converged"),
            PlanEdge(upstream="converged", downstream="propose", kind="on_false"),
        ),
        nodes=(make_node("propose"), make_node("evaluate"), make_condition()),
    )
    report = validate_plan(plan)
    assert "P10-conditions" in rules(report)
    assert not report.blocking


def test_a_metric_escaping_the_analysis_root_is_blocking():
    metric = ConditionMetric(source="../elsewhere/results.json", key="x", compare="below")
    report = validate_plan(make_loop_plan(metric=metric))
    assert "P10-conditions" in rules(report)
    assert report.blocking


def test_an_unknown_comparison_is_blocking():
    metric = ConditionMetric(source="a/outputs/r.json", key="x", compare="vibes")
    report = validate_plan(make_loop_plan(metric=metric))
    assert "P10-conditions" in rules(report)
    assert report.blocking


def test_an_unknown_exhaustion_action_is_blocking():
    report = validate_plan(make_loop_plan(on_exhaustion="maybe"))
    assert "P10-conditions" in rules(report)
    assert report.blocking


def test_reviewers_on_a_condition_node_are_advisory():
    """They would never run; that is worth saying but not worth blocking."""
    plan = make_loop_plan()
    condition = plan.node("converged")
    plan = dataclasses.replace(
        plan,
        nodes=tuple(
            dataclasses.replace(n, reviewers=("critical",)) if n.id == condition.id else n
            for n in plan.nodes
        ),
    )
    report = validate_plan(plan)
    assert "P10-conditions" in rules(report)
    assert not report.blocking


def test_capabilities_on_a_condition_node_are_advisory():
    """A condition is evaluated, not executed, so nothing would be attached."""
    plan = make_loop_plan()
    plan = dataclasses.replace(
        plan,
        nodes=tuple(
            dataclasses.replace(n, skills=("nyx",)) if n.id == "converged" else n
            for n in plan.nodes
        ),
    )
    report = validate_plan(plan)
    assert "tools, skills or MCP servers" in messages(report)
    assert not report.blocking


def test_nested_loops_report_their_worst_case_cost():
    """Two nested budgets multiply, which is easy to author by accident."""
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
            make_condition("inner_check", max_iterations=4),
            make_condition("outer_check", max_iterations=5),
            make_node("done"),
        ),
    )
    report = validate_plan(plan)
    assert "P10-conditions" in rules(report)
    assert "20 times" in messages(report)
    assert not report.blocking
