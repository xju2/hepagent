"""Tests for the process inventory: the strategy's machine-readable answer.

Three things are load-bearing and each is pinned here: the classification
vocabulary is closed, the file's location comes from the plan node rather than
from a phase name, and ingesting it into the graph is idempotent.
"""

from __future__ import annotations

import json

import pytest

from hepagent.agents.jfc.processes import (
    BACKGROUND_CATEGORIES,
    InventoryError,
    ProcessInventory,
    find_inventory,
    inventory_path,
    load_inventory,
    save_inventory,
)

VALID = {
    "processes": [
        {
            "id": "ggH",
            "label": "gg -> H -> tautau",
            "role": "signal",
            "importance": "unknown",
            "datasets": [
                {"name": "GluGluToHToTauTau.root", "path": "/data/ggH.root", "source": "prompt"}
            ],
        },
        {
            "id": "dy",
            "label": "Z/gamma* -> ll",
            "role": "background",
            "category": "irreducible",
            "importance": "dominant",
            "rationale": "same mu+tau_h final state",
            "estimation": "MC, normalised in a Z-enriched control region",
            "datasets": [{"name": "DYJetsToLL.root", "source": "prompt"}],
        },
        {
            "id": "wjets",
            "role": "background",
            "category": "instrumental",
            "importance": "major",
            "datasets": [{"name": "W1JetsToLNu.root", "source": "prompt"}],
        },
        {
            "id": "data",
            "role": "data",
            "datasets": [{"name": "Run2012B_SingleMu.root", "kind": "data", "source": "prompt"}],
        },
    ]
}


def parse(payload) -> ProcessInventory:
    return ProcessInventory.from_json(json.dumps(payload))


# ------------------------------------------------------------------- schema


def test_a_complete_inventory_validates():
    parse(VALID).validate()


def test_a_bare_list_of_processes_is_accepted():
    """The shorthand a model reaches for; refusing it would buy nothing."""
    assert parse(VALID["processes"]).problems() == []


def test_a_background_must_be_classified():
    payload = json.loads(json.dumps(VALID))
    payload["processes"][1].pop("category")
    problems = parse(payload).problems()
    assert any("irreducible" in p and "dy" in p for p in problems)


def test_an_invented_category_is_refused_with_the_three_that_exist():
    payload = json.loads(json.dumps(VALID))
    payload["processes"][1]["category"] = "electroweak"
    problems = parse(payload).problems()
    assert any(all(name in p for name in BACKGROUND_CATEGORIES) for p in problems)


def test_only_a_background_carries_a_category():
    """Categorising the signal would make the panel's grouping meaningless."""
    payload = json.loads(json.dumps(VALID))
    payload["processes"][0]["category"] = "irreducible"
    assert any("only a background" in p for p in parse(payload).problems())


def test_an_inventory_with_no_signal_is_refused():
    payload = {"processes": [p for p in VALID["processes"] if p["role"] != "signal"]}
    assert any("signal" in p for p in parse(payload).problems())


def test_a_process_with_no_dataset_is_refused():
    """The point of the inventory is what each process is *made of*."""
    payload = json.loads(json.dumps(VALID))
    payload["processes"][0]["datasets"] = []
    assert any("no dataset" in p for p in parse(payload).problems())


def test_a_duplicate_process_id_is_refused():
    payload = json.loads(json.dumps(VALID))
    payload["processes"].append(dict(payload["processes"][1]))
    assert any("appears 2 times" in p for p in parse(payload).problems())


def test_every_problem_is_reported_at_once():
    """One tool call gets one complete correction, not a round trip per row."""
    payload = {"processes": [{"id": "x", "role": "background"}]}
    problems = parse(payload).problems()
    assert len(problems) >= 3  # no category, no dataset, and no signal anywhere


def test_malformed_json_is_an_inventory_error_not_a_crash():
    with pytest.raises(InventoryError):
        ProcessInventory.from_json("{not json")


# -------------------------------------------------------------------- views


def test_the_inventory_groups_the_way_a_physicist_reads_it():
    inventory = parse(VALID)
    assert [p.id for p in inventory.by_role("signal")] == ["ggH"]
    assert [p.id for p in inventory.by_category("irreducible")] == ["dy"]
    assert [p.id for p in inventory.by_category("reducible")] == []
    assert [p.id for p in inventory.by_category("instrumental")] == ["wjets"]


def test_datasets_are_deduplicated_across_processes():
    payload = json.loads(json.dumps(VALID))
    payload["processes"][2]["datasets"].append({"name": "DYJetsToLL.root", "source": "prompt"})
    assert len(parse(payload).datasets()) == 4


# --------------------------------------------------------------------- disk


def test_the_file_location_comes_from_the_node_not_from_a_phase_name(jfc_plan, tmp_path):
    """A plan is free to rename its directories; nothing here may assume phase1."""
    node = jfc_plan.node("strategy")
    assert inventory_path(tmp_path, node) == tmp_path / node.outputs_dir / "processes.json"


def test_saving_stamps_the_node_that_recorded_it(jfc_analysis, jfc_plan):
    node = jfc_plan.node("strategy")
    save_inventory(jfc_analysis, node, parse(VALID))
    loaded = load_inventory(jfc_analysis, node)
    assert loaded is not None
    assert loaded.node_id == "strategy"
    assert loaded.updated_at
    assert [p.id for p in loaded.processes] == ["ggH", "dy", "wjets", "data"]


def test_a_half_written_file_reads_as_absent_rather_than_raising(jfc_analysis, jfc_plan):
    """This is read on the panel's request path; a truncated write must not 500."""
    node = jfc_plan.node("strategy")
    path = inventory_path(jfc_analysis, node)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"processes": [', encoding="utf-8")
    assert load_inventory(jfc_analysis, node) is None


def test_the_inventory_is_found_without_knowing_which_node_wrote_it(jfc_analysis, jfc_plan):
    save_inventory(jfc_analysis, jfc_plan.node("strategy"), parse(VALID))
    found, owner = find_inventory(jfc_analysis, jfc_plan)
    assert owner is not None and owner.id == "strategy"
    assert found is not None and len(found.processes) == 4


def test_no_inventory_is_no_error(jfc_analysis, jfc_plan):
    found, owner = find_inventory(jfc_analysis, jfc_plan)
    assert (found, owner) == (None, None)


# ---------------------------------------------------------------- ownership


def test_the_owner_is_the_node_allowed_to_create_processes(jfc_plan):
    """Ownership is read off the contract, never off a node id or phase number."""
    from hepagent.agents.jfc.processes import inventory_owner

    owner = inventory_owner(jfc_plan)
    assert owner is not None and owner.id == "strategy"


def test_the_owner_moves_with_the_contract_not_with_the_node_id(jfc_plan):
    """Hand the allowance to another node and the inventory moves with it."""
    import dataclasses

    from hepagent.agents.jfc.processes import inventory_owner

    def contract(node, types):
        return dataclasses.replace(
            node, contract=dataclasses.replace(node.contract, node_types=types)
        )

    moved = dataclasses.replace(
        jfc_plan,
        nodes=tuple(
            contract(node, ("commitment",))
            if node.id == "strategy"
            else contract(node, ("process", "dataset"))
            if node.id == "exploration"
            else node
            for node in jfc_plan.nodes
        ),
    )
    assert inventory_owner(moved).id == "exploration"
