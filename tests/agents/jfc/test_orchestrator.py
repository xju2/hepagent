"""Tests for JFC orchestration engine (mocked executor and reviewers)."""

import dataclasses
import json
from unittest.mock import AsyncMock, patch

import pytest

from hepagent.plan.store import save_plan


@pytest.fixture
def state_dir(tmp_path, jfc_plan):
    root = tmp_path / "test_analysis"
    root.mkdir()
    for node in jfc_plan.nodes:
        (root / node.directory / "outputs").mkdir(parents=True)
        (root / node.directory / "review").mkdir(parents=True)
    (root / "prompt.md").write_text(jfc_plan.problem)
    (root / "COMMITMENTS.md").write_text(
        "# Analysis Commitments\n\n"
        "| ID | Commitment | Status | Evidence | Phase Resolved |\n"
        "|----|-----------|--------|----------|---------------|\n"
        "| D1 | Test commitment | resolved | Evidence here | strategy |\n"
    )
    save_plan(root, jfc_plan)
    return root


def make_state(state_dir, **overrides):
    from hepagent.agents.jfc.orchestrator import JFCOrchestrationState

    payload = {
        "analysis_root": str(state_dir),
        "analysis_name": "test",
        "analysis_type": "measurement",
    }
    payload.update(overrides)
    return JFCOrchestrationState(**payload)


def node_of(plan, node_id):
    return plan.require_node(node_id)


# ------------------------------------------------------------------- state


def test_state_serialization(state_dir):
    from hepagent.agents.jfc.orchestrator import load_state, save_state

    state = make_state(state_dir, current_node="exploration", completed_nodes=["strategy"])
    save_state(state)

    loaded = load_state(state_dir)
    assert loaded.analysis_name == "test"
    assert loaded.current_node == "exploration"
    assert "strategy" in loaded.completed_nodes


def test_state_drops_keys_it_no_longer_knows(state_dir):
    """A state written by an older version must still load, minus what went away."""
    from hepagent.agents.jfc.orchestrator import JFCOrchestrationState

    raw = json.dumps(
        {
            "analysis_root": str(state_dir),
            "analysis_name": "old",
            "analysis_type": "measurement",
            "current_phase": 2,
            "current_subphase": "2",
            "completed_nodes": ["strategy"],
        }
    )
    state = JFCOrchestrationState.model_validate_json(raw)
    assert state.completed_nodes == ["strategy"]
    assert not hasattr(state, "current_subphase")


def test_state_file_path(state_dir):
    from hepagent.agents.jfc.orchestrator import save_state

    save_state(make_state(state_dir))
    assert (state_dir / ".orchestration_state.json").exists()


def test_max_iterations_exceeded_message():
    from hepagent.agents.jfc.orchestrator import MaxIterationsExceeded

    err = MaxIterationsExceeded("inference_expected", 3)
    assert "inference_expected" in str(err)
    assert "3" in str(err)


def test_git_commit_phase_no_crash(state_dir):
    from hepagent.agents.jfc.orchestrator import git_commit_phase

    # Should not raise even if git fails or state_dir has no .git
    git_commit_phase(state_dir, "strategy", "test message")


# ------------------------------------------------------------ node execution


@pytest.mark.asyncio
async def test_run_phase_with_review_passes(state_dir, jfc_plan):
    """run_phase_with_review completes on a PASS verdict."""
    from hepagent.agents.jfc.orchestrator import run_phase_with_review, save_state
    from hepagent.agents.jfc.review_gate import ReviewGateResult

    state = make_state(state_dir)
    save_state(state)
    strategy = node_of(jfc_plan, "strategy")

    with (
        patch(
            "hepagent.agents.jfc.orchestrator._run_executor", new_callable=AsyncMock
        ) as mock_exec,
        patch(
            "hepagent.agents.jfc.orchestrator.run_review_gate", new_callable=AsyncMock
        ) as mock_review,
    ):
        mock_review.return_value = ReviewGateResult(verdict="PASS")

        await run_phase_with_review(state, strategy, jfc_plan)

        mock_exec.assert_called_once()
        mock_review.assert_called_once_with(
            strategy, state_dir, jfc_plan, model_provider="cborg", model_name=None, max_turns=20
        )
        assert "strategy" in state.completed_nodes


@pytest.mark.asyncio
async def test_run_phase_with_review_iterate_then_pass(state_dir, jfc_plan):
    from hepagent.agents.jfc.orchestrator import run_phase_with_review, save_state
    from hepagent.agents.jfc.review_gate import ReviewGateResult

    state = make_state(state_dir, max_iterations_per_phase=3)
    save_state(state)

    call_count = 0

    async def mock_review(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count >= 2:
            return ReviewGateResult(verdict="PASS")
        return ReviewGateResult(verdict="ITERATE", category_a_findings=["Fix X"])

    with (
        patch("hepagent.agents.jfc.orchestrator._run_executor", new_callable=AsyncMock),
        patch("hepagent.agents.jfc.orchestrator.run_review_gate", side_effect=mock_review),
        patch("hepagent.agents.jfc.orchestrator.run_fixer", new_callable=AsyncMock),
    ):
        await run_phase_with_review(state, node_of(jfc_plan, "strategy"), jfc_plan)
        assert "strategy" in state.completed_nodes
        assert call_count == 2


@pytest.mark.asyncio
async def test_a_node_may_ask_for_more_iterations_than_the_run_default(state_dir, jfc_plan):
    """`max_iterations` is per node: a cheap node can be given more attempts."""
    from hepagent.agents.jfc.orchestrator import (
        MaxIterationsExceeded,
        run_phase_with_review,
        save_state,
    )
    from hepagent.agents.jfc.review_gate import ReviewGateResult

    state = make_state(state_dir, max_iterations_per_phase=1)
    save_state(state)
    patient = dataclasses.replace(node_of(jfc_plan, "strategy"), max_iterations=4)

    attempts = 0

    async def always_iterate(*args, **kwargs):
        nonlocal attempts
        attempts += 1
        return ReviewGateResult(verdict="ITERATE", category_a_findings=["nope"])

    with (
        patch("hepagent.agents.jfc.orchestrator._run_executor", new_callable=AsyncMock),
        patch("hepagent.agents.jfc.orchestrator.run_review_gate", side_effect=always_iterate),
        patch("hepagent.agents.jfc.orchestrator.run_fixer", new_callable=AsyncMock),
        pytest.raises(MaxIterationsExceeded),
    ):
        await run_phase_with_review(state, patient, jfc_plan)

    assert attempts == 4


@pytest.mark.asyncio
async def test_max_iterations_exceeded(state_dir, jfc_plan):
    from hepagent.agents.jfc.orchestrator import (
        MaxIterationsExceeded,
        run_phase_with_review,
        save_state,
    )
    from hepagent.agents.jfc.review_gate import ReviewGateResult

    state = make_state(state_dir, max_iterations_per_phase=2)
    save_state(state)
    exploration = dataclasses.replace(node_of(jfc_plan, "exploration"), max_iterations=1)

    with (
        patch("hepagent.agents.jfc.orchestrator._run_executor", new_callable=AsyncMock),
        patch(
            "hepagent.agents.jfc.orchestrator.run_review_gate", new_callable=AsyncMock
        ) as mock_review,
        patch("hepagent.agents.jfc.orchestrator.run_fixer", new_callable=AsyncMock),
    ):
        mock_review.return_value = ReviewGateResult(
            verdict="ITERATE", category_a_findings=["Bad thing"]
        )
        with pytest.raises(MaxIterationsExceeded) as exc_info:
            await run_phase_with_review(state, exploration, jfc_plan)
        assert exc_info.value.phase == "exploration"


# ------------------------------------------------------------------ regression


@pytest.mark.asyncio
async def test_regression_cycle_reruns_affected_nodes(state_dir, jfc_plan):
    from hepagent.agents.jfc.investigator import RegressionTicket
    from hepagent.agents.jfc.orchestrator import _run_regression_cycle, save_state
    from hepagent.agents.jfc.review_gate import PhaseRegressionError, ReviewGateResult

    state = make_state(state_dir, completed_nodes=["strategy", "exploration", "selection"])
    save_state(state)

    err = PhaseRegressionError(
        "selection",
        "exploration",
        "wrong cut",
        ReviewGateResult(
            verdict="REGRESS", regression_origin_phase="exploration", regression_symptom="wrong cut"
        ),
    )
    ticket = RegressionTicket(
        detected_phase="selection",
        origin_phase="exploration",
        symptom="wrong cut",
        affected_phases=["exploration", "selection"],
    )

    rerun_log: list[str] = []

    async def mock_run_phase(s, node, plan, cb=None, **kwargs):
        rerun_log.append(node.id)
        s.completed_nodes.append(node.id)

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
        await _run_regression_cycle(state, jfc_plan, err, None)

    assert rerun_log == ["exploration", "selection"]
    assert "strategy" in state.completed_nodes  # unaffected node preserved


@pytest.mark.asyncio
async def test_regression_without_a_ticket_falls_back_to_the_graph_between(state_dir, jfc_plan):
    """With no named nodes, the rerun set is descendants(origin) ∩ ancestors(detected).

    On a linear plan that is the old `origin..detected` slice; the point is that
    it is now computed from edges, so a branching plan gets the right answer
    instead of an arbitrary list slice.
    """
    from hepagent.agents.jfc.investigator import RegressionTicket
    from hepagent.agents.jfc.orchestrator import _run_regression_cycle, save_state
    from hepagent.agents.jfc.review_gate import PhaseRegressionError, ReviewGateResult

    state = make_state(
        state_dir,
        completed_nodes=["strategy", "exploration", "selection", "inference_expected"],
    )
    save_state(state)

    err = PhaseRegressionError(
        "inference_expected",
        "exploration",
        "bad binning",
        ReviewGateResult(verdict="REGRESS", regression_origin_phase="exploration"),
    )
    ticket = RegressionTicket(
        detected_phase="inference_expected",
        origin_phase="exploration",
        symptom="bad binning",
        affected_phases=[],  # the investigator named nothing
    )

    rerun_log: list[str] = []

    async def mock_run_phase(s, node, plan, cb=None, **kwargs):
        rerun_log.append(node.id)
        s.completed_nodes.append(node.id)

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
        await _run_regression_cycle(state, jfc_plan, err, None)

    assert rerun_log == ["exploration", "selection", "inference_expected"]
    assert "strategy" in state.completed_nodes


def test_between_excludes_a_branch_the_regression_cannot_reach(jfc_plan):
    """A sibling branch that does not feed the detected node is not re-run."""
    from hepagent.agents.jfc.orchestrator import _between
    from hepagent.plan.schema import PlanEdge

    # A calibration node hanging off strategy that nothing downstream consumes.
    calibration = dataclasses.replace(
        node_of(jfc_plan, "exploration"), id="calibration", directory="calibration"
    )
    plan = dataclasses.replace(
        jfc_plan,
        nodes=jfc_plan.nodes + (calibration,),
        edges=jfc_plan.edges + (PlanEdge(upstream="strategy", downstream="calibration"),),
    )
    affected = _between(plan, "strategy", "selection")
    assert "calibration" not in affected
    assert {"strategy", "exploration", "selection"} <= affected


# --------------------------------------------------------------- graph updates


@pytest.mark.asyncio
async def test_node_run_ingests_into_the_graph(state_dir, jfc_plan):
    """Executor output and the review round both get folded into the graph."""
    from hepagent.agents.jfc.orchestrator import run_phase_with_review, save_state
    from hepagent.agents.jfc.review_gate import ReviewGateResult

    state = make_state(state_dir)
    save_state(state)

    with (
        patch("hepagent.agents.jfc.orchestrator._run_executor", new_callable=AsyncMock),
        patch(
            "hepagent.agents.jfc.orchestrator.run_review_gate",
            new_callable=AsyncMock,
            return_value=ReviewGateResult(verdict="PASS"),
        ),
        patch("hepagent.agents.jfc.orchestrator.ingest_node") as mock_node,
        patch("hepagent.agents.jfc.orchestrator.ingest_review") as mock_review,
    ):
        await run_phase_with_review(state, node_of(jfc_plan, "strategy"), jfc_plan)

    mock_node.assert_called_once_with(state_dir, "strategy", jfc_plan)
    mock_review.assert_called_once_with(state_dir, "strategy", jfc_plan)


@pytest.mark.asyncio
async def test_graph_ingestion_failure_does_not_abort_the_run(state_dir, jfc_plan):
    """Graph bookkeeping is not allowed to take down a node that otherwise passed."""
    from hepagent.agents.jfc.orchestrator import run_phase_with_review, save_state
    from hepagent.agents.jfc.review_gate import ReviewGateResult

    state = make_state(state_dir)
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
            "hepagent.agents.jfc.orchestrator.ingest_node",
            side_effect=OSError("disk on fire"),
        ),
        patch("hepagent.agents.jfc.orchestrator.ingest_review"),
    ):
        await run_phase_with_review(
            state,
            node_of(jfc_plan, "strategy"),
            jfc_plan,
            lambda _p, msg: messages.append(msg),
        )

    assert "strategy" in state.completed_nodes
    assert any("graph phase ingestion failed" in m for m in messages)


@pytest.mark.asyncio
async def test_review_is_ingested_even_when_the_gate_raises(state_dir, jfc_plan):
    """An escalation is exactly when the provenance record matters most."""
    from hepagent.agents.jfc.orchestrator import run_phase_with_review, save_state
    from hepagent.agents.jfc.review_gate import PhaseEscalationError, ReviewGateResult

    state = make_state(state_dir)
    save_state(state)

    with (
        patch("hepagent.agents.jfc.orchestrator._run_executor", new_callable=AsyncMock),
        patch(
            "hepagent.agents.jfc.orchestrator.run_review_gate",
            new_callable=AsyncMock,
            side_effect=PhaseEscalationError("strategy", ReviewGateResult(verdict="ESCALATE")),
        ),
        patch("hepagent.agents.jfc.orchestrator.ingest_node"),
        patch("hepagent.agents.jfc.orchestrator.ingest_review") as mock_review,
    ):
        with pytest.raises(PhaseEscalationError):
            await run_phase_with_review(state, node_of(jfc_plan, "strategy"), jfc_plan)

    mock_review.assert_called_once_with(state_dir, "strategy", jfc_plan)


def test_graph_commitment_findings_flag_a_commitment_with_no_evidence(state_dir):
    """A commitment recorded in the graph but never closed blocks the commitments gate."""
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


# ------------------------------------------------ frontier-driven traversal


def test_nodes_before_declares_upstream_nodes_satisfied(jfc_plan):
    from hepagent.agents.jfc.orchestrator import _nodes_before

    assert _nodes_before(jfc_plan, "inference_expected") == {
        "strategy",
        "exploration",
        "selection",
    }
    assert _nodes_before(jfc_plan, "strategy") == set()
    assert _nodes_before(jfc_plan, "nonsense") == set()
    assert _nodes_before(jfc_plan, None) == set()


def _drain(state_dir, state, plan, assumed=frozenset()):
    """Run a phase sequence to exhaustion, completing each node as it is yielded."""
    from hepagent.agents.jfc.orchestrator import _phase_sequence

    emitted = []
    for node in _phase_sequence(state_dir, state, plan, set(assumed)):
        emitted.append(node.id)
        state.completed_nodes.append(node.id)
    return emitted


def test_phase_sequence_follows_the_graph_frontier(state_dir, jfc_plan):
    from hepagent.agents.jfc.graph_builder import bootstrap_graph

    bootstrap_graph(state_dir, jfc_plan)
    assert _drain(state_dir, make_state(state_dir), jfc_plan) == [
        "strategy",
        "exploration",
        "selection",
        "inference_expected",
        "inference_partial",
        "inference_observed",
        "documentation",
    ]


def test_phase_sequence_yields_plan_nodes_not_ids(state_dir, jfc_plan):
    """Downstream code reads directory, artifact and reviewers off the node."""
    from hepagent.agents.jfc.graph_builder import bootstrap_graph
    from hepagent.agents.jfc.orchestrator import _phase_sequence
    from hepagent.plan.schema import PlanNode

    bootstrap_graph(state_dir, jfc_plan)
    state = make_state(state_dir)
    first = next(iter(_phase_sequence(state_dir, state, jfc_plan, set())))
    assert isinstance(first, PlanNode)
    assert first.id == "strategy"


def test_phase_sequence_skips_completed_nodes(state_dir, jfc_plan):
    from hepagent.agents.jfc.graph_builder import bootstrap_graph

    bootstrap_graph(state_dir, jfc_plan)
    state = make_state(state_dir, completed_nodes=["strategy", "exploration"])
    assert _drain(state_dir, state, jfc_plan) == [
        "selection",
        "inference_expected",
        "inference_partial",
        "inference_observed",
        "documentation",
    ]


def test_phase_sequence_honours_an_explicit_resume_point(state_dir, jfc_plan):
    """Starting mid-plan must not stall on upstream nodes never having run."""
    from hepagent.agents.jfc.graph_builder import bootstrap_graph
    from hepagent.agents.jfc.orchestrator import _nodes_before

    bootstrap_graph(state_dir, jfc_plan)
    assumed = _nodes_before(jfc_plan, "inference_expected")
    assert _drain(state_dir, make_state(state_dir), jfc_plan, assumed) == [
        "inference_expected",
        "inference_partial",
        "inference_observed",
        "documentation",
    ]


def test_phase_sequence_terminates_when_a_node_never_completes(state_dir, jfc_plan):
    """A node that runs without passing must not be offered forever."""
    from hepagent.agents.jfc.graph_builder import bootstrap_graph
    from hepagent.agents.jfc.orchestrator import _phase_sequence

    bootstrap_graph(state_dir, jfc_plan)
    state = make_state(state_dir)
    # Never mark anything complete: the loop must still end.
    emitted = [n.id for n in _phase_sequence(state_dir, state, jfc_plan, set())]
    assert emitted == ["strategy"]


def test_phase_sequence_falls_back_to_plan_order_without_a_graph(state_dir, jfc_plan):
    assert _drain(state_dir, make_state(state_dir), jfc_plan) == list(jfc_plan.node_ids())


def test_phase_sequence_runs_both_branches_of_a_fan_out(state_dir, jfc_plan):
    """The structure the pre-plan orchestrator could not express."""
    from hepagent.agents.jfc.graph_builder import bootstrap_graph

    selection = node_of(jfc_plan, "selection")
    channels = [
        dataclasses.replace(selection, id=f"selection_{ch}", directory=f"sel_{ch}")
        for ch in ("ee", "mumu")
    ]
    at = [n.id for n in jfc_plan.nodes].index("selection")
    others = [n for n in jfc_plan.nodes if n.id != "selection"]

    edges = []
    for edge in jfc_plan.edges:
        if edge.upstream == "selection":
            edges += [dataclasses.replace(edge, upstream=c.id) for c in channels]
        elif edge.downstream == "selection":
            edges += [dataclasses.replace(edge, downstream=c.id) for c in channels]
        else:
            edges.append(edge)

    plan = dataclasses.replace(
        jfc_plan, nodes=tuple(others[:at] + channels + others[at:]), edges=tuple(edges)
    )
    save_plan(state_dir, plan)
    bootstrap_graph(state_dir, plan)

    emitted = _drain(state_dir, make_state(state_dir), plan)
    assert emitted[:4] == ["strategy", "exploration", "selection_ee", "selection_mumu"]
    assert emitted[4] == "inference_expected"
