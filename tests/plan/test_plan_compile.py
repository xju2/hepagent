"""Compiling a plan into the seed of the provenance graph."""

from __future__ import annotations

from plan_factory import make_plan

from hepagent.agents.jfc.graph_builder import bootstrap_graph
from hepagent.plan.compile import artifact_id, execution_order, plan_to_graph, problem_id
from hepagent.plan.schema import PlanEdge
from hepagent.plan.templates import instantiate

PROMPT = "# Physics Prompt\n\nMeasure the Z->bb cross section.\n"


def requires(edges):
    """The `requires` edges as (src, dst) pairs."""
    return {(e.src, e.dst) for e in edges if e.type == "requires"}


# ------------------------------------------------------------- basic shape


def test_compile_emits_problem_root_and_one_artifact_per_node(plan):
    nodes, _ = plan_to_graph(plan)
    by_type: dict[str, list[str]] = {}
    for node in nodes:
        by_type.setdefault(node.type, []).append(node.id)
    assert len(by_type["problem"]) == 1
    assert len(by_type["analysis_root"]) == 1
    assert len(by_type["artifact"]) == len(plan.nodes)


def test_artifact_nodes_are_pending_and_carry_the_node_id_as_phase(plan):
    nodes, _ = plan_to_graph(plan)
    artifacts = {n.phase: n for n in nodes if n.type == "artifact"}
    assert set(artifacts) == {"a", "b"}
    assert all(n.status == "pending" for n in artifacts.values())
    assert artifacts["a"].content_ref == "a_dir/outputs/A.md"


def test_compile_is_pure_and_touches_no_disk(plan, tmp_path):
    plan_to_graph(plan)
    assert list(tmp_path.iterdir()) == []


def test_nodes_are_emitted_before_any_edge_that_uses_them(fan_plan):
    nodes, edges = plan_to_graph(fan_plan)
    defined = {n.id for n in nodes}
    for edge in edges:
        assert edge.src in defined and edge.dst in defined


# --------------------------------------------------------- edge inversion


def test_a_plan_edge_compiles_to_an_inverted_requires_edge(plan):
    """`upstream a -> downstream b` means b requires a, so b is the graph edge's src."""
    _, edges = plan_to_graph(plan)
    a, b = artifact_id(plan.require_node("a")), artifact_id(plan.require_node("b"))
    assert (b, a) in requires(edges)
    assert (a, b) not in requires(edges)


def test_entry_nodes_require_the_problem_node(fan_plan):
    _, edges = plan_to_graph(fan_plan)
    root = artifact_id(fan_plan.require_node("root"))
    assert (root, problem_id(fan_plan)) in requires(edges)


def test_informs_edges_do_not_order_execution():
    plan = make_plan(
        node_ids=("a", "b"),
        edges=(PlanEdge(upstream="a", downstream="b", kind="informs"),),
    )
    _, edges = plan_to_graph(plan)
    a, b = artifact_id(plan.require_node("a")), artifact_id(plan.require_node("b"))
    assert (b, a) not in requires(edges)
    # With no blocking prerequisite, b hangs off the problem node instead.
    assert (b, problem_id(plan)) in requires(edges)


def test_edges_to_nodes_the_plan_does_not_declare_are_dropped():
    """A dangling edge is reported by validate_plan; compiling must not crash."""
    plan = make_plan(node_ids=("a",), edges=(("ghost", "a"),))
    _, edges = plan_to_graph(plan)
    assert (artifact_id(plan.require_node("a")), problem_id(plan)) in requires(edges)


# ------------------------------------------------------------ ordering


def test_execution_order_is_topological(fan_plan):
    order = execution_order(fan_plan)
    assert order.index("root") < order.index("left") < order.index("merge")
    assert order.index("root") < order.index("right") < order.index("merge")


def test_execution_order_breaks_ties_by_declaration_order(fan_plan):
    assert execution_order(fan_plan) == ["root", "left", "right", "merge"]


def test_execution_order_returns_every_node_even_with_a_cycle():
    plan = make_plan(node_ids=("a", "b"), edges=(("a", "b"), ("b", "a")))
    assert sorted(execution_order(plan)) == ["a", "b"]


# ------------------------------------------------ the JFC template pinning


def test_default_template_reproduces_the_canonical_order():
    plan = instantiate(
        "jfc-measurement", analysis_name="zbb", analysis_type="measurement", physics_prompt=PROMPT
    )
    assert execution_order(plan) == [
        "strategy",
        "exploration",
        "selection",
        "inference_expected",
        "inference_partial",
        "inference_observed",
        "documentation",
    ]


def test_default_template_compiles_to_the_pipeline_topology(tmp_path):
    """The compiled `requires` subgraph is what the planner walks, so it is
    pinned here in full. Node ids are content-addressed on artifact paths, which
    is what lets an existing analysis survive a plan edit that only relabels."""
    plan = instantiate(
        "jfc-measurement", analysis_name="zbb", analysis_type="measurement", physics_prompt=PROMPT
    )
    nodes, edges = plan_to_graph(plan)

    assert {n.id for n in nodes if n.type == "artifact"} == {
        "artifact:phase1_strategy/outputs/STRATEGY.md",
        "artifact:phase2_exploration/outputs/EXPLORATION.md",
        "artifact:phase3_selection/outputs/SELECTION.md",
        "artifact:phase4a_inference_expected/outputs/INFERENCE_EXPECTED.md",
        "artifact:phase4b_inference_partial/outputs/INFERENCE_PARTIAL.md",
        "artifact:phase4c_inference_observed/outputs/INFERENCE_OBSERVED.md",
        "artifact:phase5_documentation/outputs/ANALYSIS_NOTE_5_v1.md",
    }
    assert all(n.status == "pending" for n in nodes if n.type == "artifact")

    # The entry node depends on the problem statement, nothing else.
    strategy = "artifact:phase1_strategy/outputs/STRATEGY.md"
    assert {dst for src, dst in requires(edges) if src == strategy} == {"problem:zbb"}


def test_bootstrap_writes_exactly_what_the_compiler_produced(tmp_path):
    """`bootstrap_graph` must be the compiler plus disk I/O — nothing more."""
    (tmp_path / "prompt.md").write_text(PROMPT, encoding="utf-8")
    plan = instantiate(
        "jfc-measurement", analysis_name="zbb", analysis_type="measurement", physics_prompt=PROMPT
    )
    nodes, edges = plan_to_graph(plan)
    graph = bootstrap_graph(tmp_path, plan)

    def payload(node):
        return (node.type, node.status, node.content_ref, node.label)

    assert {n.id: payload(n) for n in graph.nodes()} == {n.id: payload(n) for n in nodes}
    assert requires(graph.edges(type="requires")) == requires(edges)


def test_search_template_has_the_same_topology_as_the_measurement_one():
    kwargs = {"analysis_name": "x", "physics_prompt": PROMPT}
    measurement = instantiate("jfc-measurement", analysis_type="measurement", **kwargs)
    search = instantiate("jfc-search", analysis_type="search", **kwargs)
    assert execution_order(measurement) == execution_order(search)
    assert requires(plan_to_graph(measurement)[1]) == requires(plan_to_graph(search)[1])
