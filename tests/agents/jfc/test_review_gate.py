"""Tests for JFC review gate."""

import pytest


@pytest.fixture
def analysis_root(tmp_path):
    root = tmp_path / "test_analysis"
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
    (root / "prompt.md").write_text("Test prompt")
    (root / "COMMITMENTS.md").write_text(
        "# Phase 1 Commitments\n\n"
        "| ID | Commitment | Status | Evidence | Phase Resolved |\n"
        "|----|-----------|--------|----------|---------------|\n"
    )
    return root


def test_review_gate_result_structure():
    from hepagent.agents.jfc.review_gate import ReviewGateResult

    r = ReviewGateResult(verdict="PASS", category_a_findings=[], category_b_findings=[])
    assert r.verdict == "PASS"
    assert r.category_a_findings == []


def test_phase_escalation_error():
    from hepagent.agents.jfc.review_gate import PhaseEscalationError, ReviewGateResult

    result = ReviewGateResult(verdict="ESCALATE", category_a_findings=["bad thing"])
    err = PhaseEscalationError(1, result)
    assert err.phase == 1
    assert err.result is result


def test_phase1_reviewer_composition():
    from hepagent.agents.jfc.review_gate import _PHASE_REVIEWERS

    phase1_reviewers = _PHASE_REVIEWERS[1]
    assert "physics" in phase1_reviewers
    assert "critical" in phase1_reviewers
    assert "constructive" in phase1_reviewers


def test_phase2_reviewer_composition():
    from hepagent.agents.jfc.review_gate import _PHASE_REVIEWERS

    phase2_reviewers = _PHASE_REVIEWERS[2]
    assert "plot" in phase2_reviewers
    assert "physics" not in phase2_reviewers


def test_phase4a_reviewer_composition():
    from hepagent.agents.jfc.review_gate import _PHASE_REVIEWERS

    phase4a_reviewers = _PHASE_REVIEWERS["4a"]
    assert "physics" in phase4a_reviewers
    assert "bibtex" in phase4a_reviewers
    assert "plot" in phase4a_reviewers


def test_phase5_reviewer_composition():
    from hepagent.agents.jfc.review_gate import _PHASE_REVIEWERS

    phase5_reviewers = _PHASE_REVIEWERS[5]
    assert "rendering" in phase5_reviewers
    assert "bibtex" in phase5_reviewers
    assert len(phase5_reviewers) == 6


def test_arbiter_phases():
    from hepagent.agents.jfc.review_gate import _ARBITER_PHASES

    assert 1 in _ARBITER_PHASES
    assert "4a" in _ARBITER_PHASES
    assert "4b" in _ARBITER_PHASES
    assert 5 in _ARBITER_PHASES
    assert 3 not in _ARBITER_PHASES


def test_parse_verdict_pass(tmp_path):
    from hepagent.agents.jfc.review_gate import parse_verdict_from_adjudication

    adj = tmp_path / "ADJUDICATION.md"
    adj.write_text("# Adjudication\n\nAll findings reviewed.\n\nPASS")
    verdict, cat_a, cat_b, origin = parse_verdict_from_adjudication(adj)
    assert verdict == "PASS"
    assert origin is None


def test_parse_verdict_iterate(tmp_path):
    from hepagent.agents.jfc.review_gate import parse_verdict_from_adjudication

    adj = tmp_path / "ADJUDICATION.md"
    adj.write_text("# Adjudication\n\nCategory A findings found.\n\nITERATE")
    verdict, cat_a, cat_b, origin = parse_verdict_from_adjudication(adj)
    assert verdict == "ITERATE"
    assert origin is None


def test_parse_verdict_regress_int(tmp_path):
    from hepagent.agents.jfc.review_gate import parse_verdict_from_adjudication

    adj = tmp_path / "ADJUDICATION.md"
    adj.write_text("# Adjudication\n\nRoot cause in Phase 3.\n\nREGRESS(3)")
    verdict, cat_a, cat_b, origin = parse_verdict_from_adjudication(adj)
    assert verdict == "REGRESS"
    assert origin == 3


def test_parse_verdict_regress_subphase(tmp_path):
    from hepagent.agents.jfc.review_gate import parse_verdict_from_adjudication

    adj = tmp_path / "ADJUDICATION.md"
    adj.write_text("# Adjudication\n\nRoot cause in Phase 4a.\n\nREGRESS(4a)")
    verdict, cat_a, cat_b, origin = parse_verdict_from_adjudication(adj)
    assert verdict == "REGRESS"
    assert origin == "4a"


def test_phase_regression_error():
    from hepagent.agents.jfc.review_gate import PhaseRegressionError, ReviewGateResult

    result = ReviewGateResult(verdict="REGRESS", regression_origin_phase=3)
    err = PhaseRegressionError("4a", 3, "bad selection cut", result)
    assert err.detected_phase == "4a"
    assert err.origin_phase == 3
    assert err.symptom == "bad selection cut"
    assert err.result is result


# --------------------------------------------------- graph consistency gating


@pytest.fixture
def graph_root(tmp_path):
    """An analysis root with a graph and a Phase 1 artifact already ingested."""
    from hepagent.agents.jfc.graph_builder import bootstrap_graph, ingest_phase

    root = tmp_path / "zbb"
    (root / "phase1_strategy" / "outputs").mkdir(parents=True)
    (root / "phase1_strategy" / "review").mkdir(parents=True)
    (root / "prompt.md").write_text("Measure the Z to bb cross-section.")
    (root / "phase1_strategy" / "outputs" / "STRATEGY.md").write_text("strategy")
    (root / "COMMITMENTS.md").write_text("# Phase 1 Commitments\n")
    bootstrap_graph(root, "zbb", "measurement", "Measure the Z to bb cross-section.")
    ingest_phase(root, 1)
    return root


def _open_commitment(root):
    (root / "COMMITMENTS.md").write_text(
        "# Phase 1 Commitments\n\n"
        "| ID | Commitment | Status | Evidence | Phase Resolved |\n"
        "|----|-----------|--------|----------|---------------|\n"
        "| D2 | Generator comparison | pending | | |\n"
    )
    from hepagent.agents.jfc.graph_builder import ingest_phase

    ingest_phase(root, 1)


def test_write_graph_validation_persists_the_report(graph_root):
    from hepagent.agents.jfc.review_gate import write_graph_validation

    review_dir = graph_root / "phase1_strategy" / "review"
    report = write_graph_validation(graph_root, review_dir)

    assert (review_dir / "GRAPH_VALIDATION.md").exists()
    assert "Graph validation" in (review_dir / "GRAPH_VALIDATION.md").read_text()
    assert report.ok


def test_write_graph_validation_survives_a_missing_graph(tmp_path):
    from hepagent.agents.jfc.review_gate import write_graph_validation

    review_dir = tmp_path / "review"
    review_dir.mkdir()
    report = write_graph_validation(tmp_path, review_dir)
    assert report.findings == []


def test_write_graph_validation_reports_an_open_commitment(graph_root):
    from hepagent.agents.jfc.review_gate import write_graph_validation

    _open_commitment(graph_root)
    report = write_graph_validation(graph_root, graph_root / "phase1_strategy" / "review")
    assert [f.node_id for f in report.blocking] == ["commitment:D2"]


@pytest.mark.asyncio
async def test_graph_errors_downgrade_a_reviewer_pass_to_iterate(graph_root):
    """An unclosed commitment is a fact: the phase cannot pass on it."""
    from unittest.mock import AsyncMock, patch

    from hepagent.agents.jfc.review_gate import run_review_gate

    _open_commitment(graph_root)
    # Phase 2 has no arbiter, so the verdict comes from reviewer files.
    (graph_root / "phase2_exploration" / "review").mkdir(parents=True)
    (graph_root / "phase2_exploration" / "review" / "plot_validation.md").write_text("fine\n\nPASS")

    with patch("hepagent.agents.jfc.review_gate._run_single_reviewer", new_callable=AsyncMock):
        result = await run_review_gate(2, graph_root)

    assert result.verdict == "ITERATE"
    assert any("[graph]" in f for f in result.category_a_findings)
    assert result.graph_blocking


@pytest.mark.asyncio
async def test_a_consistent_graph_leaves_the_reviewer_verdict_alone(graph_root):
    from unittest.mock import AsyncMock, patch

    from hepagent.agents.jfc.review_gate import run_review_gate

    (graph_root / "phase2_exploration" / "review").mkdir(parents=True)
    (graph_root / "phase2_exploration" / "review" / "plot_validation.md").write_text("fine\n\nPASS")

    with patch("hepagent.agents.jfc.review_gate._run_single_reviewer", new_callable=AsyncMock):
        result = await run_review_gate(2, graph_root)

    assert result.verdict == "PASS"
    assert result.graph_blocking == []


@pytest.mark.asyncio
async def test_advisory_findings_do_not_downgrade_a_pass(graph_root):
    """Warnings are the arbiter's call, not a code-enforced block."""
    from unittest.mock import AsyncMock, patch

    from hepagent.agents.jfc.review_gate import run_review_gate
    from hepagent.graph.schema import Node
    from hepagent.graph.store import AnalysisGraph

    # A warning-severity finding: evidence recorded with no backing file.
    graph = AnalysisGraph.load(graph_root)
    graph.add_node(Node(id="evidence:vague", type="evidence", label="looks fine", phase="2"))

    (graph_root / "phase2_exploration" / "review").mkdir(parents=True, exist_ok=True)
    (graph_root / "phase2_exploration" / "review" / "plot_validation.md").write_text("fine\n\nPASS")

    with patch("hepagent.agents.jfc.review_gate._run_single_reviewer", new_callable=AsyncMock):
        result = await run_review_gate(2, graph_root)

    assert result.verdict == "PASS"
    assert result.graph_blocking == []
    assert any("looks fine" in f for f in result.graph_findings)


def test_reviewer_prompts_carry_the_graph_section(graph_root):
    from hepagent.agents.jfc.reviewers import create_arbiter, create_critical_reviewer

    critical = create_critical_reviewer(1, graph_root).instructions
    assert "ANALYSIS GRAPH" in critical
    assert "Checks the graph lets you make" in critical

    arbiter = create_arbiter(1, graph_root).instructions
    assert "ANALYSIS GRAPH" in arbiter
    assert "enforced in code" in arbiter


def test_reviewer_prompt_omits_the_graph_section_when_there_is_none(tmp_path):
    from hepagent.agents.jfc.reviewers import create_critical_reviewer

    bare = tmp_path / "bare"
    bare.mkdir()
    assert "ANALYSIS GRAPH" not in create_critical_reviewer(1, bare).instructions


@pytest.mark.asyncio
async def test_a_note_citing_a_nonexistent_figure_blocks_the_phase(graph_root):
    """M6's loop closing: the note writer gets a figure manifest, R6 enforces it."""
    from unittest.mock import AsyncMock, patch

    from hepagent.agents.jfc.review_gate import run_review_gate

    note = graph_root / "phase2_exploration" / "outputs" / "ANALYSIS_NOTE_4a_v1.md"
    note.parent.mkdir(parents=True, exist_ok=True)
    note.write_text("![A plot that was never made.](figures/ghost.png)")
    (graph_root / "phase2_exploration" / "review").mkdir(parents=True, exist_ok=True)
    (graph_root / "phase2_exploration" / "review" / "plot_validation.md").write_text("fine\n\nPASS")

    with patch("hepagent.agents.jfc.review_gate._run_single_reviewer", new_callable=AsyncMock):
        result = await run_review_gate(2, graph_root)

    assert result.verdict == "ITERATE"
    assert any("ghost.png" in f for f in result.graph_blocking)
