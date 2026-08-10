"""Tests for JFC review gate."""

import pytest

from hepagent.plan.store import save_plan

COMMITMENTS_HEADER = (
    "# Analysis Commitments\n\n"
    "| ID | Commitment | Status | Evidence | Phase Resolved |\n"
    "|----|-----------|--------|----------|---------------|\n"
)


@pytest.fixture
def analysis_root(tmp_path, jfc_plan):
    root = tmp_path / "test_analysis"
    root.mkdir()
    for plan_node in jfc_plan.nodes:
        (root / plan_node.directory / "outputs").mkdir(parents=True)
        (root / plan_node.directory / "review").mkdir(parents=True)
    (root / "prompt.md").write_text(jfc_plan.problem)
    (root / "COMMITMENTS.md").write_text(COMMITMENTS_HEADER)
    save_plan(root, jfc_plan)
    return root


def test_review_gate_result_structure():
    from hepagent.agents.jfc.review_gate import ReviewGateResult

    r = ReviewGateResult(verdict="PASS", category_a_findings=[], category_b_findings=[])
    assert r.verdict == "PASS"
    assert r.category_a_findings == []


def test_phase_escalation_error():
    from hepagent.agents.jfc.review_gate import PhaseEscalationError, ReviewGateResult

    result = ReviewGateResult(verdict="ESCALATE", category_a_findings=["bad thing"])
    err = PhaseEscalationError("strategy", result)
    assert err.phase == "strategy"
    assert err.result is result


def test_the_reviewer_panel_comes_from_the_node(jfc_plan):
    """Which reviewers run is a plan field, not a table in the review gate."""
    assert set(jfc_plan.require_node("strategy").reviewers) >= {
        "physics",
        "critical",
        "constructive",
    }
    assert "plot" in jfc_plan.require_node("exploration").reviewers
    assert "physics" not in jfc_plan.require_node("exploration").reviewers


def test_every_reviewer_a_node_names_has_a_factory(jfc_plan):
    from hepagent.agents.jfc.reviewers import REVIEWER_FACTORIES

    for plan_node in jfc_plan.nodes:
        assert set(plan_node.reviewers) <= set(REVIEWER_FACTORIES), plan_node.id


def test_a_node_without_reviewers_still_gets_one(analysis_root, jfc_plan):
    """The gate must never wave a node through unreviewed."""
    import dataclasses

    from hepagent.agents.jfc.review_gate import reviewers_for

    bare = dataclasses.replace(jfc_plan.require_node("exploration"), reviewers=())
    assert reviewers_for(bare) == ["critical"]


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


def test_parse_verdict_regress_names_a_node(tmp_path):
    from hepagent.agents.jfc.review_gate import parse_verdict_from_adjudication

    adj = tmp_path / "ADJUDICATION.md"
    adj.write_text("# Adjudication\n\nRoot cause in the selection.\n\nREGRESS(selection)")
    verdict, cat_a, cat_b, origin = parse_verdict_from_adjudication(adj)
    assert verdict == "REGRESS"
    assert origin == "selection"


def test_parse_verdict_regress_lowercases_a_shouted_node_id(tmp_path):
    """Arbiters shout their verdict line; node ids are lowercase slugs."""
    from hepagent.agents.jfc.review_gate import parse_verdict_from_adjudication

    adj = tmp_path / "ADJUDICATION.md"
    adj.write_text("# Adjudication\n\nRoot cause upstream.\n\nREGRESS(INFERENCE_EXPECTED)")
    verdict, _cat_a, _cat_b, origin = parse_verdict_from_adjudication(adj)
    assert verdict == "REGRESS"
    assert origin == "inference_expected"


def test_regress_mentioned_in_prose_is_not_a_verdict(tmp_path):
    from hepagent.agents.jfc.review_gate import parse_verdict_from_adjudication

    adj = tmp_path / "ADJUDICATION.md"
    adj.write_text(
        "# Adjudication\n\nNo trigger issue would require REGRESS(strategy) here.\n\n"
        + "Everything checks out. " * 20
        + "\n\nPASS"
    )
    verdict, _cat_a, _cat_b, origin = parse_verdict_from_adjudication(adj)
    assert verdict == "PASS"
    assert origin is None


def test_phase_regression_error():
    from hepagent.agents.jfc.review_gate import PhaseRegressionError, ReviewGateResult

    result = ReviewGateResult(verdict="REGRESS", regression_origin_phase="selection")
    err = PhaseRegressionError("inference_expected", "selection", "bad selection cut", result)
    assert err.detected_phase == "inference_expected"
    assert err.origin_phase == "selection"
    assert err.symptom == "bad selection cut"
    assert err.result is result


# --------------------------------------------------- graph consistency gating


@pytest.fixture
def graph_root(analysis_root, jfc_plan):
    """An analysis root with a graph and the entry node's artifact ingested."""
    from hepagent.agents.jfc.graph_builder import bootstrap_graph, ingest_node

    strategy = jfc_plan.require_node("strategy")
    (analysis_root / strategy.artifact_path).write_text("strategy")
    bootstrap_graph(analysis_root, jfc_plan)
    ingest_node(analysis_root, "strategy")
    return analysis_root


def _open_commitment(root):
    from hepagent.agents.jfc.graph_builder import ingest_node

    (root / "COMMITMENTS.md").write_text(
        COMMITMENTS_HEADER + "| D2 | Generator comparison | pending | | |\n"
    )
    ingest_node(root, "strategy")


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
async def test_graph_errors_downgrade_a_reviewer_pass_to_iterate(graph_root, jfc_plan):
    """An unclosed commitment is a fact: the phase cannot pass on it."""
    from unittest.mock import AsyncMock, patch

    from hepagent.agents.jfc.review_gate import run_review_gate

    _open_commitment(graph_root)
    # Phase 2 has no arbiter, so the verdict comes from reviewer files.
    (graph_root / "phase2_exploration" / "review").mkdir(parents=True, exist_ok=True)
    (graph_root / "phase2_exploration" / "review" / "plot_validation.md").write_text("fine\n\nPASS")

    with patch("hepagent.agents.jfc.review_gate._run_single_reviewer", new_callable=AsyncMock):
        result = await run_review_gate(jfc_plan.require_node("exploration"), graph_root)

    assert result.verdict == "ITERATE"
    assert any("[graph]" in f for f in result.category_a_findings)
    assert result.graph_blocking


@pytest.mark.asyncio
async def test_a_consistent_graph_leaves_the_reviewer_verdict_alone(graph_root, jfc_plan):
    from unittest.mock import AsyncMock, patch

    from hepagent.agents.jfc.review_gate import run_review_gate

    (graph_root / "phase2_exploration" / "review").mkdir(parents=True, exist_ok=True)
    (graph_root / "phase2_exploration" / "review" / "plot_validation.md").write_text("fine\n\nPASS")

    with patch("hepagent.agents.jfc.review_gate._run_single_reviewer", new_callable=AsyncMock):
        result = await run_review_gate(jfc_plan.require_node("exploration"), graph_root)

    assert result.verdict == "PASS"
    assert result.graph_blocking == []


@pytest.mark.asyncio
async def test_advisory_findings_do_not_downgrade_a_pass(graph_root, jfc_plan):
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
        result = await run_review_gate(jfc_plan.require_node("exploration"), graph_root)

    assert result.verdict == "PASS"
    assert result.graph_blocking == []
    assert any("looks fine" in f for f in result.graph_findings)


def test_reviewer_prompts_carry_the_graph_section(graph_root, jfc_plan):
    from hepagent.agents.jfc.reviewers import create_arbiter, create_critical_reviewer

    critical = create_critical_reviewer(jfc_plan.require_node("strategy"), graph_root).instructions
    assert "ANALYSIS GRAPH" in critical
    assert "Checks the graph lets you make" in critical

    arbiter = create_arbiter(jfc_plan.require_node("strategy"), graph_root).instructions
    assert "ANALYSIS GRAPH" in arbiter
    assert "enforced in code" in arbiter


def test_reviewer_prompt_omits_the_graph_section_when_there_is_none(tmp_path, jfc_plan):
    from hepagent.agents.jfc.reviewers import create_critical_reviewer

    bare = tmp_path / "bare"
    bare.mkdir()
    save_plan(bare, jfc_plan)
    strategy = jfc_plan.require_node("strategy")
    assert "ANALYSIS GRAPH" not in create_critical_reviewer(strategy, bare).instructions


@pytest.mark.asyncio
async def test_a_note_citing_a_nonexistent_figure_blocks_the_node(graph_root, jfc_plan):
    """The note writer gets a figure manifest, R6 enforces it.

    R6 finds notes through the plan's `produces_note` nodes, so the check follows
    a renamed note artifact rather than a fixed filename glob.
    """
    from unittest.mock import AsyncMock, patch

    from hepagent.agents.jfc.review_gate import run_review_gate

    inference = jfc_plan.require_node("inference_expected")
    note = graph_root / inference.note_path
    note.parent.mkdir(parents=True, exist_ok=True)
    note.write_text("![A plot that was never made.](figures/ghost.png)")
    (graph_root / inference.directory / "review" / "plot_validation.md").write_text("fine\n\nPASS")

    with patch("hepagent.agents.jfc.review_gate._run_single_reviewer", new_callable=AsyncMock):
        result = await run_review_gate(jfc_plan.require_node("exploration"), graph_root)

    assert result.verdict == "ITERATE"
    assert any("ghost.png" in f for f in result.graph_blocking)


@pytest.mark.asyncio
async def test_a_file_that_is_no_nodes_note_is_not_checked_as_one(graph_root, jfc_plan):
    """A stray markdown file in an outputs directory is not an analysis note."""
    from unittest.mock import AsyncMock, patch

    from hepagent.agents.jfc.review_gate import run_review_gate

    stray = graph_root / "phase2_exploration" / "outputs" / "SCRATCH_NOTES.md"
    stray.parent.mkdir(parents=True, exist_ok=True)
    stray.write_text("![A plot that was never made.](figures/ghost.png)")
    (graph_root / "phase2_exploration" / "review" / "plot_validation.md").write_text("fine\n\nPASS")

    with patch("hepagent.agents.jfc.review_gate._run_single_reviewer", new_callable=AsyncMock):
        result = await run_review_gate(jfc_plan.require_node("exploration"), graph_root)

    assert result.verdict == "PASS"
