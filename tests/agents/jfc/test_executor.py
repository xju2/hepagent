"""Tests for JFC executor agent factories."""

import dataclasses

import pytest

from hepagent.plan.schema import PlanEdge
from hepagent.plan.store import save_plan


@pytest.fixture
def minimal_analysis_root(tmp_path, jfc_plan):
    """A minimal analysis directory: the plan, the prompt, and empty node trees."""
    root = tmp_path / "test_analysis"
    root.mkdir()
    (root / "prompt.md").write_text(f"# Physics Prompt\n\n{jfc_plan.problem}\n")
    for node in jfc_plan.nodes:
        for sub in ("outputs", "src", "review"):
            (root / node.directory / sub).mkdir(parents=True)
    save_plan(root, jfc_plan)
    return root


def node(plan, node_id):
    return plan.require_node(node_id)


def test_create_phase_executor_returns_agent(minimal_analysis_root, jfc_plan):
    from agents import Agent
    from hepagent.agents.jfc.executor import create_phase_executor

    agent = create_phase_executor(
        node(jfc_plan, "strategy"), minimal_analysis_root, model_provider="cborg"
    )
    assert isinstance(agent, Agent)
    assert "Strategy" in agent.name


def test_executor_prompt_carries_the_nodes_own_prompt(minimal_analysis_root, jfc_plan):
    """The node's prompt — the editable field — is what the executor is told to do."""
    from hepagent.agents.jfc.executor import create_phase_executor

    strategy = node(jfc_plan, "strategy")
    agent = create_phase_executor(strategy, minimal_analysis_root)
    assert strategy.prompt.strip()[:60] in agent.instructions


def test_executor_prompt_carries_the_working_directory_and_artifact(
    minimal_analysis_root, jfc_plan
):
    from hepagent.agents.jfc.executor import create_phase_executor

    selection = node(jfc_plan, "selection")
    instructions = create_phase_executor(selection, minimal_analysis_root).instructions
    assert selection.directory in instructions
    assert selection.artifact in instructions


def test_executor_prompt_has_physics_prompt(minimal_analysis_root, jfc_plan):
    from hepagent.agents.jfc.executor import create_phase_executor

    agent = create_phase_executor(node(jfc_plan, "strategy"), minimal_analysis_root)
    assert jfc_plan.problem.splitlines()[0] in agent.instructions


def test_create_phase_executor_for_every_node(minimal_analysis_root, jfc_plan):
    from agents import Agent
    from hepagent.agents.jfc.executor import create_phase_executor

    for plan_node in jfc_plan.nodes:
        assert isinstance(create_phase_executor(plan_node, minimal_analysis_root), Agent)


def test_create_note_writer_returns_agent(minimal_analysis_root, jfc_plan):
    from agents import Agent
    from hepagent.agents.jfc.executor import create_note_writer

    agent = create_note_writer(node(jfc_plan, "inference_expected"), minimal_analysis_root)
    assert isinstance(agent, Agent)
    assert "Note Writer" in agent.name


def test_create_typesetter_returns_agent(minimal_analysis_root):
    from agents import Agent
    from hepagent.agents.jfc.executor import create_typesetter

    agent = create_typesetter(minimal_analysis_root)
    assert isinstance(agent, Agent)
    assert "Typesetter" in agent.name


# ------------------------------------------------------------- upstream context


def test_executor_reads_the_artifacts_its_edges_declare(minimal_analysis_root, jfc_plan):
    from hepagent.agents.jfc.executor import create_phase_executor

    strategy = node(jfc_plan, "strategy")
    (minimal_analysis_root / strategy.artifact_path).write_text("FIDUCIAL VOLUME: |y| < 2.5")

    instructions = create_phase_executor(
        node(jfc_plan, "exploration"), minimal_analysis_root
    ).instructions
    assert "FIDUCIAL VOLUME: |y| < 2.5" in instructions


def test_an_edge_marked_inject_none_keeps_its_artifact_out_of_the_prompt(
    minimal_analysis_root, jfc_plan
):
    """`inject` controls prompt assembly independently of ordering."""
    from hepagent.agents.jfc.executor import create_phase_executor

    strategy = node(jfc_plan, "strategy")
    (minimal_analysis_root / strategy.artifact_path).write_text("SECRET FIDUCIAL DEFINITION")

    quiet = dataclasses.replace(
        jfc_plan,
        edges=tuple(
            dataclasses.replace(e, inject="none")
            if (e.upstream, e.downstream) == ("strategy", "exploration")
            else e
            for e in jfc_plan.edges
        ),
    )
    instructions = create_phase_executor(
        node(quiet, "exploration"), minimal_analysis_root, plan=quiet
    ).instructions
    assert "SECRET FIDUCIAL DEFINITION" not in instructions


def test_an_informs_edge_still_reaches_the_prompt(minimal_analysis_root, jfc_plan):
    """`informs` does not constrain order, but it does supply context."""
    from hepagent.agents.jfc.executor import create_phase_executor

    documentation = node(jfc_plan, "documentation")
    (minimal_analysis_root / documentation.artifact_path).write_text("PRIOR NOTE TEXT")

    plan = dataclasses.replace(
        jfc_plan,
        edges=jfc_plan.edges
        + (PlanEdge(upstream="documentation", downstream="exploration", kind="informs"),),
    )
    instructions = create_phase_executor(
        node(plan, "exploration"), minimal_analysis_root, plan=plan
    ).instructions
    assert "PRIOR NOTE TEXT" in instructions


def test_context_paths_are_injected_even_though_no_node_produces_them(
    minimal_analysis_root, jfc_plan
):
    from hepagent.agents.jfc.executor import create_phase_executor

    (minimal_analysis_root / "COMMITMENTS.md").write_text("| D1 | Unfold with IBU | pending | | |")
    instructions = create_phase_executor(
        node(jfc_plan, "inference_expected"), minimal_analysis_root
    ).instructions
    assert "Unfold with IBU" in instructions


# ------------------------------------------------------------ write-back contract


def test_executor_prompt_carries_the_graph_write_back_contract(minimal_analysis_root, jfc_plan):
    from hepagent.agents.jfc.executor import create_phase_executor

    instructions = create_phase_executor(
        node(jfc_plan, "strategy"), minimal_analysis_root
    ).instructions
    assert "GRAPH WRITE-BACK CONTRACT" in instructions
    # The strategy node declares commitments; it does not produce figures.
    assert "commitment" in instructions
    assert "Node 'strategy' may create these node types" in instructions


def test_executor_prompt_contract_is_node_specific(minimal_analysis_root, jfc_plan):
    from hepagent.agents.jfc.executor import create_phase_executor

    instructions = create_phase_executor(
        node(jfc_plan, "exploration"), minimal_analysis_root
    ).instructions
    assert "Node 'exploration' may create these node types" in instructions
    assert "dataset" in instructions


def test_executor_registers_the_graph_write_back_tools(minimal_analysis_root, jfc_plan):
    from hepagent.agents.jfc.executor import create_phase_executor

    agent = create_phase_executor(node(jfc_plan, "strategy"), minimal_analysis_root)
    names = {tool.name for tool in agent.tools}
    assert {"graph_add_node", "graph_add_edge", "graph_query"} <= names


def test_a_node_model_override_wins_over_the_run_wide_model(minimal_analysis_root, jfc_plan):
    """A plan may pin one node to a stronger model without changing the run."""
    from hepagent.agents.jfc.executor import create_phase_executor

    pinned = dataclasses.replace(node(jfc_plan, "strategy"), model="openai:gpt-5-mini")
    plan = dataclasses.replace(
        jfc_plan, nodes=(pinned,) + tuple(n for n in jfc_plan.nodes if n.id != "strategy")
    )
    agent = create_phase_executor(pinned, minimal_analysis_root, plan=plan, model_provider="cborg")
    assert "gpt-5-mini" in str(agent.model.model)


# ------------------------------------------- note generation from the graph


@pytest.fixture
def graphed_analysis(minimal_analysis_root, jfc_plan):
    """An analysis with figures and results recorded in the graph."""
    import json

    from hepagent.agents.jfc.graph_builder import bootstrap_graph, ingest_node

    root = minimal_analysis_root
    (root / "COMMITMENTS.md").write_text(
        "# Analysis Commitments\n\n"
        "| ID | Commitment | Status | Evidence | Phase Resolved |\n"
        "|----|-----------|--------|----------|---------------|\n"
        "| D1 | Unfold with IBU | resolved | closure chi2/ndf = 1.3/36 | 3 |\n"
        "| D2 | Generator comparison | pending | | |\n"
    )
    selection = node(jfc_plan, "selection")
    (root / selection.artifact_path).write_text("selection")
    figures = root / selection.outputs_dir / "figures"
    figures.mkdir(parents=True, exist_ok=True)
    (figures / "mjj.png").write_text("png")
    results = root / selection.outputs_dir / "results"
    results.mkdir(parents=True, exist_ok=True)
    (results / "closure.json").write_text(json.dumps({"chi2": 1.3, "ndf": 36}))

    bootstrap_graph(root, jfc_plan)
    ingest_node(root, "strategy")
    ingest_node(root, "selection")
    return root


def test_note_writer_prompt_carries_the_figure_manifest(graphed_analysis, jfc_plan):
    from hepagent.agents.jfc.executor import create_note_writer

    instructions = create_note_writer(
        node(jfc_plan, "inference_expected"), graphed_analysis
    ).instructions
    assert "WRITE FROM THE ANALYSIS GRAPH" in instructions
    assert "phase3_selection/outputs/figures/mjj.png" in instructions
    assert "Reference **only** figures listed in the manifest" in instructions


def test_note_writer_prompt_carries_authoritative_numbers(graphed_analysis, jfc_plan):
    from hepagent.agents.jfc.executor import create_note_writer

    instructions = create_note_writer(
        node(jfc_plan, "inference_expected"), graphed_analysis
    ).instructions
    assert "chi2 = 1.3" in instructions
    assert "ndf = 36" in instructions
    assert "the digest wins" in instructions


def test_note_writer_prompt_carries_the_commitment_ledger(graphed_analysis, jfc_plan):
    from hepagent.agents.jfc.executor import create_note_writer

    instructions = create_note_writer(
        node(jfc_plan, "inference_expected"), graphed_analysis
    ).instructions
    assert "commitment:D1" in instructions
    assert "still open" in instructions  # D2 must be named as an open issue


def test_note_writer_writes_to_the_nodes_note_artifact(graphed_analysis, jfc_plan):
    """A node whose note differs from its artifact must not overwrite the artifact."""
    from hepagent.agents.jfc.executor import create_note_writer

    inference = node(jfc_plan, "inference_expected")
    assert inference.note_path != inference.artifact_path
    instructions = create_note_writer(inference, graphed_analysis).instructions
    assert inference.note_path in instructions


def test_note_writer_can_query_provenance_but_not_write(graphed_analysis, jfc_plan):
    from hepagent.agents.jfc.executor import create_note_writer

    agent = create_note_writer(node(jfc_plan, "inference_expected"), graphed_analysis)
    names = {tool.name for tool in agent.tools}
    assert "graph_query" in names
    assert "graph_add_node" not in names
    assert "execute_bash_command_with_confirmation" not in names


def test_note_writer_prompt_omits_the_graph_section_when_there_is_none(
    minimal_analysis_root, jfc_plan
):
    from hepagent.agents.jfc.executor import create_note_writer

    instructions = create_note_writer(
        node(jfc_plan, "inference_expected"), minimal_analysis_root
    ).instructions
    assert "WRITE FROM THE ANALYSIS GRAPH" not in instructions
