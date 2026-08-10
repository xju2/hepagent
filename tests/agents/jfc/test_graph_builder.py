"""Tests for deterministic ingestion of a JFC analysis directory into the graph."""

import json

import pytest

from hepagent.agents.jfc.graph_builder import (
    artifact_rel_path,
    bootstrap_graph,
    ingest_phase,
    ingest_review,
    rebuild,
)
from hepagent.graph.query import what_produced
from hepagent.graph.store import AnalysisGraph
from hepagent.graph.validation import validate

PHASE_DIRS = [
    "phase1_strategy",
    "phase2_exploration",
    "phase3_selection",
    "phase4a_inference_expected",
    "phase4b_inference_partial",
    "phase4c_inference_observed",
    "phase5_documentation",
]

COMMITMENTS_HEADER = (
    "# Phase 1 Commitments\n\n"
    "| ID | Commitment | Status | Evidence | Phase Resolved |\n"
    "|----|-----------|--------|----------|---------------|\n"
)


@pytest.fixture
def analysis_root(tmp_path):
    """A scaffolded-looking analysis directory with a bootstrapped graph."""
    root = tmp_path / "zbb"
    for phase in PHASE_DIRS:
        for sub in ("outputs", "outputs/figures", "src", "review", "logs"):
            (root / phase / sub).mkdir(parents=True, exist_ok=True)
    (root / "prompt.md").write_text("# Physics Prompt\n\nMeasure the Z→bb cross-section.\n")
    (root / "COMMITMENTS.md").write_text(COMMITMENTS_HEADER)
    bootstrap_graph(root, "zbb", "measurement", "Measure the Z→bb cross-section.")
    return root


def write_artifact(root, phase, body="content"):
    path = root / artifact_rel_path(phase)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)
    return path


# ------------------------------------------------------------------ bootstrap


def test_bootstrap_creates_problem_root_and_pending_artifacts(analysis_root):
    graph = AnalysisGraph.load(analysis_root)
    assert len(graph.nodes(type="problem")) == 1
    assert len(graph.nodes(type="analysis_root")) == 1
    assert len(graph.nodes(type="artifact")) == 7
    assert all(a.status == "pending" for a in graph.nodes(type="artifact"))


def test_bootstrap_label_uses_the_prompt_first_line(analysis_root):
    graph = AnalysisGraph.load(analysis_root)
    assert graph.nodes(type="problem")[0].label == "Measure the Z→bb cross-section."


def test_bootstrap_chains_requires_from_upstream_artifacts(analysis_root):
    graph = AnalysisGraph.load(analysis_root)
    inference = graph.get_node("artifact:phase4a_inference_expected/outputs/INFERENCE_EXPECTED.md")
    prerequisites = {e.dst for e in graph.out_edges(inference.id, type="requires")}
    assert "artifact:phase3_selection/outputs/SELECTION.md" in prerequisites
    assert "artifact:phase1_strategy/outputs/STRATEGY.md" in prerequisites


def test_bootstrap_points_phase1_at_the_problem(analysis_root):
    graph = AnalysisGraph.load(analysis_root)
    strategy = graph.get_node("artifact:phase1_strategy/outputs/STRATEGY.md")
    prerequisites = {e.dst for e in graph.out_edges(strategy.id, type="requires")}
    assert prerequisites == {"problem:zbb"}


def test_bootstrap_is_idempotent(analysis_root):
    before = (analysis_root / "graph" / "nodes.jsonl").read_text()
    bootstrap_graph(analysis_root, "zbb", "measurement", "Measure the Z→bb cross-section.")
    assert (analysis_root / "graph" / "nodes.jsonl").read_text() == before


def test_bootstrap_does_not_downgrade_an_ingested_artifact(analysis_root):
    """A pending placeholder must never overwrite an artifact already produced."""
    write_artifact(analysis_root, 1)
    ingest_phase(analysis_root, 1)
    assert (
        AnalysisGraph.load(analysis_root)
        .get_node("artifact:phase1_strategy/outputs/STRATEGY.md")
        .status
        == "active"
    )

    before = (analysis_root / "graph" / "nodes.jsonl").read_text()
    bootstrap_graph(analysis_root, "zbb", "measurement", "Measure the Z→bb cross-section.")

    assert (analysis_root / "graph" / "nodes.jsonl").read_text() == before
    assert (
        AnalysisGraph.load(analysis_root)
        .get_node("artifact:phase1_strategy/outputs/STRATEGY.md")
        .status
        == "active"
    )


def test_bootstrap_then_rebuild_is_idempotent(analysis_root):
    """The `jfc graph rebuild` path: bootstrap followed by rebuild, repeated."""
    write_artifact(analysis_root, 1)
    write_artifact(analysis_root, 2)
    write_review(analysis_root, "phase1_strategy", "ADJUDICATION.md", "ok\n\nPASS")

    def pass_once():
        bootstrap_graph(analysis_root, "zbb", "measurement", "Measure the Z→bb cross-section.")
        rebuild(analysis_root)
        return (
            (analysis_root / "graph" / "nodes.jsonl").read_text(),
            (analysis_root / "graph" / "edges.jsonl").read_text(),
        )

    first = pass_once()
    assert pass_once() == first
    assert pass_once() == first


# -------------------------------------------------------------- ingest_phase


def test_ingest_phase_records_artifact_and_lineage(analysis_root):
    write_artifact(analysis_root, 1)
    write_artifact(analysis_root, 2)
    ingest_phase(analysis_root, 1)
    ingest_phase(analysis_root, 2)

    graph = AnalysisGraph.load(analysis_root)
    exploration = graph.get_node("artifact:phase2_exploration/outputs/EXPLORATION.md")
    assert exploration.status == "active"
    lineage = {e.dst for e in graph.out_edges(exploration.id, type="derives_from")}
    assert lineage == {"artifact:phase1_strategy/outputs/STRATEGY.md"}


def test_ingest_phase_reports_a_missing_artifact_without_raising(analysis_root):
    report = ingest_phase(analysis_root, 3)
    assert any("not on disk yet" in note for note in report.skipped)


def test_ingest_phase_records_figures_linked_to_the_artifact(analysis_root):
    write_artifact(analysis_root, 2)
    figures = analysis_root / "phase2_exploration" / "outputs" / "figures"
    (figures / "mjj.png").write_text("png")
    (figures / "notes.txt").write_text("not a figure")
    ingest_phase(analysis_root, 2)

    graph = AnalysisGraph.load(analysis_root)
    assert [n.label for n in graph.nodes(type="figure")] == ["mjj.png"]
    figure = graph.get_node("figure:phase2_exploration/outputs/figures/mjj.png")
    assert graph.out_edges(figure.id, type="derives_from")[0].dst.endswith("EXPLORATION.md")


def test_ingest_phase_records_results_json_as_evidence(analysis_root):
    write_artifact(analysis_root, 3)
    results = analysis_root / "phase3_selection" / "outputs" / "results"
    results.mkdir(parents=True)
    (results / "closure.json").write_text(json.dumps({"chi2": 1.3, "ndf": 36}))
    ingest_phase(analysis_root, 3)

    graph = AnalysisGraph.load(analysis_root)
    evidence = graph.get_node("evidence:phase3_selection/outputs/results/closure.json")
    assert evidence.metadata["keys"] == ["chi2", "ndf"]
    assert graph.out_edges(evidence.id, type="supports")[0].dst.endswith("SELECTION.md")


def test_ingest_phase_records_scripts_as_executions(analysis_root):
    write_artifact(analysis_root, 3)
    (analysis_root / "phase3_selection" / "src" / "select.py").write_text("print('x')")
    ingest_phase(analysis_root, 3)

    graph = AnalysisGraph.load(analysis_root)
    assert [n.label for n in graph.nodes(type="execution")] == ["select.py"]


def test_ingest_phase_records_commitments_with_closing_evidence(analysis_root):
    write_artifact(analysis_root, 1)
    (analysis_root / "COMMITMENTS.md").write_text(
        COMMITMENTS_HEADER
        + "| D1 | Unfold with IBU | resolved | closure chi2/ndf = 1.3/36 | 3 |\n"
        + "| D2 | Generator comparison | pending | | |\n"
        + "| D3 | Alt tagger | downscoped | tagger unavailable in this campaign | 3 |\n"
    )
    ingest_phase(analysis_root, 1)

    graph = AnalysisGraph.load(analysis_root)
    assert {n.id for n in graph.nodes(type="commitment")} == {
        "commitment:D1",
        "commitment:D2",
        "commitment:D3",
    }
    assert graph.out_edges("commitment:D1", type="resolves")
    assert graph.out_edges("commitment:D3", type="downscopes")
    assert not graph.out_edges("commitment:D2", type="resolves")
    # the strategy artifact is what committed to them
    assert graph.out_edges("artifact:phase1_strategy/outputs/STRATEGY.md", type="commits_to")


def test_ingest_phase_is_idempotent(analysis_root):
    write_artifact(analysis_root, 1)
    (analysis_root / "phase1_strategy" / "outputs" / "figures" / "flagship.png").write_text("png")
    ingest_phase(analysis_root, 1)
    first = (analysis_root / "graph" / "nodes.jsonl").read_text()
    edges_first = (analysis_root / "graph" / "edges.jsonl").read_text()

    ingest_phase(analysis_root, 1)
    assert (analysis_root / "graph" / "nodes.jsonl").read_text() == first
    assert (analysis_root / "graph" / "edges.jsonl").read_text() == edges_first


def test_ingest_phase_ignores_an_unknown_phase(analysis_root):
    report = ingest_phase(analysis_root, "99z")
    assert report.nodes == []
    assert any("Unknown phase" in note for note in report.skipped)


# ------------------------------------------------------------- ingest_review


def write_review(analysis_root, phase_dir, name, body):
    path = analysis_root / phase_dir / "review" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)
    return path


def test_ingest_review_attaches_reviewer_documents(analysis_root):
    write_artifact(analysis_root, 1)
    ingest_phase(analysis_root, 1)
    write_review(analysis_root, "phase1_strategy", "critical_review.md", "findings\n\nPASS")
    ingest_review(analysis_root, 1)

    graph = AnalysisGraph.load(analysis_root)
    review = graph.get_node("review:phase1_strategy/review/critical_review.md")
    assert review is not None
    assert graph.out_edges("artifact:phase1_strategy/outputs/STRATEGY.md", type="reviewed_by")


def test_ingest_review_pass_verdict_approves_the_artifact(analysis_root):
    write_artifact(analysis_root, 1)
    ingest_phase(analysis_root, 1)
    write_review(analysis_root, "phase1_strategy", "ADJUDICATION.md", "All good.\n\nPASS")
    ingest_review(analysis_root, 1)

    graph = AnalysisGraph.load(analysis_root)
    decision = graph.get_node("decision:phase1_strategy/review/ADJUDICATION.md")
    assert decision.metadata["verdict"] == "PASS"
    approved = graph.out_edges("artifact:phase1_strategy/outputs/STRATEGY.md", type="approved_by")
    assert approved and approved[0].dst == decision.id


def test_ingest_review_iterate_verdict_invalidates_the_artifact(analysis_root):
    write_artifact(analysis_root, 1)
    ingest_phase(analysis_root, 1)
    write_review(
        analysis_root,
        "phase1_strategy",
        "ADJUDICATION.md",
        "| A | x | Missing systematics table |\n\nITERATE",
    )
    ingest_review(analysis_root, 1)

    graph = AnalysisGraph.load(analysis_root)
    decision_id = "decision:phase1_strategy/review/ADJUDICATION.md"
    invalidated = graph.out_edges(decision_id, type="invalidates")
    assert invalidated[0].dst == "artifact:phase1_strategy/outputs/STRATEGY.md"
    assert "Missing systematics table" in invalidated[0].evidence_ref


def test_ingest_review_regress_verdict_points_at_the_origin_phase(analysis_root):
    write_artifact(analysis_root, 3)
    write_artifact(analysis_root, 1)
    ingest_phase(analysis_root, 1)
    ingest_phase(analysis_root, 3)
    write_review(
        analysis_root,
        "phase3_selection",
        "ADJUDICATION.md",
        "Root cause is the Phase 1 fiducial definition.\n\nREGRESS(1)",
    )
    ingest_review(analysis_root, 3)

    graph = AnalysisGraph.load(analysis_root)
    decision_id = "decision:phase3_selection/review/ADJUDICATION.md"
    regressed = graph.out_edges(decision_id, type="regresses_to")
    assert regressed[0].dst == "artifact:phase1_strategy/outputs/STRATEGY.md"
    # the phase under review is still rejected
    assert graph.out_edges(decision_id, type="invalidates")


def test_ingest_review_attaches_to_a_declared_artifact_before_it_is_produced(analysis_root):
    """The bootstrapped (pending) artifact node is enough to hang a review on."""
    write_review(analysis_root, "phase2_exploration", "plot_validation.md", "fine\n\nPASS")
    ingest_review(analysis_root, 2)

    graph = AnalysisGraph.load(analysis_root)
    assert graph.get_node("review:phase2_exploration/review/plot_validation.md") is not None
    assert graph.out_edges("artifact:phase2_exploration/outputs/EXPLORATION.md", type="reviewed_by")


def test_ingest_review_without_a_graph_records_the_review_but_no_edges(tmp_path):
    """With no bootstrapped graph the review is still captured, just unlinked."""
    root = tmp_path / "bare"
    write_review(root, "phase2_exploration", "plot_validation.md", "fine\n\nPASS")
    report = ingest_review(root, 2)

    graph = AnalysisGraph.load(root)
    assert graph.get_node("review:phase2_exploration/review/plot_validation.md") is not None
    assert report.edges == []
    assert any("artifact node missing" in note for note in report.skipped)


# ------------------------------------------------------------------- rebuild


def test_rebuild_reconstructs_the_graph_from_disk_alone(analysis_root):
    write_artifact(analysis_root, 1)
    write_artifact(analysis_root, 2)
    (analysis_root / "phase2_exploration" / "outputs" / "figures" / "mjj.png").write_text("png")
    write_review(analysis_root, "phase1_strategy", "ADJUDICATION.md", "ok\n\nPASS")

    # Delete the graph entirely, then rebuild from artifacts.
    (analysis_root / "graph" / "nodes.jsonl").unlink()
    (analysis_root / "graph" / "edges.jsonl").unlink()
    bootstrap_graph(analysis_root, "zbb", "measurement", "Measure the Z→bb cross-section.")
    rebuild(analysis_root)

    graph = AnalysisGraph.load(analysis_root)
    node, lineage = what_produced(graph, "mjj.png")
    assert node.id == "figure:phase2_exploration/outputs/figures/mjj.png"
    assert [n.label for n in lineage] == ["EXPLORATION.md", "STRATEGY.md"]


def test_rebuild_is_idempotent(analysis_root):
    """A rebuild over an unchanged directory must not append a single line.

    Reviews are included deliberately: an adjudication node is written with a
    verdict-bearing label, and an earlier version wrote it twice per pass, so
    the log grew on every rebuild.
    """
    write_artifact(analysis_root, 1)
    write_artifact(analysis_root, 2)
    (analysis_root / "phase2_exploration" / "outputs" / "figures" / "mjj.png").write_text("png")
    write_review(analysis_root, "phase1_strategy", "critical_review.md", "findings\n\nPASS")
    write_review(analysis_root, "phase1_strategy", "ADJUDICATION.md", "ok\n\nPASS")
    write_review(
        analysis_root, "phase2_exploration", "ADJUDICATION.md", "| A | x | bad plot |\n\nITERATE"
    )

    rebuild(analysis_root)
    first_nodes = (analysis_root / "graph" / "nodes.jsonl").read_text()
    first_edges = (analysis_root / "graph" / "edges.jsonl").read_text()

    rebuild(analysis_root)
    assert (analysis_root / "graph" / "nodes.jsonl").read_text() == first_nodes
    assert (analysis_root / "graph" / "edges.jsonl").read_text() == first_edges


def test_ingest_review_is_idempotent(analysis_root):
    write_artifact(analysis_root, 1)
    ingest_phase(analysis_root, 1)
    write_review(analysis_root, "phase1_strategy", "ADJUDICATION.md", "ok\n\nPASS")

    ingest_review(analysis_root, 1)
    lines = (analysis_root / "graph" / "nodes.jsonl").read_text()
    ingest_review(analysis_root, 1)
    assert (analysis_root / "graph" / "nodes.jsonl").read_text() == lines


def test_rebuilt_graph_with_an_open_commitment_fails_validation(analysis_root):
    write_artifact(analysis_root, 1)
    (analysis_root / "COMMITMENTS.md").write_text(
        COMMITMENTS_HEADER + "| D2 | Generator comparison | pending | | |\n"
    )
    rebuild(analysis_root)

    report = validate(AnalysisGraph.load(analysis_root))
    assert not report.ok
    assert any(f.node_id == "commitment:D2" for f in report.errors)
