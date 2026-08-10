"""Tests for structural plan edits.

Nothing here calls a model: `apply_edits` is pure, which is the point of keeping
the edit vocabulary out of the architect.
"""

from __future__ import annotations

import pytest
from plan_factory import make_plan

from hepagent.plan.edits import PlanEdit, apply_edits, slugify
from hepagent.plan.schema import PlanContract, PlanEdge, PlanGate
from hepagent.plan.templates import instantiate
from hepagent.plan.validate import validate_plan

PROMPT = "Measure the Z->bb cross section in the ee and mumu channels."


@pytest.fixture
def jfc():
    return instantiate(
        "jfc-measurement",
        analysis_name="zbb",
        analysis_type="measurement",
        physics_prompt=PROMPT,
    )


def apply(plan, *edits):
    return apply_edits(plan, list(edits))


# ------------------------------------------------------------------- slugify


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("selection_ee", "selection_ee"),
        ("Selection EE", "selection_ee"),
        ("  Calibration!  ", "calibration"),
        ("4a", ""),  # a node id must start with a letter
        ("!!!", ""),
    ],
)
def test_slugify(raw, expected):
    assert slugify(raw) == expected


# ------------------------------------------------------------------ dispatch


def test_an_unknown_op_is_skipped_not_fatal():
    report = apply(make_plan(), PlanEdit(op="reticulate_splines"))
    assert report.applied == []
    assert "unknown op" in report.skipped[0]


def test_edits_apply_in_order_each_seeing_the_last(jfc):
    """A split followed by a rewire of one half must work."""
    report = apply(
        jfc,
        PlanEdit(op="split_node", node_id="selection", into=("selection_ee", "selection_mumu")),
        PlanEdit(op="remove_edge", upstream="selection_mumu", downstream="documentation"),
    )
    assert len(report.applied) == 2
    assert report.skipped == []
    assert not any(
        e.upstream == "selection_mumu" and e.downstream == "documentation"
        for e in report.plan.edges
    )


def test_a_rejected_edit_leaves_the_earlier_ones_standing(jfc):
    report = apply(
        jfc,
        PlanEdit(op="remove_node", node_id="inference_partial"),
        PlanEdit(op="remove_node", node_id="no_such_node"),
    )
    assert len(report.applied) == 1
    assert len(report.skipped) == 1
    assert report.plan.node("inference_partial") is None


# ----------------------------------------------------------------- add_node


def test_add_node_inherits_from_like(jfc):
    report = apply(
        jfc,
        PlanEdit(
            op="add_node",
            node_id="calibration",
            label="Jet energy calibration",
            artifact="CALIBRATION.md",
            prompt="Derive the in-situ jet energy scale.",
            like="selection",
        ),
    )
    added = report.plan.require_node("calibration")
    source = jfc.require_node("selection")
    assert added.reviewers == source.reviewers
    assert added.contract == source.contract
    assert added.artifact == "CALIBRATION.md"
    assert added.prompt == "Derive the in-situ jet energy scale."


def test_add_node_lands_next_to_the_node_it_copies(jfc):
    """Declaration order breaks ties between equally-ready nodes, so it matters."""
    report = apply(
        jfc,
        PlanEdit(op="add_node", node_id="calibration", prompt="Calibrate.", like="exploration"),
    )
    ids = list(report.plan.node_ids())
    assert ids[ids.index("exploration") + 1] == "calibration"


def test_add_node_without_like_starts_bare(jfc):
    report = apply(jfc, PlanEdit(op="add_node", node_id="scan", prompt="Scan the mass points."))
    added = report.plan.require_node("scan")
    assert added.reviewers == ()
    assert added.contract == PlanContract()
    assert added.directory == "scan"
    assert added.artifact == "SCAN.md"


def test_a_copied_node_does_not_inherit_note_writing(jfc):
    """Two nodes writing the same note would fight over the file."""
    report = apply(
        jfc,
        PlanEdit(op="add_node", node_id="reinterpret", prompt="Reinterpret.", like="documentation"),
    )
    added = report.plan.require_node("reinterpret")
    assert added.produces_note is False
    assert added.note_artifact == ""


def test_add_node_needs_a_prompt(jfc):
    report = apply(jfc, PlanEdit(op="add_node", node_id="empty", prompt="   "))
    assert "needs a prompt" in report.skipped[0]


def test_add_node_refuses_a_duplicate_id(jfc):
    report = apply(jfc, PlanEdit(op="add_node", node_id="selection", prompt="Again."))
    assert "already exists" in report.skipped[0]


def test_add_node_refuses_an_unusable_id(jfc):
    report = apply(jfc, PlanEdit(op="add_node", node_id="4a", prompt="Do it."))
    assert "not a usable node id" in report.skipped[0]


def test_add_node_refuses_to_inherit_from_a_missing_node(jfc):
    report = apply(jfc, PlanEdit(op="add_node", node_id="scan", prompt="Scan.", like="phase_nine"))
    assert "cannot inherit" in report.skipped[0]


# --------------------------------------------------------------- remove_node


def test_remove_node_takes_its_edges_with_it(jfc):
    report = apply(jfc, PlanEdit(op="remove_node", node_id="inference_partial"))
    assert report.plan.node("inference_partial") is None
    assert not any("inference_partial" in (e.upstream, e.downstream) for e in report.plan.edges)
    assert "edge(s)" in report.applied[0]


def test_removing_a_middle_node_does_not_re_parent_what_followed_it(jfc):
    """Removal is not contraction: downstream nodes keep only what they declared.

    The template wires each inference node to several upstreams, so dropping one
    of them leaves a plan that still validates. Silently re-parenting instead
    would invent a dependency nobody authored.
    """
    before = set(jfc.prerequisites("inference_partial"))
    report = apply(jfc, PlanEdit(op="remove_node", node_id="inference_expected"))

    assert report.skipped == []
    assert set(report.plan.prerequisites("inference_partial")) == before - {"inference_expected"}
    assert validate_plan(report.plan).blocking == []


# ---------------------------------------------------------------- split_node


def test_split_node_replicates_wiring_reviewers_and_contract(jfc):
    report = apply(
        jfc,
        PlanEdit(op="split_node", node_id="selection", into=("selection_ee", "selection_mumu")),
    )
    plan = report.plan
    source = jfc.require_node("selection")

    assert plan.node("selection") is None
    for new_id in ("selection_ee", "selection_mumu"):
        node = plan.require_node(new_id)
        assert node.reviewers == source.reviewers
        assert node.contract == source.contract
        assert node.prompt == source.prompt
        # Every prerequisite the source had, each replica has.
        assert set(plan.prerequisites(new_id)) == set(jfc.prerequisites("selection"))

    # And everything that consumed the source now consumes both.
    assert {"selection_ee", "selection_mumu"} <= set(plan.prerequisites("inference_expected"))


def test_split_node_derives_distinct_directories(jfc):
    """Directories come from the ids, so replicas cannot collide on disk."""
    report = apply(
        jfc,
        PlanEdit(op="split_node", node_id="selection", into=("selection_ee", "selection_mumu")),
    )
    plan = report.plan
    assert plan.require_node("selection_ee").directory == "phase3_selection_ee"
    assert plan.require_node("selection_mumu").directory == "phase3_selection_mumu"
    assert validate_plan(plan).blocking == []


def test_split_node_keeps_the_sources_place_in_declaration_order(jfc):
    report = apply(
        jfc,
        PlanEdit(op="split_node", node_id="selection", into=("selection_ee", "selection_mumu")),
    )
    ids = list(report.plan.node_ids())
    assert ids[:4] == ["strategy", "exploration", "selection_ee", "selection_mumu"]


def test_split_node_needs_at_least_two_ids(jfc):
    report = apply(jfc, PlanEdit(op="split_node", node_id="selection", into=("only_one",)))
    assert "at least two" in report.skipped[0]


def test_split_node_rejects_duplicate_ids(jfc):
    report = apply(jfc, PlanEdit(op="split_node", node_id="selection", into=("a_ee", "a_ee")))
    assert "distinct" in report.skipped[0]


def test_split_node_rejects_ids_already_in_the_plan(jfc):
    report = apply(
        jfc, PlanEdit(op="split_node", node_id="selection", into=("selection_ee", "strategy"))
    )
    assert "already in the plan" in report.skipped[0]


def test_split_node_preserves_gates(jfc):
    """Splitting a gated node must not silently drop the gate."""
    report = apply(
        jfc,
        PlanEdit(
            op="split_node",
            node_id="inference_expected",
            into=("inference_expected_ee", "inference_expected_mumu"),
        ),
    )
    for new_id in ("inference_expected_ee", "inference_expected_mumu"):
        gates = report.plan.require_node(new_id).gates
        assert PlanGate(name="commitments", when="before") in gates


# ---------------------------------------------------------------- edge ops


def test_add_edge_defaults_to_a_blocking_full_injection(jfc):
    report = apply(jfc, PlanEdit(op="add_edge", upstream="exploration", downstream="documentation"))
    edge = report.plan.edges[-1]
    assert (edge.upstream, edge.downstream, edge.kind, edge.inject) == (
        "exploration",
        "documentation",
        "requires",
        "full",
    )


def test_add_edge_honours_kind_and_inject(jfc):
    report = apply(
        jfc,
        PlanEdit(
            op="add_edge",
            upstream="documentation",
            downstream="exploration",
            kind="informs",
            inject="summary",
        ),
    )
    edge = report.plan.edges[-1]
    assert (edge.kind, edge.inject) == ("informs", "summary")
    # An `informs` edge must not have created a cycle in the requires subgraph.
    assert validate_plan(report.plan).blocking == []


def test_add_edge_refuses_a_self_edge(jfc):
    report = apply(jfc, PlanEdit(op="add_edge", upstream="selection", downstream="selection"))
    assert "cannot depend on itself" in report.skipped[0]


def test_add_edge_refuses_a_duplicate(jfc):
    report = apply(jfc, PlanEdit(op="add_edge", upstream="strategy", downstream="exploration"))
    assert "already feeds" in report.skipped[0]


def test_add_edge_refuses_an_unknown_endpoint(jfc):
    report = apply(jfc, PlanEdit(op="add_edge", upstream="strategy", downstream="ghost"))
    assert "no node 'ghost'" in report.skipped[0]


def test_remove_edge(jfc):
    report = apply(jfc, PlanEdit(op="remove_edge", upstream="strategy", downstream="exploration"))
    assert not any(
        e.upstream == "strategy" and e.downstream == "exploration" for e in report.plan.edges
    )


def test_remove_edge_reports_a_missing_one(jfc):
    report = apply(jfc, PlanEdit(op="remove_edge", upstream="strategy", downstream="documentation"))
    # documentation does depend on strategy, so pick one that genuinely is absent
    assert report.applied or report.skipped
    report = apply(jfc, PlanEdit(op="remove_edge", upstream="documentation", downstream="strategy"))
    assert "no edge" in report.skipped[0]


# ---------------------------------------------------------------- set_prompt


def test_set_prompt_replaces_the_whole_prompt(jfc):
    report = apply(
        jfc, PlanEdit(op="set_prompt", node_id="exploration", prompt="Only look at the ee channel.")
    )
    assert report.plan.require_node("exploration").prompt == "Only look at the ee channel."
    assert "rewrote the prompt" in report.applied[0]


def test_set_prompt_refuses_to_empty_a_node(jfc):
    report = apply(jfc, PlanEdit(op="set_prompt", node_id="exploration", prompt=""))
    assert "nothing to do" in report.skipped[0]
    assert report.plan.require_node("exploration").prompt == jfc.require_node("exploration").prompt


# ---------------------------------------------------------------- end to end


def test_a_multichannel_proposal_produces_a_valid_plan(jfc):
    """The motivating case: per-channel exploration and selection, merged inference."""
    report = apply(
        jfc,
        PlanEdit(
            op="split_node", node_id="exploration", into=("exploration_ee", "exploration_mumu")
        ),
        PlanEdit(op="split_node", node_id="selection", into=("selection_ee", "selection_mumu")),
        PlanEdit(op="remove_edge", upstream="exploration_ee", downstream="selection_mumu"),
        PlanEdit(op="remove_edge", upstream="exploration_mumu", downstream="selection_ee"),
    )
    plan = report.plan
    assert report.skipped == []
    assert validate_plan(plan).blocking == []

    # Each channel's selection depends on its own exploration only.
    assert set(plan.prerequisites("selection_ee")) == {"strategy", "exploration_ee"}
    assert set(plan.prerequisites("selection_mumu")) == {"strategy", "exploration_mumu"}
    # Inference waits for both channels.
    assert {"selection_ee", "selection_mumu"} <= set(plan.prerequisites("inference_expected"))


def test_a_reinterpretation_proposal_drops_the_staged_unblinding(jfc):
    report = apply(
        jfc,
        PlanEdit(op="remove_node", node_id="inference_partial"),
        PlanEdit(op="remove_node", node_id="inference_observed"),
        PlanEdit(op="add_edge", upstream="inference_expected", downstream="documentation"),
    )
    plan = report.plan
    assert report.skipped == []
    assert validate_plan(plan).blocking == []
    assert list(plan.node_ids()) == [
        "strategy",
        "exploration",
        "selection",
        "inference_expected",
        "documentation",
    ]


def test_edits_never_mutate_the_input_plan(jfc):
    before = jfc.to_dict()
    apply(
        jfc,
        PlanEdit(op="split_node", node_id="selection", into=("selection_ee", "selection_mumu")),
        PlanEdit(op="remove_node", node_id="documentation"),
        PlanEdit(op="set_prompt", node_id="strategy", prompt="Something else."),
    )
    assert jfc.to_dict() == before


def test_an_empty_edit_list_returns_the_plan_unchanged():
    plan = make_plan()
    report = apply_edits(plan, [])
    assert report.plan is plan
    assert report.summary() == "0 edit(s) applied, 0 skipped"


def test_a_plan_edited_into_a_cycle_is_caught_by_the_validator(jfc):
    report = apply(jfc, PlanEdit(op="add_edge", upstream="documentation", downstream="strategy"))
    assert report.skipped == []
    rules = {f.rule for f in validate_plan(report.plan).blocking}
    assert "P4-acyclic" in rules


def test_edges_survive_a_split_of_both_endpoints(jfc):
    """Splitting two adjacent nodes fans the edge between them into four."""
    report = apply(
        jfc,
        PlanEdit(
            op="split_node", node_id="exploration", into=("exploration_ee", "exploration_mumu")
        ),
        PlanEdit(op="split_node", node_id="selection", into=("selection_ee", "selection_mumu")),
    )
    crossings = [
        (e.upstream, e.downstream)
        for e in report.plan.edges
        if e.upstream.startswith("exploration_") and e.downstream.startswith("selection_")
    ]
    assert len(crossings) == 4
    assert PlanEdge(upstream="exploration_ee", downstream="selection_ee") in report.plan.edges
