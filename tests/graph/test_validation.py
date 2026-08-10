"""Tests for the graph consistency rules."""

import json

import pytest

from hepagent.graph import validation as V
from hepagent.graph.schema import Edge, Node
from hepagent.graph.store import AnalysisGraph


@pytest.fixture
def graph(tmp_path):
    (tmp_path / "phase1_strategy" / "outputs").mkdir(parents=True)
    (tmp_path / "phase1_strategy" / "outputs" / "STRATEGY.md").write_text("strategy")
    g = AnalysisGraph(tmp_path)
    g.ensure_dir()
    g.add_node(
        Node(
            id="artifact:strategy",
            type="artifact",
            label="STRATEGY.md",
            content_ref="phase1_strategy/outputs/STRATEGY.md",
            phase="1",
        )
    )
    return g


def test_clean_graph_reports_no_errors(graph):
    report = V.validate(graph)
    assert report.ok
    assert report.errors == []


def test_r1_flags_dangling_edge_written_directly_to_disk(graph):
    # bypass add_edge's validation to simulate a hand-edited or corrupt log
    with open(graph.edges_path, "a", encoding="utf-8") as handle:
        handle.write(
            json.dumps(
                {"src": "artifact:strategy", "dst": "artifact:ghost", "type": "derives_from"}
            )
            + "\n"
        )
    reloaded = AnalysisGraph.load(graph.root)
    findings = V.rule_schema_integrity(reloaded)
    assert any(f.rule == "R1-schema" and "artifact:ghost" in f.message for f in findings)


def test_r2_flags_commitment_with_no_closing_edge(graph):
    graph.add_node(Node(id="commitment:D1", type="commitment", label="D1 unfold with IBU"))
    findings = V.rule_commitments_closed(graph)
    assert len(findings) == 1
    assert findings[0].node_id == "commitment:D1"
    assert findings[0].severity == "error"


def test_r2_accepts_a_resolved_commitment(graph):
    graph.add_node(Node(id="commitment:D1", type="commitment", label="D1"))
    graph.add_node(Node(id="evidence:closure", type="evidence", label="chi2/ndf = 1.3/36"))
    graph.add_edge(
        Edge(
            src="commitment:D1",
            dst="evidence:closure",
            type="resolves",
            evidence_ref="phase3_selection/outputs/results/closure.json",
        )
    )
    assert V.rule_commitments_closed(graph) == []


def test_r2b_requires_evidence_on_a_downscope(graph):
    graph.add_node(Node(id="commitment:D2", type="commitment", label="D2 generator comparison"))
    graph.add_node(Node(id="evidence:none", type="evidence", label="generator unavailable"))
    graph.add_edge(Edge(src="commitment:D2", dst="evidence:none", type="downscopes"))
    findings = V.rule_downscopes_justified(graph)
    assert any("downscoped without a documented reason" in f.message for f in findings)


def test_an_open_commitment_does_not_block_an_ordinary_node_review(graph):
    """The strategy node declares commitments that later nodes close.

    Blocking its own review on them made it unpassable: every iteration
    re-reported the commitments it had just written, until the limit ran out.
    """
    graph.add_node(Node(id="commitment:D1", type="commitment", label="D1 unfold with IBU"))

    review = V.validate(graph, rules=V.REVIEW_RULES)
    assert review.blocking == []

    # The commitments gate is where closure is actually due, and still blocks.
    assert V.validate_commitments(graph).blocking


def test_an_unjustified_downscope_blocks_a_node_review(graph):
    """Unlike an open commitment, this is wrong the moment it is written."""
    graph.add_node(Node(id="commitment:D2", type="commitment", label="D2"))
    graph.add_node(Node(id="evidence:none", type="evidence", label="unavailable"))
    graph.add_edge(Edge(src="commitment:D2", dst="evidence:none", type="downscopes"))

    blocking = V.validate(graph, rules=V.REVIEW_RULES).blocking
    assert [f.rule for f in blocking] == ["R2b-downscope"]


def test_r3_flags_a_figure_with_no_lineage(graph, tmp_path):
    figures = tmp_path / "phase2_exploration" / "outputs" / "figures"
    figures.mkdir(parents=True)
    (figures / "mjj.png").write_text("png")
    graph.add_node(
        Node(
            id="figure:mjj",
            type="figure",
            label="mjj.png",
            content_ref="phase2_exploration/outputs/figures/mjj.png",
            phase="2",
        )
    )
    findings = V.rule_provenance(graph)
    assert [f.node_id for f in findings] == ["figure:mjj"]
    assert findings[0].severity == "error"


def test_r3_exempts_the_phase1_artifact(graph):
    # the strategy artifact has no upstream by construction
    assert V.rule_provenance(graph) == []


def test_r4_flags_a_node_pointing_at_a_missing_file(graph):
    graph.add_node(
        Node(
            id="artifact:ghost",
            type="artifact",
            label="GHOST.md",
            content_ref="phase5_documentation/outputs/GHOST.md",
        )
    )
    findings = V.rule_content_exists(graph)
    assert any(f.node_id == "artifact:ghost" and f.severity == "error" for f in findings)


def test_r4_warns_when_content_ref_is_absent(graph):
    graph.add_node(Node(id="evidence:vague", type="evidence", label="looks fine"))
    findings = V.rule_content_exists(graph)
    assert any(f.node_id == "evidence:vague" and f.severity == "warning" for f in findings)


def test_r5_flags_a_commitment_dropped_from_the_current_graph(graph):
    graph.add_node(Node(id="commitment:D1", type="commitment", label="D1 unfold with IBU"))
    # Simulate deletion: history keeps the record, the live index does not.
    reloaded = AnalysisGraph.load(graph.root)
    reloaded._nodes.pop("commitment:D1")
    findings = V.rule_no_silent_deletion(reloaded)
    assert any("may not be silently deleted" in f.message for f in findings)


def test_r5_passes_when_the_commitment_is_still_present(graph):
    graph.add_node(Node(id="commitment:D1", type="commitment", label="D1"))
    assert V.rule_no_silent_deletion(graph) == []


def test_validate_commitments_runs_only_the_commitment_rule(graph):
    graph.add_node(Node(id="commitment:D1", type="commitment", label="D1"))
    graph.add_node(
        Node(id="artifact:ghost", type="artifact", label="G", content_ref="missing/file.md")
    )
    report = V.validate_commitments(graph)
    assert all(f.rule == "R2-commitments" for f in report.findings)


def test_report_markdown_renders_a_table(graph):
    graph.add_node(Node(id="commitment:D1", type="commitment", label="D1 | with a pipe"))
    report = V.validate(graph)
    markdown = report.to_markdown()
    assert "| Severity | Rule | Node | Finding |" in markdown
    assert "\\|" in markdown  # the pipe in the label is escaped
    assert not report.ok


def test_report_markdown_when_clean(graph):
    assert "No findings" in V.validate(graph).to_markdown()


# ------------------------------------------------------- R6 figure references


def write_note(root, phase_dir, name, body):
    path = root / phase_dir / "outputs" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)
    return path


def test_r6_accepts_a_reference_to_a_declared_figure(graph, tmp_path):
    figures = tmp_path / "phase2_exploration" / "outputs" / "figures"
    figures.mkdir(parents=True)
    (figures / "mjj.png").write_text("png")
    graph.add_node(
        Node(
            id="figure:mjj",
            type="figure",
            label="mjj.png",
            content_ref="phase2_exploration/outputs/figures/mjj.png",
            phase="2",
        )
    )
    write_note(
        tmp_path,
        "phase2_exploration",
        "ANALYSIS_NOTE_4a_v1.md",
        "![Dijet mass.](figures/mjj.png)",
    )
    assert V.rule_figure_references(graph) == []


def test_r6_flags_a_reference_to_a_figure_that_does_not_exist(graph, tmp_path):
    write_note(
        tmp_path,
        "phase2_exploration",
        "ANALYSIS_NOTE_4a_v1.md",
        "![Dijet mass.](figures/ghost.png)",
    )
    findings = V.rule_figure_references(graph)
    assert any("does not exist on disk" in f.message for f in findings)
    assert all(f.severity == "error" for f in findings)


def test_r6_flags_a_figure_present_on_disk_but_absent_from_the_graph(graph, tmp_path):
    figures = tmp_path / "phase2_exploration" / "outputs" / "figures"
    figures.mkdir(parents=True)
    (figures / "rogue.png").write_text("png")
    write_note(
        tmp_path,
        "phase2_exploration",
        "ANALYSIS_NOTE_4a_v1.md",
        "![Rogue plot.](figures/rogue.png)",
    )
    findings = V.rule_figure_references(graph)
    assert any("has no node in the graph" in f.message for f in findings)


def test_r6_reads_latex_includegraphics_too(graph, tmp_path):
    write_note(
        tmp_path,
        "phase5_documentation",
        "ANALYSIS_NOTE_5_v1.md",
        r"\includegraphics[width=0.5\textwidth]{figures/ghost.pdf}",
    )
    findings = V.rule_figure_references(graph)
    assert any("ghost.pdf" in f.message for f in findings)


def test_r6_ignores_remote_images(graph, tmp_path):
    write_note(
        tmp_path,
        "phase5_documentation",
        "ANALYSIS_NOTE_5_v1.md",
        "![Logo.](https://example.org/logo.png)",
    )
    assert V.rule_figure_references(graph) == []


def test_r6_flags_a_reference_escaping_the_analysis_root(graph, tmp_path):
    write_note(
        tmp_path,
        "phase5_documentation",
        "ANALYSIS_NOTE_5_v1.md",
        "![Outside.](../../../etc/hosts.png)",
    )
    findings = V.rule_figure_references(graph)
    assert any("not a file under the analysis root" in f.message for f in findings)


def test_r6_ignores_files_that_are_not_analysis_notes(graph, tmp_path):
    write_note(tmp_path, "phase3_selection", "SELECTION.md", "![x](figures/ghost.png)")
    assert V.rule_figure_references(graph) == []


# ------------------------------------------------------------ gating subsets


def test_blocking_is_every_error_regardless_of_rule(graph, tmp_path):
    """Severity is the gate: an error is a fact about the analysis, whatever found it."""
    graph.add_node(Node(id="commitment:D1", type="commitment", label="D1 open"))
    write_note(tmp_path, "phase5_documentation", "ANALYSIS_NOTE_5_v1.md", "![x](figures/ghost.png)")
    report = V.validate(graph)

    assert {f.rule for f in report.blocking} == {"R2-commitments", "R6-figure-refs"}
    assert all(f.severity == "error" for f in report.blocking)
    assert len(report.blocking) + len(report.advisory) == len(report.findings)


def test_validate_gating_returns_only_errors(graph, tmp_path):
    graph.add_node(Node(id="commitment:D1", type="commitment", label="D1 open"))
    graph.add_node(Node(id="evidence:vague", type="evidence", label="no content_ref"))
    gating = V.validate_gating(graph)

    assert [f.rule for f in gating.findings] == ["R2-commitments"]
    assert all(f.severity == "error" for f in gating.findings)


def test_warnings_are_advisory_and_do_not_block(graph):
    """A node with no content_ref is a judgement call, not a blocking fact."""
    graph.add_node(Node(id="evidence:vague", type="evidence", label="looks fine"))
    report = V.validate(graph)

    assert report.blocking == []
    assert [f.rule for f in report.advisory] == ["R4-content"]
