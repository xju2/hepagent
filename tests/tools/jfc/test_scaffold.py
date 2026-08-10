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
async def test_scaffold_phase_subdirs(tmp_base):
    from hepagent.tools.jfc.scaffold import (
        PHASE_DIRS,
        PHASE_SUBDIRS,
        _scaffold_impl as scaffold_jfc_analysis,
    )

    result = await scaffold_jfc_analysis("test_phases", "Test", "measurement", tmp_base)
    root = Path(result)

    for phase in PHASE_DIRS:
        for sub in PHASE_SUBDIRS:
            d = root / phase / sub
            assert d.exists(), f"Missing directory: {d}"


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

    # One pending artifact node per phase, chained by `requires`.
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
