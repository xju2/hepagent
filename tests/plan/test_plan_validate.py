"""Structural rules over a plan. Severity is the gate, exactly as for the graph."""

from __future__ import annotations

from plan_factory import make_node, make_plan

from hepagent.plan.schema import PlanContract, PlanEdge, PlanGate
from hepagent.plan.validate import validate_plan


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
    assert not validate_plan(plan, known_reviewers={"critical", "plot"}).ok


def test_a_known_reviewer_passes_the_registry_check():
    plan = make_plan(node_ids=("a",), edges=(), nodes=(make_node("a", reviewers=("critical",)),))
    assert validate_plan(plan, known_reviewers={"critical", "plot"}).ok


def test_an_empty_reviewer_name_is_blocking():
    plan = make_plan(node_ids=("a",), edges=(), nodes=(make_node("a", reviewers=("  ",)),))
    assert not validate_plan(plan).ok


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
