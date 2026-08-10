"""Tests for scaffold_jfc_analysis tool."""

from pathlib import Path

import pytest


@pytest.fixture
def tmp_base(tmp_path):
    return str(tmp_path / "analyses")


@pytest.mark.asyncio
async def test_scaffold_creates_directory_tree(tmp_base, tmp_path):
    from hepagent.tools.jfc.scaffold import _scaffold_impl as scaffold_jfc_analysis

    result = await scaffold_jfc_analysis(
        "test_z_boson", "Measure Z→bb cross-section", "measurement", tmp_base
    )
    root = Path(result)

    assert root.exists()
    assert (root / "prompt.md").exists()
    assert (root / "experiment_log.md").exists()
    assert (root / "retrieval_log.md").exists()
    assert (root / "COMMITMENTS.md").exists()


@pytest.mark.asyncio
async def test_scaffold_prompt_content(tmp_base):
    from hepagent.tools.jfc.scaffold import _scaffold_impl as scaffold_jfc_analysis

    result = await scaffold_jfc_analysis("test_prompt", "Search for Z' → bb", "search", tmp_base)
    root = Path(result)
    content = (root / "prompt.md").read_text()
    assert "Search for Z' → bb" in content


@pytest.mark.asyncio
async def test_scaffold_experiment_log_header(tmp_base):
    from hepagent.tools.jfc.scaffold import _scaffold_impl as scaffold_jfc_analysis

    result = await scaffold_jfc_analysis("test_log", "Test analysis", "measurement", tmp_base)
    root = Path(result)
    log_content = (root / "experiment_log.md").read_text()
    assert "Experiment Log" in log_content
    assert "Analysis type: measurement" in log_content


@pytest.mark.asyncio
async def test_scaffold_creates_a_tree_per_plan_node(tmp_base):
    """The directory layout follows the plan — nothing here knows the node count."""
    from hepagent.plan.store import load_plan
    from hepagent.tools.jfc.scaffold import (
        NODE_SUBDIRS,
        _scaffold_impl as scaffold_jfc_analysis,
    )

    result = await scaffold_jfc_analysis("test_phases", "Test", "measurement", tmp_base)
    root = Path(result)

    for node in load_plan(root).nodes:
        for sub in NODE_SUBDIRS:
            d = root / node.directory / sub
            assert d.exists(), f"Missing directory: {d}"


@pytest.mark.asyncio
async def test_scaffold_writes_the_plan_before_anything_derived_from_it(tmp_base):
    from hepagent.plan.store import load_plan
    from hepagent.tools.jfc.scaffold import _scaffold_impl as scaffold_jfc_analysis

    result = await scaffold_jfc_analysis("plan_written", "Test", "measurement", tmp_base)
    root = Path(result)

    assert (root / "plan.json").exists()
    plan = load_plan(root)
    assert plan.name == "plan_written"
    assert plan.template == "jfc-measurement"


@pytest.mark.asyncio
async def test_each_node_gets_its_prompt_as_that_directorys_claude_md(tmp_base):
    """The editable prompt is what the executor working there actually reads."""
    from hepagent.plan.store import load_plan
    from hepagent.tools.jfc.scaffold import _scaffold_impl as scaffold_jfc_analysis

    result = await scaffold_jfc_analysis("prompts", "Test", "measurement", tmp_base)
    root = Path(result)

    for node in load_plan(root).nodes:
        claude_md = root / node.directory / "CLAUDE.md"
        assert claude_md.exists(), node.id
        assert claude_md.read_text(encoding="utf-8") == node.prompt


@pytest.mark.asyncio
async def test_scaffold_honours_an_explicit_plan(tmp_base):
    """`--plan` and the plan editor hand the scaffold a finished document."""
    import dataclasses

    from hepagent.plan.store import load_plan
    from hepagent.plan.templates import instantiate
    from hepagent.tools.jfc.scaffold import _scaffold_impl as scaffold_jfc_analysis

    base = instantiate(
        "jfc-measurement",
        analysis_name="authored",
        analysis_type="measurement",
        physics_prompt="Test",
    )
    trimmed = dataclasses.replace(
        base,
        nodes=tuple(n for n in base.nodes if n.id in {"strategy", "exploration"}),
        edges=tuple(
            e for e in base.edges if {e.upstream, e.downstream} <= {"strategy", "exploration"}
        ),
    )
    result = await scaffold_jfc_analysis("authored", "Test", "measurement", tmp_base, plan=trimmed)
    root = Path(result)

    assert list(load_plan(root).node_ids()) == ["strategy", "exploration"]
    assert not (root / "phase3_selection").exists()


@pytest.mark.parametrize("escape", ["../OTHER_PROJECT", "/tmp/absolute_escape", "a/../../sideways"])
@pytest.mark.asyncio
async def test_scaffold_refuses_a_node_directory_outside_the_root(tmp_base, escape):
    """A plan is executable data: `directory` becomes a real mkdir and file write.

    P2 rejects this at validation time, but the scaffolder must not depend on
    having been validated — reaching it means something skipped the check.
    """
    import dataclasses

    from hepagent.plan.templates import instantiate
    from hepagent.tools.jfc.scaffold import _write_node_tree

    plan = instantiate("jfc-measurement", analysis_name="x", analysis_type="measurement")
    evil = dataclasses.replace(plan.nodes[0], directory=escape, prompt="should never be written")
    plan = dataclasses.replace(plan, nodes=(evil,) + plan.nodes[1:])

    root = Path(tmp_base) / "x"
    root.mkdir(parents=True)
    with pytest.raises(ValueError, match="outside the analysis root"):
        _write_node_tree(root, plan)

    assert not (root.parent / "OTHER_PROJECT").exists()
    assert not Path("/tmp/absolute_escape").exists()


@pytest.mark.asyncio
async def test_scaffold_reports_an_unknown_template(tmp_base):
    from hepagent.tools.jfc.scaffold import _scaffold_impl as scaffold_jfc_analysis

    result = await scaffold_jfc_analysis(
        "bad_template", "Test", "measurement", tmp_base, template="no-such-template"
    )
    assert result.startswith("Error:")
    assert "no-such-template" in result


@pytest.mark.asyncio
async def test_scaffold_refuses_duplicate(tmp_base):
    from hepagent.tools.jfc.scaffold import _scaffold_impl as scaffold_jfc_analysis

    await scaffold_jfc_analysis("dup_test", "Test", "measurement", tmp_base)
    result2 = await scaffold_jfc_analysis("dup_test", "Test2", "search", tmp_base)
    assert result2.startswith("Error:")
    assert "already exists" in result2


@pytest.mark.asyncio
async def test_scaffold_importable():
    from hepagent.tools.jfc.scaffold import _scaffold_impl as scaffold_jfc_analysis

    assert callable(scaffold_jfc_analysis)


@pytest.mark.asyncio
async def test_scaffold_seeds_the_analysis_graph(tmp_base):
    from hepagent.graph.store import AnalysisGraph
    from hepagent.tools.jfc.scaffold import _scaffold_impl as scaffold_jfc_analysis

    result = await scaffold_jfc_analysis(
        "graph_seed", "Measure the Z→bb cross-section", "measurement", tmp_base
    )
    root = Path(result)

    assert (root / "graph" / "nodes.jsonl").exists()
    assert (root / "graph" / "README.md").exists()

    graph = AnalysisGraph.load(root)
    problem = graph.nodes(type="problem")
    assert len(problem) == 1
    assert problem[0].metadata["analysis_type"] == "measurement"
    assert graph.nodes(type="analysis_root")

    # One pending artifact node per plan node, chained by `requires`.
    artifacts = graph.nodes(type="artifact")
    assert len(artifacts) == 7
    assert all(a.status == "pending" for a in artifacts)

    selection = graph.get_node("artifact:phase3_selection/outputs/SELECTION.md")
    prerequisites = {e.dst for e in graph.out_edges(selection.id, type="requires")}
    assert "artifact:phase1_strategy/outputs/STRATEGY.md" in prerequisites
    assert "artifact:phase2_exploration/outputs/EXPLORATION.md" in prerequisites


@pytest.mark.asyncio
async def test_scaffolded_graph_validates_clean(tmp_base):
    """A fresh scaffold must not trip any rule — pending nodes are plans."""
    from hepagent.graph.store import AnalysisGraph
    from hepagent.graph.validation import validate
    from hepagent.tools.jfc.scaffold import _scaffold_impl as scaffold_jfc_analysis

    result = await scaffold_jfc_analysis("graph_clean", "Test", "search", tmp_base)
    report = validate(AnalysisGraph.load(Path(result)))
    assert report.ok, report.to_markdown()


@pytest.mark.asyncio
async def test_scaffold_commits_the_graph(tmp_base):
    """`graph/` must land in the scaffold commit, not as an untracked leftover."""
    import subprocess

    from hepagent.tools.jfc.scaffold import _scaffold_impl as scaffold_jfc_analysis

    result = await scaffold_jfc_analysis("graph_commit", "Test", "measurement", tmp_base)
    tracked = subprocess.run(
        ["git", "ls-files", "graph/"],
        cwd=result,
        capture_output=True,
        text=True,
    )
    if tracked.returncode != 0:
        pytest.skip("git is unavailable in this environment")
    assert "graph/nodes.jsonl" in tracked.stdout
