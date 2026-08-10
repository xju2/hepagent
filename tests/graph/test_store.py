"""Tests for the append-only JSONL graph store."""

import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from hepagent.graph.schema import Edge, GraphSchemaError, Node
from hepagent.graph.store import AnalysisGraph


@pytest.fixture
def graph(tmp_path):
    g = AnalysisGraph(tmp_path)
    g.ensure_dir()
    return g


def test_ensure_dir_writes_readme(graph):
    assert graph.graph_dir.exists()
    assert (graph.graph_dir / "README.md").exists()


def test_load_of_missing_files_yields_empty_graph(tmp_path):
    g = AnalysisGraph.load(tmp_path / "nothing_here")
    assert len(g) == 0
    assert g.edges() == []


def test_add_node_persists_and_reloads(graph, tmp_path):
    graph.add_node(Node(id="artifact:a", type="artifact", label="Strategy", phase="1"))
    reloaded = AnalysisGraph.load(tmp_path)
    assert reloaded.get_node("artifact:a").label == "Strategy"


def test_add_node_is_idempotent_for_identical_content(graph):
    node = Node(id="artifact:a", type="artifact", label="Strategy")
    graph.add_node(node)
    graph.add_node(Node(id="artifact:a", type="artifact", label="Strategy"))
    lines = graph.nodes_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1


def test_changed_node_appends_a_new_revision_and_wins_on_load(graph, tmp_path):
    graph.add_node(Node(id="commitment:D1", type="commitment", label="D1", status="pending"))
    graph.add_node(Node(id="commitment:D1", type="commitment", label="D1", status="resolved"))

    lines = graph.nodes_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2  # history preserved

    reloaded = AnalysisGraph.load(tmp_path)
    assert reloaded.get_node("commitment:D1").status == "resolved"


def test_add_edge_requires_existing_endpoints(graph):
    graph.add_node(Node(id="artifact:a", type="artifact", label="A"))
    with pytest.raises(GraphSchemaError, match="target node"):
        graph.add_edge(Edge(src="artifact:a", dst="artifact:missing", type="derives_from"))
    with pytest.raises(GraphSchemaError, match="source node"):
        graph.add_edge(Edge(src="artifact:missing", dst="artifact:a", type="derives_from"))


def test_add_edge_enforces_the_domain_matrix(graph):
    graph.add_node(Node(id="problem:q", type="problem", label="Q"))
    graph.add_node(Node(id="artifact:a", type="artifact", label="A"))
    with pytest.raises(GraphSchemaError, match="cannot start at a 'problem' node"):
        graph.add_edge(Edge(src="problem:q", dst="artifact:a", type="supports"))


def test_edges_round_trip(graph, tmp_path):
    graph.add_node(Node(id="artifact:a", type="artifact", label="A"))
    graph.add_node(Node(id="artifact:b", type="artifact", label="B"))
    graph.add_edge(Edge(src="artifact:a", dst="artifact:b", type="derives_from"))

    reloaded = AnalysisGraph.load(tmp_path)
    assert len(reloaded.edges()) == 1
    assert reloaded.out_edges("artifact:a")[0].dst == "artifact:b"
    assert reloaded.in_edges("artifact:b")[0].src == "artifact:a"


def test_node_and_edge_filters(graph):
    graph.add_node(Node(id="artifact:a", type="artifact", label="A", phase="1"))
    graph.add_node(Node(id="figure:f", type="figure", label="F", phase="2"))
    graph.add_node(Node(id="commitment:D1", type="commitment", label="D1", status="pending"))
    graph.add_edge(Edge(src="figure:f", dst="artifact:a", type="derives_from"))

    assert [n.id for n in graph.nodes(type="figure")] == ["figure:f"]
    assert [n.id for n in graph.nodes(phase="1")] == ["artifact:a"]
    assert [n.id for n in graph.nodes(status="pending")] == ["commitment:D1"]
    assert len(graph.edges(type="derives_from")) == 1
    assert graph.edges(type="supports") == []


def test_find_by_content_ref_exact_and_suffix(graph):
    graph.add_node(
        Node(
            id="figure:mjj",
            type="figure",
            label="mjj",
            content_ref="phase2_exploration/outputs/figures/mjj.png",
        )
    )
    assert graph.find_by_content_ref("phase2_exploration/outputs/figures/mjj.png")
    assert graph.find_by_content_ref("mjj.png")
    assert graph.find_by_content_ref("no_such_file.png") == []


def test_malformed_lines_are_skipped_on_load(graph, tmp_path):
    graph.add_node(Node(id="artifact:a", type="artifact", label="A"))
    with open(graph.nodes_path, "a", encoding="utf-8") as handle:
        handle.write("this is not json\n")
        handle.write(json.dumps({"id": "x", "type": "not_a_type", "label": "x"}) + "\n")

    reloaded = AnalysisGraph.load(tmp_path)
    assert len(reloaded) == 1


def test_node_history_includes_superseded_revisions(graph):
    graph.add_node(Node(id="commitment:D1", type="commitment", label="D1", status="pending"))
    graph.add_node(Node(id="commitment:D1", type="commitment", label="D1", status="resolved"))
    history = list(graph.node_history())
    assert [r["status"] for r in history] == ["pending", "resolved"]


def test_concurrent_appends_produce_well_formed_lines(graph, tmp_path):
    """Reviewers append concurrently; every line must stay parseable."""

    def add(i: int) -> None:
        graph.add_node(Node(id=f"evidence:e{i}", type="evidence", label=f"E{i}"))

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(add, range(64)))

    lines = graph.nodes_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 64
    for line in lines:
        json.loads(line)  # raises if a write interleaved mid-record

    assert len(AnalysisGraph.load(tmp_path)) == 64


def test_summary_counts_nodes_by_type_and_edges(graph):
    graph.add_node(Node(id="artifact:a", type="artifact", label="A"))
    graph.add_node(Node(id="figure:f", type="figure", label="F"))
    graph.add_edge(Edge(src="figure:f", dst="artifact:a", type="derives_from"))
    summary = graph.summary()
    assert summary["artifact"] == 1
    assert summary["figure"] == 1
    assert summary["_edges"] == 1
