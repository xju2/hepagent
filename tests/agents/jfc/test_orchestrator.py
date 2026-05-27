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
