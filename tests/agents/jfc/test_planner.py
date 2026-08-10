"""Tests for graph-derived phase ordering and resumption."""

import pytest

from hepagent.agents.jfc.graph_builder import bootstrap_graph, ingest_phase
from hepagent.agents.jfc.planner import (
    PHASE_ORDER,
    is_consistent_checkpoint,
    last_consistent_phase,
    next_phase,
    phase_dependencies,
    phase_readiness,
    ready_phases,
    resume_point,
)
from hepagent.graph.schema import Edge, Node
from hepagent.graph.store import AnalysisGraph

PHASE_DIRS = [
    "phase1_strategy",
    "phase2_exploration",
    "phase3_selection",
    "phase4a_inference_expected",
    "phase4b_inference_partial",
    "phase4c_inference_observed",
    "phase5_documentation",
]

ARTIFACTS = {
    "1": "phase1_strategy/outputs/STRATEGY.md",
    "2": "phase2_exploration/outputs/EXPLORATION.md",
    "3": "phase3_selection/outputs/SELECTION.md",
    "4a": "phase4a_inference_expected/outputs/INFERENCE_EXPECTED.md",
    "4b": "phase4b_inference_partial/outputs/INFERENCE_PARTIAL.md",
    "4c": "phase4c_inference_observed/outputs/INFERENCE_OBSERVED.md",
    "5": "phase5_documentation/outputs/ANALYSIS_NOTE_5_v1.md",
}


@pytest.fixture
def root(tmp_path):
    analysis = tmp_path / "zbb"
    for phase in PHASE_DIRS:
        (analysis / phase / "outputs" / "figures").mkdir(parents=True)
        (analysis / phase / "review").mkdir(parents=True)
    (analysis / "prompt.md").write_text("Measure the Z to bb cross-section.")
    (analysis / "COMMITMENTS.md").write_text("# Phase 1 Commitments\n")
    bootstrap_graph(analysis, "zbb", "measurement", "Measure the Z to bb cross-section.")
    return analysis


@pytest.fixture
def graph(root):
    return AnalysisGraph.load(root)


def produce(root, phase):
    """Write a phase's artifact and ingest it, as a real run would."""
    path = root / ARTIFACTS[phase]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"phase {phase} output")
    ingest_phase(root, phase)


# ------------------------------------------------------------- dependencies


def test_dependencies_are_derived_from_requires_edges(graph):
    dependencies = phase_dependencies(graph)
    assert dependencies["1"] == []
    assert dependencies["2"] == ["1"]
    assert dependencies["3"] == ["1", "2"]
    assert dependencies["4a"] == ["1", "2", "3"]
    assert dependencies["5"] == ["1", "3", "4c"]


def test_derived_order_reproduces_the_canonical_phase_order(graph):
    """The graph must produce exactly the sequence PHASE_ORDER hardcoded."""
    completed: list[str] = []
    derived = []
    while True:
        phase = next_phase(graph, completed)
        if phase is None:
            break
        derived.append(phase)
        completed.append(phase)
    assert derived == [str(p) for p in PHASE_ORDER]


# ---------------------------------------------------------------- readiness


def test_only_phase_1_is_ready_at_the_start(graph):
    assert ready_phases(graph, []) == ["1"]


def test_completing_a_phase_unblocks_the_next(graph):
    assert ready_phases(graph, ["1"]) == ["2"]
    assert ready_phases(graph, ["1", "2"]) == ["3"]


def test_readiness_reports_what_a_phase_is_blocked_on(graph):
    readiness = {r.phase: r for r in phase_readiness(graph, ["1"])}
    assert readiness["3"].ready is False
    assert readiness["3"].blocked_by == ["2"]
    assert readiness["1"].complete is True


def test_next_phase_returns_none_when_everything_is_complete(graph):
    assert next_phase(graph, [str(p) for p in PHASE_ORDER]) is None


def test_next_phase_honours_the_skip_set(graph):
    assert next_phase(graph, [], skip={"1"}) is None  # 2 is still blocked on 1


def test_next_phase_breaks_ties_by_canonical_order(root, graph):
    """Independent phases run in the canonical order, not dictionary order."""
    graph.add_node(
        Node(id="artifact:extra", type="artifact", label="EXTRA.md", status="pending", phase="4c")
    )
    graph.add_edge(
        Edge(
            src="artifact:extra",
            dst="artifact:phase1_strategy/outputs/STRATEGY.md",
            type="requires",
        )
    )
    # With 1-3 done, both 4a and 4c are dependency-satisfied; 4a must win.
    assert next_phase(graph, ["1", "2", "3"]) == "4a"


def test_next_phase_falls_back_to_phase_order_without_a_graph(tmp_path):
    empty = AnalysisGraph(tmp_path / "nothing")
    assert next_phase(empty, []) == "1"
    assert next_phase(empty, ["1", "2"]) == "3"


# --------------------------------------------------------------- checkpoints


def test_a_produced_and_unchallenged_phase_is_a_checkpoint(root):
    produce(root, "1")
    assert is_consistent_checkpoint(AnalysisGraph.load(root), "1")


def test_a_phase_whose_artifact_is_missing_is_not_a_checkpoint(graph):
    assert not is_consistent_checkpoint(graph, "1")


def test_an_invalidated_phase_is_not_a_checkpoint(root):
    produce(root, "1")
    graph = AnalysisGraph.load(root)
    graph.add_node(Node(id="decision:adj", type="decision", label="ADJUDICATION.md", phase="1"))
    graph.add_edge(
        Edge(
            src="decision:adj",
            dst="artifact:phase1_strategy/outputs/STRATEGY.md",
            type="invalidates",
        )
    )
    assert not is_consistent_checkpoint(AnalysisGraph.load(root), "1")


def test_last_consistent_phase_picks_the_latest_intact_one(root):
    produce(root, "1")
    produce(root, "2")
    produce(root, "3")
    assert last_consistent_phase(AnalysisGraph.load(root), ["1", "2", "3"]) == "3"


def test_last_consistent_phase_stops_before_an_invalidated_phase(root):
    produce(root, "1")
    produce(root, "2")
    produce(root, "3")
    graph = AnalysisGraph.load(root)
    graph.add_node(Node(id="decision:adj3", type="decision", label="ADJUDICATION.md", phase="3"))
    graph.add_edge(
        Edge(
            src="decision:adj3",
            dst="artifact:phase3_selection/outputs/SELECTION.md",
            type="invalidates",
        )
    )
    assert last_consistent_phase(AnalysisGraph.load(root), ["1", "2", "3"]) == "2"


# ------------------------------------------------------------------- resume


def test_resume_point_is_the_phase_after_the_last_intact_one(root):
    produce(root, "1")
    produce(root, "2")
    assert resume_point(root, ["1", "2"]) == "3"


def test_resume_point_rewinds_past_an_invalidated_phase(root):
    """A phase a later review rejected is not a checkpoint to resume past."""
    produce(root, "1")
    produce(root, "2")
    produce(root, "3")
    graph = AnalysisGraph.load(root)
    graph.add_node(Node(id="decision:adj3", type="decision", label="ADJUDICATION.md", phase="3"))
    graph.add_edge(
        Edge(
            src="decision:adj3",
            dst="artifact:phase3_selection/outputs/SELECTION.md",
            type="invalidates",
        )
    )
    # State claims 3 finished, but the graph says it was invalidated → redo 3.
    assert resume_point(root, ["1", "2", "3"]) == "3"


def test_resume_point_of_a_fresh_analysis_is_phase_1(root):
    assert resume_point(root, []) == "1"


def test_resume_point_survives_a_missing_graph(tmp_path):
    assert resume_point(tmp_path / "nowhere", ["1"]) == "1"
