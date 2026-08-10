"""Tests for graph traversal helpers."""

import pytest

from hepagent.graph import query
from hepagent.graph.schema import Edge, Node
from hepagent.graph.store import AnalysisGraph


@pytest.fixture
def analysis(tmp_path):
    """A small but realistic graph: strategy → selection → figure, plus a review."""
    root = tmp_path
    (root / "phase1_strategy" / "outputs").mkdir(parents=True)
    (root / "phase3_selection" / "outputs" / "figures").mkdir(parents=True)
    (root / "phase1_strategy" / "outputs" / "STRATEGY.md").write_text("strategy")
    (root / "phase3_selection" / "outputs" / "SELECTION.md").write_text("selection")
    (root / "phase3_selection" / "outputs" / "figures" / "mjj.png").write_text("png")

    g = AnalysisGraph(root)
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
    g.add_node(
        Node(
            id="artifact:selection",
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
        Node(id="evidence:closure", type="evidence", label="closure chi2/ndf = 1.3/36", phase="3")
    )
    g.add_node(Node(id="review:critical", type="review", label="critical_review.md", phase="3"))
    g.add_node(Node(id="commitment:D1", type="commitment", label="D1 unfold with IBU", phase="1"))
    g.add_node(Node(id="commitment:D2", type="commitment", label="D2 generator comparison"))

    g.add_edge(Edge(src="artifact:selection", dst="artifact:strategy", type="derives_from"))
    g.add_edge(Edge(src="figure:mjj", dst="artifact:selection", type="derives_from"))
    g.add_edge(Edge(src="evidence:closure", dst="artifact:selection", type="supports"))
    g.add_edge(Edge(src="review:critical", dst="artifact:selection", type="invalidates"))
    g.add_edge(Edge(src="commitment:D1", dst="evidence:closure", type="resolves"))
    return g


def test_ancestors_walks_lineage_transitively(analysis):
    lineage = [n.id for n in query.ancestors(analysis, "figure:mjj")]
    assert lineage == ["artifact:selection", "artifact:strategy"]


def test_descendants_walks_the_other_way(analysis):
    downstream = {n.id for n in query.descendants(analysis, "artifact:strategy")}
    assert downstream == {"artifact:selection", "figure:mjj"}


def test_what_produced_resolves_by_bare_filename(analysis):
    node, lineage = query.what_produced(analysis, "mjj.png")
    assert node.id == "figure:mjj"
    assert [n.id for n in lineage] == ["artifact:selection", "artifact:strategy"]


def test_what_produced_returns_none_for_unknown_file(analysis):
    node, lineage = query.what_produced(analysis, "nope.png")
    assert node is None
    assert lineage == []


def test_evidence_for_reads_supports_edges(analysis):
    assert [n.id for n in query.evidence_for(analysis, "artifact:selection")] == [
        "evidence:closure"
    ]


def test_rejections_reads_invalidates_edges(analysis):
    assert [n.id for n in query.rejections(analysis, "artifact:selection")] == ["review:critical"]


def test_a_decision_that_now_passes_no_longer_rejects(analysis):
    """Both review rounds write the same ADJUDICATION.md, so both get the same id.

    The PASS revision supersedes the decision node, but the `invalidates` edge
    written while it said ITERATE has a different key and stays in the log. Left
    unfiltered it kept the artifact rejected forever, which made
    `is_consistent_checkpoint` false and rewound resume past passing work.
    """
    art = "artifact:selection"
    decision = "decision:phase3_selection/review/ADJUDICATION.md"

    analysis.add_node(
        Node(
            id=decision,
            type="decision",
            label="ADJUDICATION.md — ITERATE",
            metadata={"verdict": "ITERATE"},
        )
    )
    analysis.add_edge(Edge(src=decision, dst=art, type="invalidates", evidence_ref="r"))
    assert decision in [n.id for n in query.rejections(analysis, art)]

    # Second round on the same file: same id, verdict now PASS.
    analysis.add_node(
        Node(
            id=decision,
            type="decision",
            label="ADJUDICATION.md — PASS",
            metadata={"verdict": "PASS"},
        )
    )
    analysis.add_edge(Edge(src=art, dst=decision, type="approved_by", evidence_ref="r"))

    assert decision not in [n.id for n in query.rejections(analysis, art)]
    # An unrelated reviewer's rejection is untouched.
    assert "review:critical" in [n.id for n in query.rejections(analysis, art)]


def test_unresolved_commitments_excludes_closed_ones(analysis):
    assert [n.id for n in query.unresolved_commitments(analysis)] == ["commitment:D2"]


def test_orphan_claims_finds_lineage_gaps(analysis):
    # strategy is the root artifact and has no upstream, so it is the only orphan
    assert [n.id for n in query.orphan_claims(analysis)] == ["artifact:strategy"]


def test_is_realized_checks_the_filesystem_for_file_backed_nodes(analysis):
    assert query.is_realized(analysis, analysis.get_node("figure:mjj"))

    analysis.add_node(
        Node(
            id="artifact:ghost",
            type="artifact",
            label="GHOST.md",
            content_ref="phase5_documentation/outputs/GHOST.md",
        )
    )
    assert not query.is_realized(analysis, analysis.get_node("artifact:ghost"))


def test_frontier_returns_nodes_whose_prerequisites_are_all_realized(analysis):
    analysis.add_node(
        Node(
            id="artifact:inference",
            type="artifact",
            label="INFERENCE_EXPECTED.md",
            content_ref="phase4a_inference_expected/outputs/INFERENCE_EXPECTED.md",
            phase="4a",
        )
    )
    analysis.add_node(
        Node(
            id="artifact:note",
            type="artifact",
            label="ANALYSIS_NOTE_5_v1.md",
            content_ref="phase5_documentation/outputs/ANALYSIS_NOTE_5_v1.md",
            phase="5",
        )
    )
    analysis.add_edge(Edge(src="artifact:inference", dst="artifact:selection", type="requires"))
    analysis.add_edge(Edge(src="artifact:note", dst="artifact:inference", type="requires"))

    ready = {n.id for n in query.frontier(analysis)}
    # inference is unrealized but its prerequisite (selection) exists on disk;
    # the note is blocked because inference does not exist yet.
    assert "artifact:inference" in ready
    assert "artifact:note" not in ready


def test_to_mermaid_renders_included_edges_only(analysis):
    diagram = query.to_mermaid(analysis, node_type="artifact")
    assert diagram.startswith("graph LR")
    assert "derives_from" in diagram
    assert "figure__mjj" not in diagram  # filtered out by node_type


def test_to_mermaid_truncates_and_says_so(analysis):
    diagram = query.to_mermaid(analysis, max_nodes=2)
    assert "truncated at 2 nodes" in diagram


def test_to_table_lists_nodes(analysis):
    table = query.to_table(analysis, node_type="figure")
    assert "mjj.png" in table
    assert "TYPE" in table


def test_to_table_handles_empty_graph(tmp_path):
    assert query.to_table(AnalysisGraph(tmp_path)) == "(graph is empty)"


def test_describe_includes_lineage_evidence_and_rejections(analysis):
    text = query.describe(analysis, analysis.get_node("artifact:selection"))
    assert "derives from:" in text
    assert "STRATEGY.md" in text
    assert "supported by:" in text
    assert "invalidated by:" in text


def test_describe_reports_missing_lineage(analysis):
    text = query.describe(analysis, analysis.get_node("artifact:strategy"))
    assert "(nothing recorded)" in text


def test_relative_to_root_strips_the_prefix(tmp_path):
    target = tmp_path / "phase1_strategy" / "outputs" / "STRATEGY.md"
    target.parent.mkdir(parents=True)
    target.write_text("x")
    assert query.relative_to_root(tmp_path, target) == "phase1_strategy/outputs/STRATEGY.md"


def test_relative_to_root_leaves_outside_paths_alone(tmp_path):
    assert query.relative_to_root(tmp_path, "/etc/hosts") == "/etc/hosts"
