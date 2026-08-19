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


# ------------------------------------------------- a plan must be runnable first


def test_a_plan_that_escapes_the_analysis_root_is_refused(jfc_plan):
    """`--plan` bypasses the editor, which is what normally refuses this.

    The plan's `directory` becomes a real mkdir and a real `CLAUDE.md` write, so
    an unchecked authored plan could scaffold itself over a sibling project.
    """
    import dataclasses

    from hepagent.agents.jfc.orchestrator import PlanNotRunnableError, require_runnable_plan

    evil = dataclasses.replace(jfc_plan.nodes[0], directory="../elsewhere")
    plan = dataclasses.replace(jfc_plan, nodes=(evil,) + jfc_plan.nodes[1:])

    with pytest.raises(PlanNotRunnableError, match="escapes the analysis root"):
        require_runnable_plan(plan)


def test_a_cyclic_plan_is_refused(jfc_plan):
    import dataclasses

    from hepagent.agents.jfc.orchestrator import PlanNotRunnableError, require_runnable_plan
    from hepagent.plan.schema import PlanEdge

    plan = dataclasses.replace(
        jfc_plan,
        edges=jfc_plan.edges + (PlanEdge(upstream="documentation", downstream="strategy"),),
    )
    with pytest.raises(PlanNotRunnableError, match="P4-acyclic"):
        require_runnable_plan(plan)


def test_the_shipped_templates_are_runnable(jfc_plan):
    """The guard must not reject what `jfc run` produces by default."""
    from hepagent.agents.jfc.orchestrator import require_runnable_plan

    assert require_runnable_plan(jfc_plan) is jfc_plan


@pytest.mark.asyncio
async def test_an_escaping_plan_never_reaches_the_scaffolder(tmp_path, jfc_plan):
    """The check runs before anything touches the filesystem."""
    import dataclasses
    from unittest.mock import AsyncMock, patch

    from hepagent.agents.jfc.orchestrator import PlanNotRunnableError, run_jfc_analysis

    evil = dataclasses.replace(jfc_plan.nodes[0], directory="../elsewhere")
    plan = dataclasses.replace(jfc_plan, nodes=(evil,) + jfc_plan.nodes[1:])

    with patch(
        "hepagent.agents.jfc.orchestrator.scaffold_jfc_analysis", new_callable=AsyncMock
    ) as scaffold:
        with pytest.raises(PlanNotRunnableError):
            await run_jfc_analysis(
                analysis_name="zbb",
                physics_prompt="Measure it.",
                analysis_type="measurement",
                base_dir=str(tmp_path),
                plan=plan,
            )
    scaffold.assert_not_awaited()
    assert not (tmp_path / "elsewhere").exists()


# ------------------------------------------------------------ condition nodes


@pytest.fixture
def loop_analysis(tmp_path):
    """An analysis whose plan loops: propose → evaluate → converged? ↺ propose."""
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "plan"))
    from plan_factory import make_loop_plan

    from hepagent.agents.jfc.graph_builder import bootstrap_graph

    plan = make_loop_plan()
    root = tmp_path / "loopdemo"
    root.mkdir()
    for node in plan.nodes:
        (root / node.directory / "outputs").mkdir(parents=True)
        (root / node.directory / "review").mkdir(parents=True)
    (root / "prompt.md").write_text(plan.problem)
    save_plan(root, plan)
    bootstrap_graph(root, plan)
    return root, plan


def write_metric(root, value):
    """Write the number the loop's condition reads."""
    path = root / "evaluate_dir/outputs/results/optimization.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"significance": value}))


async def run_loop(root, plan, values, **state_overrides):
    """Drive a full run, feeding `values` to the metric one execution at a time.

    Returns the order nodes were executed in, so a test can assert the loop body
    actually ran more than once.
    """
    from hepagent.agents.jfc.orchestrator import run_jfc_analysis
    from hepagent.agents.jfc.review_gate import ReviewGateResult

    executed = []
    feed = list(values)

    async def fake_executor(state, node, *args, **kwargs):
        executed.append(node.id)
        (state.root / node.artifact_path).parent.mkdir(parents=True, exist_ok=True)
        (state.root / node.artifact_path).write_text(f"# {node.label}\n")
        if node.id == "evaluate" and feed:
            write_metric(state.root, feed.pop(0))

    async def always_pass(*args, **kwargs):
        return ReviewGateResult(verdict="PASS")

    with (
        patch("hepagent.agents.jfc.orchestrator._run_executor", side_effect=fake_executor),
        patch("hepagent.agents.jfc.orchestrator.run_review_gate", side_effect=always_pass),
        patch("hepagent.agents.jfc.orchestrator.run_fixer", new_callable=AsyncMock),
    ):
        await run_jfc_analysis(
            analysis_name=root.name,
            physics_prompt=plan.problem,
            analysis_type="measurement",
            base_dir=str(root.parent),
            plan=plan,
            **state_overrides,
        )
    return executed


@pytest.mark.asyncio
async def test_a_loop_re_runs_its_body_until_the_metric_converges(loop_analysis):
    """The whole point: the optimizer keeps going while the metric improves."""
    root, plan = loop_analysis
    # 3.0 → 3.5 (still improving) → 3.51 (gain 0.01 < 0.02, converged)
    executed = await run_loop(root, plan, [3.0, 3.5, 3.51])

    assert executed.count("propose") == 3
    assert executed.count("evaluate") == 3
    assert executed[-1] == "inference"


@pytest.mark.asyncio
async def test_a_converged_loop_runs_its_body_once(loop_analysis):
    """A first pass that already converges still has to run the body once."""
    root, plan = loop_analysis
    executed = await run_loop(root, plan, [3.0, 3.0])

    assert executed.count("propose") == 2  # first pass has no previous value to compare
    assert executed[-1] == "inference"


@pytest.mark.asyncio
async def test_a_loop_that_never_converges_stops_at_its_budget(loop_analysis):
    """The iteration bound is the termination guarantee, not the metric."""
    root, plan = loop_analysis
    executed = await run_loop(root, plan, [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0])

    condition = plan.node("converged")
    assert executed.count("propose") == condition.condition.max_iterations
    assert executed[-1] == "inference"


@pytest.mark.asyncio
async def test_an_exhausted_loop_can_be_made_to_escalate(loop_analysis):
    """`on_exhaustion: escalate` stops the run for a human instead of continuing."""
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "plan"))
    from plan_factory import make_loop_plan

    from hepagent.agents.jfc.review_gate import PhaseEscalationError

    root, _ = loop_analysis
    plan = make_loop_plan(on_exhaustion="escalate")
    save_plan(root, plan)

    with pytest.raises(PhaseEscalationError):
        await run_loop(root, plan, [1.0, 2.0, 3.0, 4.0, 5.0, 6.0])


@pytest.mark.asyncio
async def test_the_loop_budget_is_recorded_and_survives_a_restart(loop_analysis):
    """A resume must not silently refill the budget."""
    from hepagent.agents.jfc.orchestrator import load_state

    root, plan = loop_analysis
    await run_loop(root, plan, [1.0, 2.0, 3.0, 4.0, 5.0, 6.0])

    state = load_state(root)
    assert state.condition_iterations["converged"] == 3
    assert state.condition_history["converged"] == [1.0, 2.0, 3.0]


@pytest.mark.asyncio
async def test_every_evaluation_leaves_a_decision_record(loop_analysis):
    root, plan = loop_analysis
    await run_loop(root, plan, [3.0, 3.5, 3.51])

    decisions = sorted((root / "converged_dir" / "decisions").glob("*.md"))
    assert [p.name for p in decisions] == [
        "iteration_01.md",
        "iteration_02.md",
        "iteration_03.md",
    ]
    assert "propose" in decisions[0].read_text()
    assert "inference" in decisions[-1].read_text()


@pytest.mark.asyncio
async def test_a_loop_decision_lands_in_the_provenance_graph(loop_analysis):
    from hepagent.graph.store import AnalysisGraph

    root, plan = loop_analysis
    await run_loop(root, plan, [3.0, 3.5, 3.51])

    graph = AnalysisGraph.load(root)
    decisions = [n for n in graph.nodes(type="decision") if n.phase == "converged"]
    assert len(decisions) == 3

    # The back branch invalidates the loop head; the forward one approves its target.
    head = "artifact:propose_dir/outputs/PROPOSE.md"
    exits = "artifact:inference_dir/outputs/INFERENCE.md"
    assert any(e.dst == head and e.type == "invalidates" for e in graph.edges())
    assert any(e.src == exits and e.type == "approved_by" for e in graph.edges())


@pytest.mark.asyncio
async def test_progress_reports_each_pass_of_the_loop(loop_analysis):
    """A loop that spends model budget silently is the failure mode to avoid."""
    from hepagent.agents.jfc.orchestrator import run_condition_node, save_state

    root, plan = loop_analysis
    state = make_state(root, analysis_root=str(root))
    save_state(state)
    write_metric(root, 3.0)

    messages = []
    await run_condition_node(
        state,
        plan.node("converged"),
        plan,
        set(),
        progress_callback=lambda node, msg: messages.append((node, msg)),
    )

    joined = " ".join(m for _node, m in messages)
    assert "iteration 1/3" in joined
    assert "condition false" in joined


# ------------------------------------------------------------------- forks


@pytest.fixture
def fork_analysis(tmp_path):
    """A plan that forks and re-joins: check → (true) fast / (false) slow → combine."""
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "plan"))
    from plan_factory import make_condition, make_node, make_plan

    from hepagent.agents.jfc.graph_builder import bootstrap_graph
    from hepagent.plan.schema import ConditionMetric, PlanEdge

    plan = make_plan(
        node_ids=("start", "fast", "slow", "combine"),
        edges=(
            ("start", "check"),
            PlanEdge(upstream="check", downstream="fast", kind="on_true"),
            PlanEdge(upstream="check", downstream="slow", kind="on_false"),
            ("fast", "combine"),
            ("slow", "combine"),
        ),
        nodes=(
            make_node("start"),
            make_condition(
                "check",
                metric=ConditionMetric(
                    source="start_dir/outputs/results/quality.json",
                    key="clean",
                    compare="above",
                    value=0.5,
                ),
            ),
            make_node("fast"),
            make_node("slow"),
            make_node("combine"),
        ),
    )
    root = tmp_path / "forkdemo"
    root.mkdir()
    for node in plan.nodes:
        (root / node.directory / "outputs").mkdir(parents=True)
        (root / node.directory / "review").mkdir(parents=True)
    (root / "prompt.md").write_text(plan.problem)
    save_plan(root, plan)
    bootstrap_graph(root, plan)
    return root, plan


async def run_fork(root, plan, quality):
    from hepagent.agents.jfc.orchestrator import run_jfc_analysis
    from hepagent.agents.jfc.review_gate import ReviewGateResult

    executed = []

    async def fake_executor(state, node, *args, **kwargs):
        executed.append(node.id)
        (state.root / node.artifact_path).parent.mkdir(parents=True, exist_ok=True)
        (state.root / node.artifact_path).write_text(f"# {node.label}\n")
        if node.id == "start":
            path = state.root / "start_dir/outputs/results/quality.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({"clean": quality}))

    async def always_pass(*args, **kwargs):
        return ReviewGateResult(verdict="PASS")

    with (
        patch("hepagent.agents.jfc.orchestrator._run_executor", side_effect=fake_executor),
        patch("hepagent.agents.jfc.orchestrator.run_review_gate", side_effect=always_pass),
        patch("hepagent.agents.jfc.orchestrator.run_fixer", new_callable=AsyncMock),
    ):
        await run_jfc_analysis(
            analysis_name=root.name,
            physics_prompt=plan.problem,
            analysis_type="measurement",
            base_dir=str(root.parent),
            plan=plan,
        )
    return executed


@pytest.mark.asyncio
async def test_a_fork_runs_only_the_branch_it_took(fork_analysis):
    root, plan = fork_analysis
    executed = await run_fork(root, plan, quality=0.9)

    assert "fast" in executed
    assert "slow" not in executed


@pytest.mark.asyncio
async def test_the_node_after_a_fork_still_runs(fork_analysis):
    """It is downstream of both branches; waiting on the untaken one would stall."""
    root, plan = fork_analysis
    executed = await run_fork(root, plan, quality=0.9)

    assert executed[-1] == "combine"


@pytest.mark.asyncio
async def test_the_other_branch_runs_when_the_condition_goes_the_other_way(fork_analysis):
    root, plan = fork_analysis
    executed = await run_fork(root, plan, quality=0.1)

    assert "slow" in executed
    assert "fast" not in executed
    assert executed[-1] == "combine"


@pytest.mark.asyncio
async def test_the_skipped_branch_is_recorded_in_the_run_state(fork_analysis):
    from hepagent.agents.jfc.orchestrator import load_state

    root, plan = fork_analysis
    await run_fork(root, plan, quality=0.9)

    assert load_state(root).skipped_nodes == ["slow"]


@pytest.mark.asyncio
async def test_a_plan_whose_exhaustion_branch_also_loops_stops_anyway(loop_analysis):
    """The budget exists to rule out running forever; a bad plan cannot undo that.

    `on_exhaustion: "false"` names the branch that loops back, so honouring it
    would rewind again on every pass.
    """
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "plan"))
    from plan_factory import make_loop_plan

    root, _ = loop_analysis
    plan = make_loop_plan(on_exhaustion="false")
    save_plan(root, plan)

    executed = await run_loop(root, plan, [1.0, 2.0, 3.0, 4.0, 5.0, 6.0])
    assert executed.count("propose") == 3


@pytest.mark.asyncio
async def test_the_budget_bounds_body_executions_not_evaluations(loop_analysis):
    """`max_iterations: N` is a cost the author is choosing: N runs of the body."""
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "plan"))
    from plan_factory import make_loop_plan

    from hepagent.agents.jfc.orchestrator import load_state

    root, _ = loop_analysis
    plan = make_loop_plan(max_iterations=2)
    save_plan(root, plan)

    executed = await run_loop(root, plan, [1.0, 2.0, 3.0, 4.0])
    assert executed.count("propose") == 2
    assert load_state(root).condition_iterations["converged"] == 2


# --------------------------------------------------- running a single node


async def run_one(root, plan, node_id):
    """Run `node_id` alone, and report which nodes actually executed."""
    from hepagent.agents.jfc.orchestrator import run_jfc_analysis
    from hepagent.agents.jfc.review_gate import ReviewGateResult

    executed: list[str] = []

    async def fake_executor(state, node, *args, **kwargs):
        executed.append(node.id)
        (state.root / node.artifact_path).parent.mkdir(parents=True, exist_ok=True)
        (state.root / node.artifact_path).write_text(f"# {node.label}\n")

    async def always_pass(*args, **kwargs):
        return ReviewGateResult(verdict="PASS")

    with (
        patch("hepagent.agents.jfc.orchestrator._run_executor", side_effect=fake_executor),
        patch("hepagent.agents.jfc.orchestrator.run_review_gate", side_effect=always_pass),
        patch(
            "hepagent.agents.jfc.orchestrator._run_note_writer_and_typesetter",
            new_callable=AsyncMock,
        ),
        patch("hepagent.agents.jfc.orchestrator.run_fixer", new_callable=AsyncMock),
    ):
        result = await run_jfc_analysis(
            analysis_name=root.name,
            physics_prompt=plan.problem,
            analysis_type="measurement",
            base_dir=str(root.parent),
            only_node=node_id,
        )
    return executed, result


@pytest.mark.asyncio
async def test_only_node_runs_that_node_and_stops(jfc_analysis, jfc_plan):
    """The plan page's per-node Run button: this node, nothing upstream, nothing after."""
    executed, result = await run_one(jfc_analysis, jfc_plan, "selection")

    assert executed == ["selection"]
    assert result == jfc_analysis / jfc_plan.require_node("selection").artifact_path


@pytest.mark.asyncio
async def test_only_node_ignores_the_frontier_the_planner_would_offer(jfc_analysis, jfc_plan):
    """Nothing upstream of `selection` has run, and the button still runs it.

    Going through `next_phase` would refuse — which is exactly why a single-node
    run does not go through it.
    """
    executed, _ = await run_one(jfc_analysis, jfc_plan, "selection")

    assert "strategy" not in executed
    assert "exploration" not in executed


@pytest.mark.asyncio
async def test_only_node_leaves_the_run_state_alone(jfc_analysis, jfc_plan):
    """A single-node run is a resume, not a fresh start: earlier progress stands."""
    from hepagent.agents.jfc.orchestrator import load_state

    await run_one(jfc_analysis, jfc_plan, "exploration")
    await run_one(jfc_analysis, jfc_plan, "selection")

    completed = set(load_state(jfc_analysis).completed_nodes)
    assert {"exploration", "selection"} <= completed


@pytest.mark.asyncio
async def test_only_node_refuses_a_node_the_plan_does_not_have(jfc_analysis, jfc_plan):
    with pytest.raises(ValueError, match="nonesuch"):
        await run_one(jfc_analysis, jfc_plan, "nonesuch")


# ------------------------------------------------------------- the turn cap


def _node(**kwargs):
    from hepagent.plan.schema import PlanNode

    return PlanNode(id="n", label="N", directory="n", artifact="N.md", **kwargs)


def test_turn_cap_precedence_is_node_then_run_then_role():
    """Narrowest wins. A node that needs a long leash does not raise the ceiling
    for the rest of the plan, and a run-wide cap still beats the role default."""
    from hepagent.agents.jfc.orchestrator import _turns_for

    assert _turns_for(_node(max_turns=120), 40, 50) == 120  # node over run
    assert _turns_for(_node(), 40, 50) == 40  # run over role
    assert _turns_for(_node(), None, 50) == 50  # role default
