"""Built-in templates: discovery, prompt resolution, and instantiation."""

from __future__ import annotations

import pytest

from hepagent.plan.templates import (
    DEFAULT_TEMPLATE,
    TemplateNotFoundError,
    describe_templates,
    instantiate,
    list_templates,
    load_template,
)
from hepagent.plan.templates.registry import CONVENTIONS_FOR_TYPE, substitute

PROMPT = "# Physics Prompt\n\nMeasure the Z->bb cross section.\n"


def build(name=DEFAULT_TEMPLATE, analysis_type="measurement"):
    return instantiate(
        name, analysis_name="zbb", analysis_type=analysis_type, physics_prompt=PROMPT
    )


# ----------------------------------------------------------------- discovery


def test_the_shipped_templates_are_discoverable():
    assert set(list_templates()) == {"jfc-measurement", "jfc-search"}


def test_the_default_template_exists():
    assert DEFAULT_TEMPLATE in list_templates()


def test_every_template_describes_itself():
    assert all(description for _, description in describe_templates())


def test_an_unknown_template_names_what_is_available():
    with pytest.raises(TemplateNotFoundError, match="jfc-measurement"):
        load_template("nonexistent")


# ------------------------------------------------------------- instantiation


def test_instantiation_stamps_the_analysis_identity():
    plan = build()
    assert plan.name == "zbb"
    assert plan.template == "jfc-measurement"
    assert plan.analysis_type == "measurement"
    assert plan.problem == PROMPT
    assert plan.revision == 0


def test_prompts_are_inlined_not_left_as_references():
    plan = build()
    for node in plan.nodes:
        assert node.prompt, f"{node.id} has no prompt"
        assert "prompt_ref" not in node.to_dict()


def test_placeholders_are_substituted():
    plan = build()
    joined = "\n".join(node.prompt for node in plan.nodes)
    assert "{{" not in joined
    assert "conventions/unfolding.md" in joined


def test_the_search_template_carries_the_search_conventions():
    joined = "\n".join(node.prompt for node in build("jfc-search", "search").nodes)
    assert "conventions/search.md" in joined
    assert "conventions/unfolding.md" not in joined


def test_conventions_follow_the_analysis_type_not_the_template_name():
    """Instantiating the measurement template as a search still gets search
    conventions — the type is what the prompts branch on."""
    joined = "\n".join(node.prompt for node in build("jfc-measurement", "search").nodes)
    assert "conventions/search.md" in joined


def test_substitute_replaces_every_occurrence():
    assert substitute("{{a}}-{{a}}-{{b}}", {"a": "1", "b": "2"}) == "1-1-2"


def test_an_unknown_analysis_type_substitutes_an_empty_conventions_block():
    assert CONVENTIONS_FOR_TYPE.get("interpretation") is None
    plan = build(analysis_type="interpretation")
    assert "{{conventions_files}}" not in plan.node("strategy").prompt


# ------------------------------------------------- fidelity to the pipeline
#
# The template is now the only place this structure is written down, so these
# expectations are spelled out literally rather than compared against a second
# table. A template edit that changes the shipped pipeline has to change them.

JFC_ARTIFACTS = {
    "strategy": "phase1_strategy/outputs/STRATEGY.md",
    "exploration": "phase2_exploration/outputs/EXPLORATION.md",
    "selection": "phase3_selection/outputs/SELECTION.md",
    "inference_expected": "phase4a_inference_expected/outputs/INFERENCE_EXPECTED.md",
    "inference_partial": "phase4b_inference_partial/outputs/INFERENCE_PARTIAL.md",
    "inference_observed": "phase4c_inference_observed/outputs/INFERENCE_OBSERVED.md",
    "documentation": "phase5_documentation/outputs/ANALYSIS_NOTE_5_v1.md",
}

#: Paths cited by `data/methodology/` and `data/conventions/`, which is why the
#: shipped template keeps them even though a user may rename them.
JFC_PREREQUISITES = {
    "strategy": set(),
    "exploration": {"strategy"},
    "selection": {"strategy", "exploration"},
    "inference_expected": {"strategy", "exploration", "selection"},
    "inference_partial": {"strategy", "selection", "inference_expected"},
    "inference_observed": {"selection", "inference_expected", "inference_partial"},
    "documentation": {"strategy", "selection", "inference_observed"},
}


def test_the_default_template_writes_the_documented_artifacts():
    plan = build()
    assert {node.id: node.artifact_path for node in plan.nodes} == JFC_ARTIFACTS


def test_the_default_template_wires_the_documented_dependencies():
    plan = build()
    for node_id, expected in JFC_PREREQUISITES.items():
        assert set(plan.prerequisites(node_id)) == expected, node_id


def test_commitments_are_ambient_context_not_a_prerequisite():
    """`COMMITMENTS.md` is read by later nodes but produced by none of them.

    It lives in `context_paths`, not in an edge — an edge would make it look
    like work that has to complete first, and would put it in the `requires`
    subgraph that decides ordering.
    """
    plan = build()
    for node_id in ("inference_expected", "inference_partial", "inference_observed"):
        assert "COMMITMENTS.md" in plan.require_node(node_id).context_paths, node_id
    assert "COMMITMENTS.md" not in {node.artifact_path for node in plan.nodes}


def test_the_commitment_gate_sits_before_the_first_inference_node():
    node = build().require_node("inference_expected")
    assert [g.name for g in node.gates_at("before")] == ["commitments"]


def test_the_human_gate_sits_after_partial_unblinding():
    node = build().require_node("inference_partial")
    assert [g.name for g in node.gates_at("after")] == ["human"]


def test_the_codesign_gate_ships_disabled():
    """`--codesign` flips data rather than branching on a node id."""
    node = build().require_node("strategy")
    assert [g.name for g in node.gates] == ["codesign"]
    assert node.gates_at("after") == ()


def test_note_writing_nodes_are_the_inference_and_documentation_nodes():
    plan = build()
    assert {node.id for node in plan.nodes if node.produces_note} == {
        "inference_expected",
        "inference_partial",
        "inference_observed",
        "documentation",
    }


def test_the_declaring_node_is_the_only_one_that_may_write_commitments():
    """Exactly one node opens the commitment ledger; the rest close entries."""
    plan = build()
    openers = [n.id for n in plan.nodes if "commitment" in n.contract.node_types]
    assert openers == ["strategy"]


def test_every_node_may_write_back_something():
    for node in build().nodes:
        assert node.contract.node_types, node.id
        assert node.contract.edge_types, node.id


def test_every_declared_reviewer_is_one_the_review_gate_can_build():
    from hepagent.agents.jfc.reviewers import REVIEWER_NAMES

    for node in build().nodes:
        assert set(node.reviewers) <= REVIEWER_NAMES, node.id


def test_the_arbiter_adjudicates_the_nodes_with_the_widest_reviewer_panels():
    plan = build()
    assert {n.id for n in plan.nodes if n.arbiter} == {
        "strategy",
        "inference_expected",
        "inference_partial",
        "documentation",
    }


def test_the_role_catalogue_lists_every_reviewer_the_template_uses():
    """`data/agents/README.md` is shipped as the auditable role catalogue.

    It documents the default template, so a template that starts using a
    reviewer the catalogue does not mention has drifted from its own
    documentation.
    """
    from hepagent.agents.jfc._data import get_jfc_data_dir

    catalogue = (get_jfc_data_dir() / "agents" / "README.md").read_text(encoding="utf-8")
    assert "architect.md" in catalogue

    for node in build().nodes:
        for reviewer in node.reviewers:
            assert reviewer in catalogue.lower(), f"{node.id} uses '{reviewer}'"
