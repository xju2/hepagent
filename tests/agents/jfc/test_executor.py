"""Tests for JFC executor agent factories."""

import pytest


@pytest.fixture
def minimal_analysis_root(tmp_path):
    """Create a minimal analysis directory for testing."""
    root = tmp_path / "test_analysis"
    root.mkdir()
    (root / "prompt.md").write_text("# Physics Prompt\n\nTest prompt for Z boson measurement.\n")
    for phase in [
        "phase1_strategy",
        "phase2_exploration",
        "phase3_selection",
        "phase4a_inference_expected",
        "phase4b_inference_partial",
        "phase4c_inference_observed",
        "phase5_documentation",
    ]:
        (root / phase / "outputs").mkdir(parents=True)
        (root / phase / "src").mkdir(parents=True)
        (root / phase / "review").mkdir(parents=True)
    return root


def test_create_phase_executor_returns_agent(minimal_analysis_root):
    from agents import Agent
    from hepagent.agents.jfc.executor import create_phase_executor

    agent = create_phase_executor(1, minimal_analysis_root, model_provider="cborg")
    assert isinstance(agent, Agent)
    assert "Phase 1" in agent.name


def test_create_phase_executor_prompt_has_role_section(minimal_analysis_root):
    from hepagent.agents.jfc.executor import create_phase_executor

    agent = create_phase_executor(1, minimal_analysis_root, model_provider="cborg")
    # Instructions may be a string or callable
    instructions = agent.instructions
    if callable(instructions):
        # Skip for callable (dynamic) instructions
        return
    assert "WORKING DIRECTORY" in instructions or "Phase" in instructions


def test_create_phase_executor_prompt_has_physics_prompt(minimal_analysis_root):
    from hepagent.agents.jfc.executor import create_phase_executor

    agent = create_phase_executor(1, minimal_analysis_root, model_provider="cborg")
    instructions = agent.instructions
    if callable(instructions):
        return
    # prompt.md content should appear in instructions
    assert "Test prompt for Z boson" in instructions or "PHYSICS PROMPT" in instructions


def test_create_phase_executor_all_phases(minimal_analysis_root):
    from agents import Agent
    from hepagent.agents.jfc.executor import create_phase_executor

    for phase in [1, 2, 3, "4a", "4b", "4c", 5]:
        agent = create_phase_executor(phase, minimal_analysis_root)
        assert isinstance(agent, Agent)


def test_create_phase_executor_invalid_phase(minimal_analysis_root):
    from hepagent.agents.jfc.executor import create_phase_executor

    with pytest.raises(ValueError, match="Unknown phase"):
        create_phase_executor(99, minimal_analysis_root)


def test_create_note_writer_returns_agent(minimal_analysis_root):
    from agents import Agent
    from hepagent.agents.jfc.executor import create_note_writer

    agent = create_note_writer("4a", minimal_analysis_root)
    assert isinstance(agent, Agent)
    assert "Note Writer" in agent.name


def test_create_typesetter_returns_agent(minimal_analysis_root):
    from agents import Agent
    from hepagent.agents.jfc.executor import create_typesetter

    agent = create_typesetter(minimal_analysis_root)
    assert isinstance(agent, Agent)
    assert "Typesetter" in agent.name


def test_executor_prompt_carries_the_graph_write_back_contract(minimal_analysis_root):
    from hepagent.agents.jfc.executor import create_phase_executor

    agent = create_phase_executor(1, minimal_analysis_root)
    instructions = agent.instructions
    assert "GRAPH WRITE-BACK CONTRACT" in instructions
    # Phase 1 declares commitments; it does not produce figures.
    assert "commitment" in instructions
    assert "Phase 1 may create these node types" in instructions


def test_executor_prompt_contract_is_phase_specific(minimal_analysis_root):
    from hepagent.agents.jfc.executor import create_phase_executor

    phase2 = create_phase_executor(2, minimal_analysis_root).instructions
    assert "Phase 2 may create these node types" in phase2
    assert "dataset" in phase2


def test_executor_registers_the_graph_write_back_tools(minimal_analysis_root):
    from hepagent.agents.jfc.executor import create_phase_executor

    agent = create_phase_executor(1, minimal_analysis_root)
    names = {tool.name for tool in agent.tools}
    assert {"graph_add_node", "graph_add_edge", "graph_query"} <= names


# ------------------------------------------- M6: note generation from the graph


@pytest.fixture
def graphed_analysis(minimal_analysis_root):
    """An analysis with figures and results recorded in the graph."""
    import json

    from hepagent.agents.jfc.graph_builder import bootstrap_graph, ingest_phase

    root = minimal_analysis_root
    (root / "COMMITMENTS.md").write_text(
        "# Phase 1 Commitments\n\n"
        "| ID | Commitment | Status | Evidence | Phase Resolved |\n"
        "|----|-----------|--------|----------|---------------|\n"
        "| D1 | Unfold with IBU | resolved | closure chi2/ndf = 1.3/36 | 3 |\n"
        "| D2 | Generator comparison | pending | | |\n"
    )
    (root / "phase3_selection" / "outputs" / "SELECTION.md").write_text("selection")
    figures = root / "phase3_selection" / "outputs" / "figures"
    figures.mkdir(parents=True, exist_ok=True)
    (figures / "mjj.png").write_text("png")
    results = root / "phase3_selection" / "outputs" / "results"
    results.mkdir(parents=True, exist_ok=True)
    (results / "closure.json").write_text(json.dumps({"chi2": 1.3, "ndf": 36}))

    bootstrap_graph(root, "test_analysis", "measurement", "Test prompt")
    ingest_phase(root, 1)
    ingest_phase(root, 3)
    return root


def test_note_writer_prompt_carries_the_figure_manifest(graphed_analysis):
    from hepagent.agents.jfc.executor import create_note_writer

    instructions = create_note_writer("4a", graphed_analysis).instructions
    assert "WRITE FROM THE ANALYSIS GRAPH" in instructions
    assert "phase3_selection/outputs/figures/mjj.png" in instructions
    assert "Reference **only** figures listed in the manifest" in instructions


def test_note_writer_prompt_carries_authoritative_numbers(graphed_analysis):
    from hepagent.agents.jfc.executor import create_note_writer

    instructions = create_note_writer("4a", graphed_analysis).instructions
    assert "chi2 = 1.3" in instructions
    assert "ndf = 36" in instructions
    assert "the digest wins" in instructions


def test_note_writer_prompt_carries_the_commitment_ledger(graphed_analysis):
    from hepagent.agents.jfc.executor import create_note_writer

    instructions = create_note_writer("4a", graphed_analysis).instructions
    assert "commitment:D1" in instructions
    assert "still open" in instructions  # D2 must be named as an open issue


def test_note_writer_can_query_provenance_but_not_write(graphed_analysis):
    from hepagent.agents.jfc.executor import create_note_writer

    agent = create_note_writer("4a", graphed_analysis)
    names = {tool.name for tool in agent.tools}
    assert "graph_query" in names
    assert "graph_add_node" not in names
    assert "execute_bash_command_with_confirmation" not in names


def test_note_writer_prompt_omits_the_graph_section_when_there_is_none(minimal_analysis_root):
    from hepagent.agents.jfc.executor import create_note_writer

    instructions = create_note_writer("4a", minimal_analysis_root).instructions
    assert "WRITE FROM THE ANALYSIS GRAPH" not in instructions
