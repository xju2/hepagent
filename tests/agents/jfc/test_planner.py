"""Tests for graph-derived node ordering and resumption."""

import dataclasses

import pytest

from hepagent.agents.jfc.graph_builder import bootstrap_graph, ingest_node
from hepagent.agents.jfc.planner import (
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
from hepagent.plan.schema import PlanEdge
from hepagent.plan.store import save_plan

#: The order the shipped measurement template must produce. Spelled out rather
#: than derived, so a template rewiring that changes the pipeline shows up here.
JFC_ORDER = [
    "strategy",
    "exploration",
    "selection",
    "inference_expected",
    "inference_partial",
    "inference_observed",
    "documentation",
]

STRATEGY_ARTIFACT = "artifact:phase1_strategy/outputs/STRATEGY.md"
SELECTION_ARTIFACT = "artifact:phase3_selection/outputs/SELECTION.md"


@pytest.fixture
def root(tmp_path, jfc_plan):
    analysis = tmp_path / "zbb"
    for node in jfc_plan.nodes:
        (analysis / node.directory / "outputs" / "figures").mkdir(parents=True)
        (analysis / node.directory / "review").mkdir(parents=True)
    (analysis / "prompt.md").write_text(jfc_plan.problem)
    (analysis / "COMMITMENTS.md").write_text("# Analysis Commitments\n")
    save_plan(analysis, jfc_plan)
    bootstrap_graph(analysis, jfc_plan)
    return analysis


@pytest.fixture
def graph(root):
    return AnalysisGraph.load(root)


def produce(root, node_id):
    """Write a node's artifact and ingest it, as a real run would."""
    from hepagent.plan.store import load_plan

    path = root / load_plan(root).require_node(node_id).artifact_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{node_id} output")
    ingest_node(root, node_id)


# ------------------------------------------------------------- dependencies


def test_dependencies_are_derived_from_requires_edges(graph):
    dependencies = phase_dependencies(graph)
    assert dependencies["strategy"] == []
    assert dependencies["exploration"] == ["strategy"]
    assert dependencies["selection"] == ["exploration", "strategy"]
    assert dependencies["inference_expected"] == ["exploration", "selection", "strategy"]
    assert dependencies["documentation"] == ["inference_observed", "selection", "strategy"]


def test_derived_order_reproduces_the_jfc_pipeline(graph, jfc_plan):
    """The shipped template's `requires` edges must yield the seven-phase order."""
    completed: list[str] = []
    derived = []
    while True:
        node_id = next_phase(graph, completed, plan=jfc_plan)
        if node_id is None:
            break
        derived.append(node_id)
        completed.append(node_id)
    assert derived == JFC_ORDER


# ---------------------------------------------------------------- readiness


def test_only_the_entry_node_is_ready_at_the_start(graph, jfc_plan):
    assert ready_phases(graph, [], jfc_plan) == ["strategy"]


def test_completing_a_node_unblocks_the_next(graph, jfc_plan):
    assert ready_phases(graph, ["strategy"], jfc_plan) == ["exploration"]
    assert ready_phases(graph, ["strategy", "exploration"], jfc_plan) == ["selection"]


def test_readiness_reports_what_a_node_is_blocked_on(graph, jfc_plan):
    readiness = {r.phase: r for r in phase_readiness(graph, ["strategy"], jfc_plan)}
    assert readiness["selection"].ready is False
    assert readiness["selection"].blocked_by == ["exploration"]
    assert readiness["strategy"].complete is True


def test_next_phase_returns_none_when_everything_is_complete(graph, jfc_plan):
    assert next_phase(graph, JFC_ORDER, plan=jfc_plan) is None


def test_next_phase_honours_the_skip_set(graph, jfc_plan):
    # exploration is still blocked on strategy, so skipping strategy leaves nothing.
    assert next_phase(graph, [], skip={"strategy"}, plan=jfc_plan) is None


def test_next_phase_breaks_ties_by_plan_declaration_order(graph, jfc_plan):
    """Independent nodes run in the order the plan declares, not dictionary order."""
    graph.add_node(
        Node(
            id="artifact:extra",
            type="artifact",
            label="EXTRA.md",
            status="pending",
            phase="inference_observed",
        )
    )
    graph.add_edge(Edge(src="artifact:extra", dst=STRATEGY_ARTIFACT, type="requires"))
    # With the first three done, both inference_expected and the grafted
    # inference_observed node are satisfied; the plan's order decides.
    done = ["strategy", "exploration", "selection"]
    assert next_phase(graph, done, plan=jfc_plan) == "inference_expected"


def test_next_phase_falls_back_to_the_plan_without_a_graph(tmp_path, jfc_plan):
    empty = AnalysisGraph(tmp_path / "nothing")
    assert next_phase(empty, [], plan=jfc_plan) == "strategy"
    assert next_phase(empty, ["strategy", "exploration"], plan=jfc_plan) == "selection"


# ------------------------------------------------------------------ branching


@pytest.fixture
def fan_root(tmp_path, jfc_plan):
    """An analysis whose selection fans into two channels that merge at inference.

    This is the structure the pre-plan runtime could not express: the point of
    deriving order from edges rather than a list is that both channels come back
    ready at the same time.
    """
    selection = jfc_plan.require_node("selection")
    channels = [
        dataclasses.replace(
            selection, id=f"selection_{ch}", label=f"Selection ({ch})", directory=f"sel_{ch}"
        )
        for ch in ("ee", "mumu")
    ]
    others = [n for n in jfc_plan.nodes if n.id != "selection"]
    at = [n.id for n in jfc_plan.nodes].index("selection")

    edges = []
    for edge in jfc_plan.edges:
        if edge.upstream == "selection":
            edges += [dataclasses.replace(edge, upstream=c.id) for c in channels]
        elif edge.downstream == "selection":
            edges += [dataclasses.replace(edge, downstream=c.id) for c in channels]
        else:
            edges.append(edge)

    plan = dataclasses.replace(
        jfc_plan,
        nodes=tuple(others[:at] + channels + others[at:]),
        edges=tuple(edges),
    )
    analysis = tmp_path / "fan"
    analysis.mkdir()
    save_plan(analysis, plan)
    bootstrap_graph(analysis, plan)
    return analysis, plan


def test_a_fan_out_makes_both_branches_ready_at_once(fan_root):
    analysis, plan = fan_root
    graph = AnalysisGraph.load(analysis)
    ready = ready_phases(graph, ["strategy", "exploration"], plan)
    assert ready == ["selection_ee", "selection_mumu"]


def test_a_merge_node_waits_for_every_branch(fan_root):
    analysis, plan = fan_root
    graph = AnalysisGraph.load(analysis)
    done = ["strategy", "exploration", "selection_ee"]
    readiness = {r.phase: r for r in phase_readiness(graph, done, plan)}
    assert readiness["inference_expected"].ready is False
    assert readiness["inference_expected"].blocked_by == ["selection_mumu"]

    done.append("selection_mumu")
    assert next_phase(graph, done, plan=plan) == "inference_expected"


def test_an_informs_edge_does_not_constrain_the_order(tmp_path, jfc_plan):
    """`informs` reaches the prompt but never the `requires` subgraph."""
    plan = dataclasses.replace(
        jfc_plan,
        edges=jfc_plan.edges
        + (PlanEdge(upstream="documentation", downstream="exploration", kind="informs"),),
    )
    analysis = tmp_path / "informs"
    analysis.mkdir()
    save_plan(analysis, plan)
    bootstrap_graph(analysis, plan)

    graph = AnalysisGraph.load(analysis)
    assert "documentation" not in phase_dependencies(graph)["exploration"]
    assert ready_phases(graph, ["strategy"], plan) == ["exploration"]


# --------------------------------------------------------------- checkpoints


def test_a_produced_and_unchallenged_node_is_a_checkpoint(root):
    produce(root, "strategy")
    assert is_consistent_checkpoint(AnalysisGraph.load(root), "strategy")


def test_a_node_whose_artifact_is_missing_is_not_a_checkpoint(graph):
    assert not is_consistent_checkpoint(graph, "strategy")


def test_an_invalidated_node_is_not_a_checkpoint(root):
    produce(root, "strategy")
    graph = AnalysisGraph.load(root)
    graph.add_node(
        Node(id="decision:adj", type="decision", label="ADJUDICATION.md", phase="strategy")
    )
    graph.add_edge(Edge(src="decision:adj", dst=STRATEGY_ARTIFACT, type="invalidates"))
    assert not is_consistent_checkpoint(AnalysisGraph.load(root), "strategy")


def test_last_consistent_phase_picks_the_latest_intact_one(root, jfc_plan):
    for node_id in ("strategy", "exploration", "selection"):
        produce(root, node_id)
    done = ["strategy", "exploration", "selection"]
    assert last_consistent_phase(AnalysisGraph.load(root), done, jfc_plan) == "selection"


def test_last_consistent_phase_stops_before_an_invalidated_node(root, jfc_plan):
    for node_id in ("strategy", "exploration", "selection"):
        produce(root, node_id)
    graph = AnalysisGraph.load(root)
    graph.add_node(
        Node(id="decision:adj3", type="decision", label="ADJUDICATION.md", phase="selection")
    )
    graph.add_edge(Edge(src="decision:adj3", dst=SELECTION_ARTIFACT, type="invalidates"))
    done = ["strategy", "exploration", "selection"]
    assert last_consistent_phase(AnalysisGraph.load(root), done, jfc_plan) == "exploration"


# ------------------------------------------------------------------- resume


def test_resume_point_is_the_node_after_the_last_intact_one(root):
    produce(root, "strategy")
    produce(root, "exploration")
    assert resume_point(root, ["strategy", "exploration"]) == "selection"


def test_resume_point_rewinds_past_an_invalidated_node(root):
    """A node a later review rejected is not a checkpoint to resume past."""
    for node_id in ("strategy", "exploration", "selection"):
        produce(root, node_id)
    graph = AnalysisGraph.load(root)
    graph.add_node(
        Node(id="decision:adj3", type="decision", label="ADJUDICATION.md", phase="selection")
    )
    graph.add_edge(Edge(src="decision:adj3", dst=SELECTION_ARTIFACT, type="invalidates"))
    # State claims selection finished, but the graph says it was invalidated.
    assert resume_point(root, ["strategy", "exploration", "selection"]) == "selection"


def test_resume_point_of_a_fresh_analysis_is_the_entry_node(root):
    assert resume_point(root, []) == "strategy"


def test_resume_point_survives_a_missing_graph(tmp_path):
    assert resume_point(tmp_path / "nowhere", ["strategy"]) is None
