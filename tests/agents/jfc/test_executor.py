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
