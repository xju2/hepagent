"""End to end for the process inventory: tool -> file -> graph -> panel.

`test_processes.py` covers the document itself. This covers what the document is
*for*: it is recorded by a bounded tool, ingested into the provenance graph,
injected into the nodes that consume it, and rendered by the progress panel.
"""

from __future__ import annotations

import json

import pytest

from hepagent.agents.jfc.graph_builder import ingest_node
from hepagent.agents.jfc.processes import ProcessInventory, save_inventory
from hepagent.agents.jfc.state import analysis_state
from hepagent.graph.store import AnalysisGraph
from hepagent.plan.store import save_plan
from hepagent.tools.jfc.processes import read_process_inventory, record_process_inventory

INVENTORY = {
    "processes": [
        {
            "id": "ggH",
            "label": "gg -> H -> tautau",
            "role": "signal",
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
            "datasets": [{"name": "DYJetsToLL.root", "source": "prompt"}],
        },
        {
            "id": "wjets",
            "role": "background",
            "category": "instrumental",
            "importance": "major",
            "datasets": [{"name": "W1JetsToLNu.root", "source": "prompt"}],
        },
    ]
}


def call(tool, **kwargs) -> str:
    """Invoke a function tool the way the SDK does."""
    import asyncio

    return asyncio.run(tool.on_invoke_tool(None, json.dumps(kwargs)))


@pytest.fixture
def parsed() -> ProcessInventory:
    return ProcessInventory.from_json(json.dumps(INVENTORY))


# --------------------------------------------------------------------- tool


def test_recording_writes_the_file_and_reports_what_it_recorded(jfc_analysis):
    result = call(
        record_process_inventory,
        analysis_root=str(jfc_analysis),
        node_id="strategy",
        inventory_json=json.dumps(INVENTORY),
    )
    assert not result.startswith("Error:")
    assert "1 signal, 2 background" in result
    assert (jfc_analysis / "phase1_strategy/outputs/processes.json").is_file()


def test_an_invalid_inventory_is_refused_with_every_reason_and_the_vocabulary(jfc_analysis):
    result = call(
        record_process_inventory,
        analysis_root=str(jfc_analysis),
        node_id="strategy",
        inventory_json=json.dumps({"processes": [{"id": "dy", "role": "background"}]}),
    )
    assert result.startswith("Error:")
    assert "irreducible, reducible, instrumental" in result
    assert not (jfc_analysis / "phase1_strategy/outputs/processes.json").exists()


def test_a_node_whose_contract_forbids_it_may_not_record_one(jfc_analysis, jfc_plan):
    """Recording an inventory creates process and dataset graph nodes, so the
    same contract that bounds `graph_add_node` has to bound this."""
    import dataclasses

    node = jfc_plan.node("strategy")
    narrowed = dataclasses.replace(
        node, contract=dataclasses.replace(node.contract, node_types=("commitment",))
    )
    save_plan(
        jfc_analysis,
        dataclasses.replace(
            jfc_plan,
            nodes=tuple(narrowed if n.id == "strategy" else n for n in jfc_plan.nodes),
        ),
    )
    result = call(
        record_process_inventory,
        analysis_root=str(jfc_analysis),
        node_id="strategy",
        inventory_json=json.dumps(INVENTORY),
    )
    assert result.startswith("Error:")
    assert "process" in result and "dataset" in result


def test_an_unknown_node_names_the_ones_that_exist(jfc_analysis):
    result = call(
        record_process_inventory,
        analysis_root=str(jfc_analysis),
        node_id="phase1",
        inventory_json=json.dumps(INVENTORY),
    )
    assert result.startswith("Error:") and "strategy" in result


def test_reading_it_back_needs_no_node_id(jfc_analysis, jfc_plan, parsed):
    """A consumer knows the analysis, not which node happens to own the file."""
    save_inventory(jfc_analysis, jfc_plan.node("strategy"), parsed)
    result = call(read_process_inventory, analysis_root=str(jfc_analysis))
    assert "background/irreducible" in result
    assert "DYJetsToLL.root" in result


def test_reading_before_the_strategy_ran_says_so_rather_than_failing(jfc_analysis):
    result = call(read_process_inventory, analysis_root=str(jfc_analysis))
    assert "No process inventory" in result


# -------------------------------------------------------------------- graph


def test_ingestion_records_each_process_with_its_classification(jfc_analysis, jfc_plan, parsed):
    save_inventory(jfc_analysis, jfc_plan.node("strategy"), parsed)
    ingest_node(jfc_analysis, "strategy")

    graph = AnalysisGraph.load(jfc_analysis)
    processes = {n.id: n for n in graph.nodes(type="process")}
    assert set(processes) == {"process:ggH", "process:dy", "process:wjets"}
    assert processes["process:dy"].metadata["category"] == "irreducible"
    assert processes["process:wjets"].metadata["role"] == "background"
    assert processes["process:ggH"].phase == "strategy"


def test_a_process_requires_the_dataset_that_carries_it(jfc_analysis, jfc_plan, parsed):
    save_inventory(jfc_analysis, jfc_plan.node("strategy"), parsed)
    ingest_node(jfc_analysis, "strategy")

    graph = AnalysisGraph.load(jfc_analysis)
    edges = graph.out_edges("process:dy", type="requires")
    assert [e.dst for e in edges] == ["dataset:DYJetsToLL.root"]
    assert graph.get_node("dataset:DYJetsToLL.root").metadata["source"] == "prompt"


def test_ingesting_twice_changes_nothing(jfc_analysis, jfc_plan, parsed):
    """Idempotent ingestion is the invariant the whole graph layer rests on."""
    save_inventory(jfc_analysis, jfc_plan.node("strategy"), parsed)
    ingest_node(jfc_analysis, "strategy")
    first = (jfc_analysis / "graph/nodes.jsonl").read_text(encoding="utf-8")
    ingest_node(jfc_analysis, "strategy")
    assert (jfc_analysis / "graph/nodes.jsonl").read_text(encoding="utf-8") == first


def test_an_invalid_file_on_disk_is_skipped_rather_than_half_ingested(jfc_analysis, jfc_plan):
    node = jfc_plan.node("strategy")
    path = jfc_analysis / node.outputs_dir / "processes.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"processes": [{"id": "dy", "role": "background"}]}), "utf-8")

    report = ingest_node(jfc_analysis, "strategy")
    assert any("processes.json" in line for line in report.skipped)
    assert AnalysisGraph.load(jfc_analysis).nodes(type="process") == []


# ------------------------------------------------------------------- prompt


def test_the_owning_node_is_told_to_record_the_inventory(jfc_analysis, jfc_plan):
    from hepagent.agents.jfc.executor import _assemble_executor_prompt

    prompt = _assemble_executor_prompt(jfc_plan.node("strategy"), jfc_plan, jfc_analysis)
    assert "record_process_inventory" in prompt
    assert "irreducible, reducible, instrumental" in prompt


def test_a_downstream_node_is_handed_the_inventory_that_exists(jfc_analysis, jfc_plan, parsed):
    """The whole point of writing it down once: nobody re-derives it from prose."""
    from hepagent.agents.jfc.executor import _assemble_executor_prompt

    save_inventory(jfc_analysis, jfc_plan.node("strategy"), parsed)
    prompt = _assemble_executor_prompt(jfc_plan.node("selection"), jfc_plan, jfc_analysis)
    assert "PROCESS INVENTORY" in prompt
    assert "background/irreducible" in prompt
    assert "record_process_inventory" not in prompt


def test_a_downstream_node_says_nothing_before_the_strategy_has_run(jfc_analysis, jfc_plan):
    from hepagent.agents.jfc.executor import _assemble_executor_prompt

    prompt = _assemble_executor_prompt(jfc_plan.node("selection"), jfc_plan, jfc_analysis)
    assert "PROCESS INVENTORY" not in prompt


# -------------------------------------------------------------------- panel


def test_the_panel_reports_no_inventory_before_the_strategy_records_one(jfc_analysis, jfc_plan):
    state = analysis_state(jfc_analysis, jfc_plan)
    assert state["processes"]["recorded"] is False
    # Every section is still named, so "none identified" is visible as a claim.
    assert [s["name"] for s in state["processes"]["sections"]] == [
        "signal",
        "irreducible",
        "reducible",
        "instrumental",
        "data",
    ]


def test_the_panel_groups_the_recorded_processes(jfc_analysis, jfc_plan, parsed):
    save_inventory(jfc_analysis, jfc_plan.node("strategy"), parsed)
    state = analysis_state(jfc_analysis, jfc_plan)
    sections = {s["name"]: s for s in state["processes"]["sections"]}
    assert state["processes"]["recorded"] is True
    assert state["processes"]["node_id"] == "strategy"
    assert [p["id"] for p in sections["signal"]["processes"]] == ["ggH"]
    assert [p["id"] for p in sections["irreducible"]["processes"]] == ["dy"]
    assert sections["reducible"]["processes"] == []
    assert len(state["processes"]["datasets"]) == 3


def test_the_panel_reports_which_steps_have_produced_their_artifact(jfc_analysis, jfc_plan):
    node = jfc_plan.node("strategy")
    (jfc_analysis / node.outputs_dir).mkdir(parents=True, exist_ok=True)
    (jfc_analysis / node.artifact_path).write_text("# Strategy\n", encoding="utf-8")

    rows = {row["id"]: row for row in analysis_state(jfc_analysis, jfc_plan)["nodes"]}
    assert rows["strategy"]["produced"] is True
    assert rows["selection"]["produced"] is False
    assert rows["strategy"]["artifact"] == node.artifact_path
