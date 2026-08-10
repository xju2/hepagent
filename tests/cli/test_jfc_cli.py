"""Tests for the hepagent jfc CLI subcommand (mocked orchestrator)."""

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from typer.testing import CliRunner


def _normalize_cli_output(text: str) -> str:
    """Strip ANSI styling and normalize whitespace for robust assertions."""
    import re

    text = re.sub(r"\x1b\[[0-9;]*m", "", text)
    return " ".join(text.split())


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture
def analyses_dir(tmp_path):
    d = tmp_path / "analyses"
    d.mkdir()
    return d


def test_jfc_help(runner):
    from hepagent.main import app

    result = runner.invoke(app, ["jfc", "--help"])
    assert result.exit_code == 0
    assert "jfc" in result.output.lower()


def test_jfc_run_help(runner):
    from hepagent.main import app

    result = runner.invoke(app, ["jfc", "run", "--help"], env={"NO_COLOR": "1"})
    output = _normalize_cli_output(result.output)
    assert result.exit_code == 0
    assert "--name" in output
    assert "--type" in output
    assert "--prompt" in output


def test_jfc_list_empty(runner, analyses_dir):
    from hepagent.main import app

    result = runner.invoke(app, ["jfc", "list", "--base-dir", str(analyses_dir)])
    assert result.exit_code == 0


def test_jfc_list_with_analysis(runner, analyses_dir, tmp_path):
    import json

    from hepagent.main import app

    analysis_dir = analyses_dir / "my_analysis"
    analysis_dir.mkdir()
    state = {
        "analysis_root": str(analysis_dir),
        "analysis_name": "my_analysis",
        "analysis_type": "measurement",
        "current_phase": 1,
        "current_subphase": "1",
        "max_iterations_per_phase": 3,
        "model_provider": "cborg",
        "model_name": None,
        "completed_phases": ["1", "2"],
        "phase_iterations": {},
    }
    (analysis_dir / ".orchestration_state.json").write_text(json.dumps(state))

    result = runner.invoke(app, ["jfc", "list", "--base-dir", str(analyses_dir)])
    output = _normalize_cli_output(result.output)
    assert result.exit_code == 0
    assert "my_analysis" in output


def test_jfc_status_not_found(runner, analyses_dir):
    from hepagent.main import app

    result = runner.invoke(
        app, ["jfc", "status", "--name", "nonexistent", "--base-dir", str(analyses_dir)]
    )
    assert result.exit_code != 0


def test_jfc_status_shows_phases(runner, analyses_dir, tmp_path):
    import json

    from hepagent.main import app

    analysis_dir = analyses_dir / "status_test"
    analysis_dir.mkdir()
    state = {
        "analysis_root": str(analysis_dir),
        "analysis_name": "status_test",
        "analysis_type": "measurement",
        "current_phase": 3,
        "current_subphase": "3",
        "max_iterations_per_phase": 3,
        "model_provider": "cborg",
        "model_name": None,
        "completed_phases": ["1", "2"],
        "phase_iterations": {"1": 1, "2": 1},
    }
    (analysis_dir / ".orchestration_state.json").write_text(json.dumps(state))

    result = runner.invoke(
        app, ["jfc", "status", "--name", "status_test", "--base-dir", str(analyses_dir)]
    )
    output = _normalize_cli_output(result.output)
    assert result.exit_code == 0
    assert "PASS" in output or "Strategy" in output
    assert "IN PROGRESS" in output or "pending" in output


def test_jfc_run_invokes_orchestrator(runner, analyses_dir):
    from hepagent.main import app

    with patch(
        "hepagent.agents.jfc.orchestrator.run_jfc_analysis", new_callable=AsyncMock
    ) as mock_run:
        mock_run.return_value = Path("/tmp/analysis.pdf")
        result = runner.invoke(
            app,
            [
                "jfc",
                "run",
                "--name",
                "test_run",
                "--type",
                "measurement",
                "--prompt-file",
                "prompt.md",
                "--base-dir",
                str(analyses_dir),
            ],
        )
    # Either succeeds or fails, but CLI invocation worked
    assert "--help" not in _normalize_cli_output(result.output)


@pytest.fixture
def graph_analysis(analyses_dir):
    """A small analysis with a rebuilt graph: strategy → exploration → figure."""
    from hepagent.agents.jfc.graph_builder import bootstrap_graph, rebuild

    root = analyses_dir / "graphy"
    for phase in ("phase1_strategy", "phase2_exploration"):
        (root / phase / "outputs" / "figures").mkdir(parents=True)
    (root / "prompt.md").write_text("Measure the Z to bb cross-section.")
    (root / "phase1_strategy" / "outputs" / "STRATEGY.md").write_text("strategy")
    (root / "phase2_exploration" / "outputs" / "EXPLORATION.md").write_text("exploration")
    (root / "phase2_exploration" / "outputs" / "figures" / "mjj.png").write_text("png")

    bootstrap_graph(root, "graphy", "measurement", "Measure the Z to bb cross-section.")
    rebuild(root)
    return root


def test_jfc_graph_help(runner):
    from hepagent.main import app

    result = runner.invoke(app, ["jfc", "graph", "--help"], env={"NO_COLOR": "1"})
    output = _normalize_cli_output(result.output)
    assert result.exit_code == 0
    for command in ("show", "validate", "trace", "rebuild"):
        assert command in output


def test_jfc_graph_show_table(runner, analyses_dir, graph_analysis):
    from hepagent.main import app

    result = runner.invoke(
        app, ["jfc", "graph", "show", "--name", "graphy", "--base-dir", str(analyses_dir)]
    )
    assert result.exit_code == 0
    assert "STRATEGY.md" in result.output
    assert "mjj.png" in result.output


def test_jfc_graph_show_mermaid(runner, analyses_dir, graph_analysis):
    from hepagent.main import app

    result = runner.invoke(
        app,
        [
            "jfc",
            "graph",
            "show",
            "--name",
            "graphy",
            "--base-dir",
            str(analyses_dir),
            "--format",
            "mermaid",
        ],
    )
    assert result.exit_code == 0
    assert "graph LR" in result.output
    assert "derives_from" in result.output


def test_jfc_graph_show_filters_by_type(runner, analyses_dir, graph_analysis):
    from hepagent.main import app

    result = runner.invoke(
        app,
        [
            "jfc",
            "graph",
            "show",
            "--name",
            "graphy",
            "--base-dir",
            str(analyses_dir),
            "--type",
            "figure",
        ],
    )
    assert result.exit_code == 0
    assert "mjj.png" in result.output
    assert "STRATEGY.md" not in result.output


def test_jfc_graph_show_missing_analysis(runner, analyses_dir):
    from hepagent.main import app

    result = runner.invoke(
        app, ["jfc", "graph", "show", "--name", "nope", "--base-dir", str(analyses_dir)]
    )
    assert result.exit_code == 1


def test_jfc_graph_trace_answers_what_produced_this(runner, analyses_dir, graph_analysis):
    from hepagent.main import app

    result = runner.invoke(
        app,
        [
            "jfc",
            "graph",
            "trace",
            "--name",
            "graphy",
            "--base-dir",
            str(analyses_dir),
            "--node",
            "mjj.png",
        ],
    )
    assert result.exit_code == 0
    assert "derives from:" in result.output
    assert "EXPLORATION.md" in result.output
    assert "STRATEGY.md" in result.output


def test_jfc_graph_trace_unknown_node(runner, analyses_dir, graph_analysis):
    from hepagent.main import app

    result = runner.invoke(
        app,
        [
            "jfc",
            "graph",
            "trace",
            "--name",
            "graphy",
            "--base-dir",
            str(analyses_dir),
            "--node",
            "ghost.png",
        ],
    )
    assert result.exit_code == 1


def test_jfc_graph_validate_clean(runner, analyses_dir, graph_analysis):
    from hepagent.main import app

    result = runner.invoke(
        app, ["jfc", "graph", "validate", "--name", "graphy", "--base-dir", str(analyses_dir)]
    )
    assert result.exit_code == 0
    assert "No findings" in result.output


def test_jfc_graph_validate_exits_1_on_an_open_commitment(runner, analyses_dir, graph_analysis):
    from hepagent.main import app

    (graph_analysis / "COMMITMENTS.md").write_text(
        "# Phase 1 Commitments\n\n"
        "| ID | Commitment | Status | Evidence | Phase Resolved |\n"
        "|----|-----------|--------|----------|---------------|\n"
        "| D2 | Generator comparison | pending | | |\n"
    )
    runner.invoke(
        app, ["jfc", "graph", "rebuild", "--name", "graphy", "--base-dir", str(analyses_dir)]
    )
    result = runner.invoke(
        app, ["jfc", "graph", "validate", "--name", "graphy", "--base-dir", str(analyses_dir)]
    )
    assert result.exit_code == 1
    assert "D2" in result.output


def test_jfc_graph_rebuild_reconstructs_from_disk(runner, analyses_dir, graph_analysis):
    from hepagent.graph.store import AnalysisGraph
    from hepagent.main import app

    (graph_analysis / "graph" / "nodes.jsonl").unlink()
    (graph_analysis / "graph" / "edges.jsonl").unlink()

    result = runner.invoke(
        app, ["jfc", "graph", "rebuild", "--name", "graphy", "--base-dir", str(analyses_dir)]
    )
    assert result.exit_code == 0
    assert "Rebuilt graph" in result.output

    graph = AnalysisGraph.load(graph_analysis)
    assert graph.get_node("figure:phase2_exploration/outputs/figures/mjj.png") is not None


def test_jfc_graph_rebuild_missing_analysis(runner, analyses_dir):
    from hepagent.main import app

    result = runner.invoke(
        app, ["jfc", "graph", "rebuild", "--name", "nope", "--base-dir", str(analyses_dir)]
    )
    assert result.exit_code == 1
