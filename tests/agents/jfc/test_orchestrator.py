"""Tests for JFC orchestration engine (mocked executor and reviewers)."""

from unittest.mock import AsyncMock, patch

import pytest


@pytest.fixture
def state_dir(tmp_path):
    root = tmp_path / "test_analysis"
    root.mkdir()
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
        (root / phase / "review").mkdir(parents=True)
    (root / "prompt.md").write_text("Test physics prompt")
    (root / "COMMITMENTS.md").write_text(
        "# Phase 1 Commitments\n\n"
        "| ID | Commitment | Status | Evidence | Phase Resolved |\n"
        "|----|-----------|--------|----------|---------------|\n"
        "| D1 | Test commitment | resolved | Evidence here | 1 |\n"
    )
    return root


def test_state_serialization(state_dir):
    from hepagent.agents.jfc.orchestrator import JFCOrchestrationState, load_state, save_state

    state = JFCOrchestrationState(
        analysis_root=str(state_dir),
        analysis_name="test",
        analysis_type="measurement",
        current_phase=2,
        current_subphase="2",
        completed_phases=["1"],
    )
    save_state(state)

    loaded = load_state(state_dir)
    assert loaded.analysis_name == "test"
    assert loaded.current_phase == 2
    assert "1" in loaded.completed_phases


def test_state_file_path(state_dir):
    from hepagent.agents.jfc.orchestrator import JFCOrchestrationState, save_state

    state = JFCOrchestrationState(
        analysis_root=str(state_dir),
        analysis_name="test",
        analysis_type="measurement",
    )
    save_state(state)
    assert (state_dir / ".orchestration_state.json").exists()


def test_phase_order():
    from hepagent.agents.jfc.orchestrator import PHASE_ORDER

    assert PHASE_ORDER[0] == 1
    assert PHASE_ORDER[-1] == 5
    assert "4a" in PHASE_ORDER
    assert "4b" in PHASE_ORDER
    assert "4c" in PHASE_ORDER


def test_max_iterations_exceeded_message():
    from hepagent.agents.jfc.orchestrator import MaxIterationsExceeded

    err = MaxIterationsExceeded("4a", 3)
    assert "4a" in str(err)
    assert "3" in str(err)


def test_git_commit_phase_no_crash(state_dir):
    from hepagent.agents.jfc.orchestrator import git_commit_phase

    # Should not raise even if git fails or state_dir has no .git
    git_commit_phase(state_dir, 1, "test message")


@pytest.mark.asyncio
async def test_run_phase_with_review_passes(state_dir):
    """Test that run_phase_with_review completes on PASS verdict."""
    from hepagent.agents.jfc.orchestrator import (
        JFCOrchestrationState,
        run_phase_with_review,
        save_state,
    )
    from hepagent.agents.jfc.review_gate import ReviewGateResult

    state = JFCOrchestrationState(
        analysis_root=str(state_dir),
        analysis_name="test",
        analysis_type="measurement",
    )
    save_state(state)

    mock_result = ReviewGateResult(verdict="PASS")

    with (
        patch(
            "hepagent.agents.jfc.orchestrator._run_executor", new_callable=AsyncMock
        ) as mock_exec,
        patch(
            "hepagent.agents.jfc.orchestrator.run_review_gate", new_callable=AsyncMock
        ) as mock_review,
    ):
        mock_review.return_value = mock_result

        await run_phase_with_review(state, 1)

        mock_exec.assert_called_once()
        mock_review.assert_called_once_with(
            1, state_dir, model_provider="cborg", model_name=None, max_turns=20
        )
        assert "1" in state.completed_phases


@pytest.mark.asyncio
async def test_run_phase_with_review_iterate_then_pass(state_dir):
    """Test that ITERATE cycles before PASS are handled."""
    from hepagent.agents.jfc.orchestrator import (
        JFCOrchestrationState,
        run_phase_with_review,
        save_state,
    )
    from hepagent.agents.jfc.review_gate import ReviewGateResult

    state = JFCOrchestrationState(
        analysis_root=str(state_dir),
        analysis_name="test",
        analysis_type="measurement",
        max_iterations_per_phase=3,
    )
    save_state(state)

    iterate_result = ReviewGateResult(verdict="ITERATE", category_a_findings=["Fix X"])
    pass_result = ReviewGateResult(verdict="PASS")

    call_count = 0

    async def mock_review(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        return pass_result if call_count >= 2 else iterate_result

    with (
        patch("hepagent.agents.jfc.orchestrator._run_executor", new_callable=AsyncMock),
        patch("hepagent.agents.jfc.orchestrator.run_review_gate", side_effect=mock_review),
        patch("hepagent.agents.jfc.orchestrator.run_fixer", new_callable=AsyncMock),
    ):
        await run_phase_with_review(state, 1)
        assert "1" in state.completed_phases
        assert call_count == 2


@pytest.mark.asyncio
async def test_regression_cycle_reruns_affected_phases(state_dir):
    """Test that _run_regression_cycle calls investigator and re-runs affected phases."""
    from hepagent.agents.jfc.investigator import RegressionTicket
    from hepagent.agents.jfc.orchestrator import (
        JFCOrchestrationState,
        _run_regression_cycle,
        save_state,
    )
    from hepagent.agents.jfc.review_gate import PhaseRegressionError, ReviewGateResult

    state = JFCOrchestrationState(
        analysis_root=str(state_dir),
        analysis_name="test_analysis",
        analysis_type="measurement",
        completed_phases=["1", "2", "3"],
    )
    save_state(state)

    regress_result = ReviewGateResult(
        verdict="REGRESS",
        regression_origin_phase=2,
        regression_symptom="wrong cut",
    )
    err = PhaseRegressionError("3", 2, "wrong cut", regress_result)

    ticket = RegressionTicket(
        detected_phase="3",
        origin_phase=2,
        symptom="wrong cut",
        affected_phases=[2, 3],
    )

    rerun_log: list[str] = []

    async def mock_run_phase(s, phase, cb=None, **kwargs):
        rerun_log.append(str(phase))
        s.completed_phases.append(str(phase))

    with (
        patch(
            "hepagent.agents.jfc.orchestrator.run_investigator",
            new_callable=AsyncMock,
            return_value=ticket,
        ),
        patch(
            "hepagent.agents.jfc.orchestrator.run_phase_with_review",
            side_effect=mock_run_phase,
        ),
    ):
        await _run_regression_cycle(state, err, None)

    # Phases 2 and 3 removed from completed, then re-run in order
    assert rerun_log == ["2", "3"]
    assert "1" in state.completed_phases  # unaffected phase preserved


@pytest.mark.asyncio
async def test_phase_run_ingests_into_the_graph(state_dir):
    """Executor output and the review round both get folded into the graph."""
    from hepagent.agents.jfc.orchestrator import (
        JFCOrchestrationState,
        run_phase_with_review,
        save_state,
    )
    from hepagent.agents.jfc.review_gate import ReviewGateResult

    state = JFCOrchestrationState(
        analysis_root=str(state_dir),
        analysis_name="test",
        analysis_type="measurement",
    )
    save_state(state)

    with (
        patch("hepagent.agents.jfc.orchestrator._run_executor", new_callable=AsyncMock),
        patch(
            "hepagent.agents.jfc.orchestrator.run_review_gate",
            new_callable=AsyncMock,
            return_value=ReviewGateResult(verdict="PASS"),
        ),
        patch("hepagent.agents.jfc.orchestrator.ingest_phase") as mock_phase,
        patch("hepagent.agents.jfc.orchestrator.ingest_review") as mock_review,
    ):
        await run_phase_with_review(state, 1)

    mock_phase.assert_called_once_with(state_dir, 1)
    mock_review.assert_called_once_with(state_dir, 1)


@pytest.mark.asyncio
async def test_graph_ingestion_failure_does_not_abort_the_run(state_dir):
    """Graph bookkeeping is not allowed to take down a phase that otherwise passed."""
    from hepagent.agents.jfc.orchestrator import (
        JFCOrchestrationState,
        run_phase_with_review,
        save_state,
    )
    from hepagent.agents.jfc.review_gate import ReviewGateResult

    state = JFCOrchestrationState(
        analysis_root=str(state_dir),
        analysis_name="test",
        analysis_type="measurement",
    )
    save_state(state)

    messages: list[str] = []

    with (
        patch("hepagent.agents.jfc.orchestrator._run_executor", new_callable=AsyncMock),
        patch(
            "hepagent.agents.jfc.orchestrator.run_review_gate",
            new_callable=AsyncMock,
            return_value=ReviewGateResult(verdict="PASS"),
        ),
        patch(
            "hepagent.agents.jfc.orchestrator.ingest_phase",
            side_effect=OSError("disk on fire"),
        ),
        patch("hepagent.agents.jfc.orchestrator.ingest_review"),
    ):
        await run_phase_with_review(state, 1, lambda _p, msg: messages.append(msg))

    assert "1" in state.completed_phases
    assert any("graph phase ingestion failed" in m for m in messages)


@pytest.mark.asyncio
async def test_review_is_ingested_even_when_the_gate_raises(state_dir):
    """An escalation is exactly when the provenance record matters most."""
    from hepagent.agents.jfc.orchestrator import (
        JFCOrchestrationState,
        run_phase_with_review,
        save_state,
    )
    from hepagent.agents.jfc.review_gate import PhaseEscalationError, ReviewGateResult

    state = JFCOrchestrationState(
        analysis_root=str(state_dir),
        analysis_name="test",
        analysis_type="measurement",
    )
    save_state(state)

    with (
        patch("hepagent.agents.jfc.orchestrator._run_executor", new_callable=AsyncMock),
        patch(
            "hepagent.agents.jfc.orchestrator.run_review_gate",
            new_callable=AsyncMock,
            side_effect=PhaseEscalationError(1, ReviewGateResult(verdict="ESCALATE")),
        ),
        patch("hepagent.agents.jfc.orchestrator.ingest_phase"),
        patch("hepagent.agents.jfc.orchestrator.ingest_review") as mock_review,
    ):
        with pytest.raises(PhaseEscalationError):
            await run_phase_with_review(state, 1)

    mock_review.assert_called_once_with(state_dir, 1)


def test_graph_commitment_findings_flag_a_commitment_with_no_evidence(state_dir):
    """A commitment recorded in the graph but never closed blocks Phase 4a."""
    from hepagent.agents.jfc.orchestrator import _graph_commitment_findings
    from hepagent.graph.schema import Node
    from hepagent.graph.store import AnalysisGraph

    graph = AnalysisGraph(state_dir)
    graph.ensure_dir()
    graph.add_node(Node(id="commitment:D9", type="commitment", label="D9 unclosed"))

    findings = _graph_commitment_findings(state_dir)
    assert any("D9 unclosed" in f for f in findings)


def test_graph_commitment_findings_are_empty_without_a_graph(state_dir):
    from hepagent.agents.jfc.orchestrator import _graph_commitment_findings

    assert _graph_commitment_findings(state_dir) == []


@pytest.mark.asyncio
async def test_max_iterations_exceeded(state_dir):
    """Test that MaxIterationsExceeded is raised after max iterations."""
    from hepagent.agents.jfc.orchestrator import (
        JFCOrchestrationState,
        MaxIterationsExceeded,
        run_phase_with_review,
        save_state,
    )
    from hepagent.agents.jfc.review_gate import ReviewGateResult

    state = JFCOrchestrationState(
        analysis_root=str(state_dir),
        analysis_name="test",
        analysis_type="measurement",
        max_iterations_per_phase=2,
    )
    save_state(state)

    iterate_result = ReviewGateResult(verdict="ITERATE", category_a_findings=["Bad thing"])

    with (
        patch("hepagent.agents.jfc.orchestrator._run_executor", new_callable=AsyncMock),
        patch(
            "hepagent.agents.jfc.orchestrator.run_review_gate", new_callable=AsyncMock
        ) as mock_review,
        patch("hepagent.agents.jfc.orchestrator.run_fixer", new_callable=AsyncMock),
    ):
        mock_review.return_value = iterate_result
        with pytest.raises(MaxIterationsExceeded) as exc_info:
            await run_phase_with_review(state, 2)
        assert exc_info.value.phase == 2


# ------------------------------------------------ frontier-driven traversal


def test_phases_before_declares_earlier_phases_satisfied():
    from hepagent.agents.jfc.orchestrator import _phases_before

    assert _phases_before("4a") == {"1", "2", "3"}
    assert _phases_before(1) == set()
    assert _phases_before("nonsense") == set()


def test_phase_sequence_follows_the_graph_frontier(state_dir):
    from hepagent.agents.jfc.graph_builder import bootstrap_graph
    from hepagent.agents.jfc.orchestrator import (
        JFCOrchestrationState,
        _phase_sequence,
    )

    (state_dir / "prompt.md").write_text("Test physics prompt")
    bootstrap_graph(state_dir, "test", "measurement", "Test physics prompt")

    state = JFCOrchestrationState(
        analysis_root=str(state_dir),
        analysis_name="test",
        analysis_type="measurement",
    )

    # Simulate each phase completing as it is yielded.
    emitted = []
    for phase in _phase_sequence(state_dir, state, set()):
        emitted.append(str(phase))
        state.completed_phases.append(str(phase))

    assert emitted == ["1", "2", "3", "4a", "4b", "4c", "5"]


def test_phase_sequence_yields_the_native_phase_type(state_dir):
    """Downstream code keys off int 1 and str '4a'; the planner returns strings."""
    from hepagent.agents.jfc.graph_builder import bootstrap_graph
    from hepagent.agents.jfc.orchestrator import JFCOrchestrationState, _phase_sequence

    (state_dir / "prompt.md").write_text("Test physics prompt")
    bootstrap_graph(state_dir, "test", "measurement", "Test physics prompt")
    state = JFCOrchestrationState(
        analysis_root=str(state_dir), analysis_name="test", analysis_type="measurement"
    )

    emitted = []
    for phase in _phase_sequence(state_dir, state, set()):
        emitted.append(phase)
        state.completed_phases.append(str(phase))

    assert emitted[0] == 1  # int, matches PHASE_SPECS keys
    assert "4a" in emitted  # str sub-phase preserved
    assert emitted[-1] == 5


def test_phase_sequence_skips_completed_phases(state_dir):
    from hepagent.agents.jfc.graph_builder import bootstrap_graph
    from hepagent.agents.jfc.orchestrator import JFCOrchestrationState, _phase_sequence

    (state_dir / "prompt.md").write_text("Test physics prompt")
    bootstrap_graph(state_dir, "test", "measurement", "Test physics prompt")
    state = JFCOrchestrationState(
        analysis_root=str(state_dir),
        analysis_name="test",
        analysis_type="measurement",
        completed_phases=["1", "2"],
    )

    emitted = []
    for phase in _phase_sequence(state_dir, state, set()):
        emitted.append(str(phase))
        state.completed_phases.append(str(phase))

    assert emitted == ["3", "4a", "4b", "4c", "5"]


def test_phase_sequence_honours_an_explicit_resume_point(state_dir):
    """Starting at 4a must not stall on phases 1-3 never having run."""
    from hepagent.agents.jfc.graph_builder import bootstrap_graph
    from hepagent.agents.jfc.orchestrator import (
        JFCOrchestrationState,
        _phase_sequence,
        _phases_before,
    )

    (state_dir / "prompt.md").write_text("Test physics prompt")
    bootstrap_graph(state_dir, "test", "measurement", "Test physics prompt")
    state = JFCOrchestrationState(
        analysis_root=str(state_dir), analysis_name="test", analysis_type="measurement"
    )

    emitted = []
    for phase in _phase_sequence(state_dir, state, _phases_before("4a")):
        emitted.append(str(phase))
        state.completed_phases.append(str(phase))

    assert emitted == ["4a", "4b", "4c", "5"]


def test_phase_sequence_terminates_when_a_phase_never_completes(state_dir):
    """A phase that runs without passing must not be offered forever."""
    from hepagent.agents.jfc.graph_builder import bootstrap_graph
    from hepagent.agents.jfc.orchestrator import JFCOrchestrationState, _phase_sequence

    (state_dir / "prompt.md").write_text("Test physics prompt")
    bootstrap_graph(state_dir, "test", "measurement", "Test physics prompt")
    state = JFCOrchestrationState(
        analysis_root=str(state_dir), analysis_name="test", analysis_type="measurement"
    )

    # Never mark anything complete: the loop must still end.
    emitted = [str(p) for p in _phase_sequence(state_dir, state, set())]
    assert emitted == ["1"]


def test_phase_sequence_falls_back_to_phase_order_without_a_graph(state_dir):
    from hepagent.agents.jfc.orchestrator import JFCOrchestrationState, _phase_sequence

    state = JFCOrchestrationState(
        analysis_root=str(state_dir), analysis_name="test", analysis_type="measurement"
    )

    emitted = []
    for phase in _phase_sequence(state_dir, state, set()):
        emitted.append(str(phase))
        state.completed_phases.append(str(phase))

    assert emitted == ["1", "2", "3", "4a", "4b", "4c", "5"]
