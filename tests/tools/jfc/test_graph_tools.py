"""Tests for the agent-facing graph write-back tools and their node contract."""

import json

import pytest

from hepagent.agents.jfc.graph_builder import bootstrap_graph
from hepagent.graph.schema import Node
from hepagent.graph.store import AnalysisGraph
from hepagent.plan.schema import PlanNode
from hepagent.plan.store import save_plan
from hepagent.tools.jfc.graph import (
    contract_for,
    contract_summary,
    graph_add_edge,
    graph_add_node,
    graph_query,
)

# The @function_tool decorator wraps the coroutine; call the original directly.
add_node = graph_add_node.on_invoke_tool
add_edge = graph_add_edge.on_invoke_tool
run_query = graph_query.on_invoke_tool


async def call(tool, **kwargs):
    return await tool(None, json.dumps(kwargs))


@pytest.fixture
def analysis_root(tmp_path, jfc_plan):
    root = tmp_path / "zbb"
    (root / "phase1_strategy" / "outputs").mkdir(parents=True)
    (root / "prompt.md").write_text(jfc_plan.problem)
    (root / "COMMITMENTS.md").write_text("# Analysis Commitments\n")
    save_plan(root, jfc_plan)
    bootstrap_graph(root, jfc_plan)
    return root


def _bare_node():
    """A node that declares no write-back contract at all."""
    return PlanNode(id="bare", label="Bare", directory="bare", artifact="BARE.md")


# ------------------------------------------------------------------ contract


def test_every_pipeline_node_declares_a_contract(jfc_plan):
    """The shipped template must not leave a node unable to write anything back."""
    for node in jfc_plan.nodes:
        assert contract_for(node)[0], f"{node.id} declares no node types"


def test_contract_for_a_node_without_one_is_empty():
    assert contract_for(_bare_node()) == (frozenset(), frozenset())


def test_contract_summary_lists_allowed_types(jfc_plan):
    summary = contract_summary(jfc_plan.require_node("strategy"))
    assert "commitment" in summary
    assert "commits_to" in summary


def test_contract_summary_without_an_allowance_says_so():
    assert "no graph write-back allowance" in contract_summary(_bare_node())


# ------------------------------------------------------------ graph_add_node


@pytest.mark.asyncio
async def test_add_node_within_contract_succeeds(analysis_root):
    result = await call(
        add_node,
        analysis_root=str(analysis_root),
        node_id="strategy",
        node_type="commitment",
        label="D1 unfold with IBU",
    )
    assert result == "commitment:D1-unfold-with-IBU"
    assert AnalysisGraph.load(analysis_root).get_node(result) is not None


@pytest.mark.asyncio
async def test_add_node_outside_contract_is_refused(analysis_root):
    result = await call(
        add_node,
        analysis_root=str(analysis_root),
        node_id="strategy",
        node_type="figure",
        label="mjj.png",
    )
    assert result.startswith("Error:")
    assert "may not create node type 'figure'" in result
    assert AnalysisGraph.load(analysis_root).nodes(type="figure") == []


@pytest.mark.asyncio
async def test_add_node_stores_atlas_metadata(analysis_root):
    metadata = {"ami_tag": "e8514_s4162_r14622", "campaign": "mc23a", "lumi_fb": 140.1}
    node_id = await call(
        add_node,
        analysis_root=str(analysis_root),
        node_id="exploration",
        node_type="dataset",
        label="mc23a Zbb PowhegPythia8",
        metadata_json=json.dumps(metadata),
    )
    node = AnalysisGraph.load(analysis_root).get_node(node_id)
    assert node.metadata == metadata
    assert node.phase == "exploration"
    assert node.created_by == "exploration_executor"


@pytest.mark.asyncio
async def test_add_node_rejects_malformed_metadata(analysis_root):
    result = await call(
        add_node,
        analysis_root=str(analysis_root),
        node_id="exploration",
        node_type="dataset",
        label="x",
        metadata_json="{not json",
    )
    assert result.startswith("Error: metadata_json is not valid JSON")


@pytest.mark.asyncio
async def test_add_node_rejects_non_object_metadata(analysis_root):
    result = await call(
        add_node,
        analysis_root=str(analysis_root),
        node_id="exploration",
        node_type="dataset",
        label="x",
        metadata_json="[1, 2]",
    )
    assert result == "Error: metadata_json must be a JSON object."


@pytest.mark.asyncio
async def test_add_node_reports_an_unknown_node_id(analysis_root):
    result = await call(
        add_node,
        analysis_root=str(analysis_root),
        node_id="phase1",
        node_type="commitment",
        label="D1",
    )
    assert result.startswith("Error: unknown node 'phase1'")
    assert "strategy" in result  # the message names what is valid


@pytest.mark.asyncio
async def test_add_node_reports_a_missing_analysis_root(tmp_path):
    result = await call(
        add_node,
        analysis_root=str(tmp_path / "nope"),
        node_id="strategy",
        node_type="commitment",
        label="D1",
    )
    assert result.startswith("Error: analysis root not found")


# ------------------------------------------------------------ graph_add_edge


@pytest.mark.asyncio
async def test_add_edge_within_contract_succeeds(analysis_root):
    commitment = await call(
        add_node,
        analysis_root=str(analysis_root),
        node_id="selection",
        node_type="method",
        label="IBU unfolding",
    )
    evidence = await call(
        add_node,
        analysis_root=str(analysis_root),
        node_id="selection",
        node_type="evidence",
        label="closure chi2/ndf = 1.3/36",
    )
    result = await call(
        add_edge,
        analysis_root=str(analysis_root),
        node_id="selection",
        src_id=evidence,
        edge_type="supports",
        dst_id=commitment,
    )
    assert result.startswith("Linked")
    assert AnalysisGraph.load(analysis_root).edges(type="supports")


@pytest.mark.asyncio
async def test_add_edge_outside_contract_is_refused(analysis_root):
    result = await call(
        add_edge,
        analysis_root=str(analysis_root),
        node_id="exploration",
        src_id="a",
        edge_type="invalidates",
        dst_id="b",
    )
    assert result.startswith("Error:")
    assert "may not create edge type 'invalidates'" in result


@pytest.mark.asyncio
async def test_downscope_edge_requires_a_documented_reason(analysis_root):
    result = await call(
        add_edge,
        analysis_root=str(analysis_root),
        node_id="inference_expected",
        src_id="commitment:D1",
        edge_type="downscopes",
        dst_id="evidence:x",
    )
    assert result.startswith("Error: a downscopes edge requires evidence_ref")


@pytest.mark.asyncio
async def test_add_edge_with_a_missing_endpoint_is_refused(analysis_root):
    result = await call(
        add_edge,
        analysis_root=str(analysis_root),
        node_id="exploration",
        src_id="dataset:ghost",
        edge_type="supports",
        dst_id="artifact:phase2_exploration/outputs/EXPLORATION.md",
    )
    assert result.startswith("Error:")
    assert "does not exist in the graph" in result


@pytest.mark.asyncio
async def test_add_edge_enforces_the_type_domain(analysis_root):
    dataset = await call(
        add_node,
        analysis_root=str(analysis_root),
        node_id="exploration",
        node_type="dataset",
        label="mc23a Zbb",
    )
    result = await call(
        add_edge,
        analysis_root=str(analysis_root),
        node_id="exploration",
        src_id=dataset,
        edge_type="supports",
        dst_id="artifact:phase2_exploration/outputs/EXPLORATION.md",
    )
    # `supports` may not start at a dataset node
    assert result.startswith("Error:")
    assert "cannot start at a 'dataset' node" in result


# --------------------------------------------------------------- graph_query


@pytest.mark.asyncio
async def test_query_summary_counts_nodes(analysis_root):
    result = await call(run_query, analysis_root=str(analysis_root), question="summary")
    assert "artifact" in result
    assert "edges" in result


@pytest.mark.asyncio
async def test_query_nodes_filters_by_type(analysis_root, jfc_plan):
    result = await call(
        run_query, analysis_root=str(analysis_root), question="nodes", target="problem"
    )
    assert jfc_plan.problem.splitlines()[0][:38] in result
    assert "STRATEGY.md" not in result


@pytest.mark.asyncio
async def test_query_commitments_reports_open_items(analysis_root):
    graph = AnalysisGraph.load(analysis_root)
    graph.add_node(Node(id="commitment:D7", type="commitment", label="D7 open item"))

    result = await call(run_query, analysis_root=str(analysis_root), question="commitments")
    assert "commitment:D7" in result


@pytest.mark.asyncio
async def test_query_commitments_when_all_closed(analysis_root):
    result = await call(run_query, analysis_root=str(analysis_root), question="commitments")
    assert "Every commitment has closing evidence." in result


@pytest.mark.asyncio
async def test_query_provenance_describes_a_node(analysis_root):
    result = await call(
        run_query,
        analysis_root=str(analysis_root),
        question="provenance",
        target="phase3_selection/outputs/SELECTION.md",
    )
    assert "artifact:phase3_selection/outputs/SELECTION.md" in result
    assert "no lineage recorded" in result


@pytest.mark.asyncio
async def test_query_provenance_requires_a_target(analysis_root):
    result = await call(run_query, analysis_root=str(analysis_root), question="provenance")
    assert result.startswith("Error: provenance requires a target")


@pytest.mark.asyncio
async def test_query_provenance_for_an_unknown_node(analysis_root):
    result = await call(
        run_query, analysis_root=str(analysis_root), question="provenance", target="ghost.png"
    )
    assert "No node found" in result


@pytest.mark.asyncio
async def test_query_rejects_an_unknown_question(analysis_root):
    result = await call(run_query, analysis_root=str(analysis_root), question="why")
    assert result.startswith("Error: unknown question")


@pytest.mark.asyncio
async def test_query_on_an_empty_graph(tmp_path):
    bare = tmp_path / "bare"
    bare.mkdir()
    result = await call(run_query, analysis_root=str(bare), question="summary")
    assert result == "The analysis graph is empty."


# ----------------------------------------------------------- tool registration


def test_graph_tools_are_registered_for_agents():
    from hepagent.tools.jfc import get_jfc_tools

    names = {tool.name for tool in get_jfc_tools()}
    assert {"graph_add_node", "graph_add_edge", "graph_query"} <= names
