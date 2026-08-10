"""Tests for the graph renderings injected into agent prompts."""

import json

import pytest

from hepagent.graph import report
from hepagent.graph.schema import Edge, Node
from hepagent.graph.store import AnalysisGraph


@pytest.fixture
def graph(tmp_path):
    results = tmp_path / "phase3_selection" / "outputs" / "results"
    figures = tmp_path / "phase3_selection" / "outputs" / "figures"
    results.mkdir(parents=True)
    figures.mkdir(parents=True)
    (results / "closure.json").write_text(
        json.dumps({"chi2": 1.3, "ndf": 36, "fit": {"mu": 1.02, "err": 0.07}})
    )
    (figures / "mjj.png").write_text("png")

    g = AnalysisGraph(tmp_path)
    g.ensure_dir()
    g.add_node(
        Node(
            id="artifact:sel",
            type="artifact",
            label="SELECTION.md",
            content_ref="phase3_selection/outputs/SELECTION.md",
            phase="3",
        )
    )
    g.add_node(
        Node(
            id="figure:mjj",
            type="figure",
            label="mjj.png",
            content_ref="phase3_selection/outputs/figures/mjj.png",
            phase="3",
        )
    )
    g.add_node(
        Node(
            id="evidence:closure",
            type="evidence",
            label="closure.json",
            content_ref="phase3_selection/outputs/results/closure.json",
            phase="3",
        )
    )
    g.add_node(Node(id="commitment:D1", type="commitment", label="D1 unfold with IBU"))
    g.add_node(Node(id="commitment:D2", type="commitment", label="D2 generator comparison"))
    g.add_node(Node(id="evidence:c1", type="evidence", label="closure passed"))
    g.add_edge(Edge(src="figure:mjj", dst="artifact:sel", type="derives_from"))
    g.add_edge(Edge(src="evidence:closure", dst="artifact:sel", type="supports"))
    g.add_edge(
        Edge(
            src="commitment:D1",
            dst="evidence:c1",
            type="resolves",
            evidence_ref="chi2/ndf = 1.3/36",
        )
    )
    return g


def test_evidence_digest_flattens_nested_values(graph):
    digest = report.evidence_digest(graph)
    assert "chi2 = 1.3" in digest
    assert "fit.mu = 1.02" in digest
    assert "single source of truth" in digest


def test_evidence_digest_when_no_results(tmp_path):
    assert "No machine-readable results" in report.evidence_digest(AnalysisGraph(tmp_path))


def test_evidence_digest_filters_by_phase(graph):
    assert "closure.json" in report.evidence_digest(graph, phase="3")
    assert "closure.json" not in report.evidence_digest(graph, phase="5")


def test_figure_manifest_lists_paths_and_origin(graph):
    manifest = report.figure_manifest(graph)
    assert "phase3_selection/outputs/figures/mjj.png" in manifest
    assert "produced by SELECTION.md" in manifest
    assert "[MISSING]" not in manifest


def test_figure_manifest_flags_a_missing_file(graph):
    graph.add_node(
        Node(
            id="figure:ghost",
            type="figure",
            label="ghost.png",
            content_ref="phase3_selection/outputs/figures/ghost.png",
            phase="3",
        )
    )
    assert "[MISSING]" in report.figure_manifest(graph)


def test_commitment_ledger_distinguishes_open_from_closed(graph):
    ledger = report.commitment_ledger(graph)
    assert "chi2/ndf = 1.3/36" in ledger
    assert "still open" in ledger


def test_commitment_ledger_when_empty(tmp_path):
    assert "No commitments" in report.commitment_ledger(AnalysisGraph(tmp_path))


def test_provenance_brief_lists_nodes_with_lineage(graph):
    brief = report.provenance_brief(graph, "3")
    assert "figure: mjj.png ← SELECTION.md" in brief


def test_provenance_brief_reports_rejections(graph):
    graph.add_node(Node(id="review:crit", type="review", label="critical_review.md", phase="3"))
    graph.add_edge(Edge(src="review:crit", dst="artifact:sel", type="invalidates"))
    brief = report.provenance_brief(graph, "3")
    assert "invalidated by critical_review.md" in brief


def test_provenance_brief_skips_pending_nodes(graph):
    graph.add_node(
        Node(id="artifact:future", type="artifact", label="FUTURE.md", status="pending", phase="3")
    )
    assert "FUTURE.md" not in report.provenance_brief(graph, "3")


def test_provenance_brief_for_an_unknown_phase(graph):
    assert "records nothing for phase 9" in report.provenance_brief(graph, "9")


def test_phase_brief_combines_every_section(graph):
    brief = report.phase_brief(graph, "3")
    for heading in ("what this phase has produced", "commitments", "figures available"):
        assert heading in brief


def test_note_brief_carries_figures_numbers_and_commitments(graph):
    brief = report.note_brief(graph)
    assert "figures you may reference" in brief
    assert "numbers you must quote exactly" in brief
    assert "chi2 = 1.3" in brief
    assert "still open" in brief


def test_long_arrays_are_summarised_not_dumped(graph, tmp_path):
    bins = tmp_path / "phase3_selection" / "outputs" / "results" / "bins.json"
    bins.write_text(json.dumps({"edges": list(range(100))}))
    graph.add_node(
        Node(
            id="evidence:bins",
            type="evidence",
            label="bins.json",
            content_ref="phase3_selection/outputs/results/bins.json",
            phase="3",
        )
    )
    digest = report.evidence_digest(graph)
    assert "edges[100]" in digest
    assert "50" not in digest.split("edges[100]")[1].split("\n")[0]


def test_unreadable_json_is_reported_not_raised(graph, tmp_path):
    broken = tmp_path / "phase3_selection" / "outputs" / "results" / "broken.json"
    broken.write_text("{not json")
    graph.add_node(
        Node(
            id="evidence:broken",
            type="evidence",
            label="broken.json",
            content_ref="phase3_selection/outputs/results/broken.json",
            phase="3",
        )
    )
    assert "unreadable" in report.evidence_digest(graph)
