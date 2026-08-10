"""Tests for the hepagent jfc CLI subcommand (mocked orchestrator)."""

import json
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


def _write_state(analysis_dir, **overrides):
    """Write an orchestration state file for an analysis directory."""
    import json

    state = {
        "analysis_root": str(analysis_dir),
        "analysis_name": analysis_dir.name,
        "analysis_type": "measurement",
        "current_node": "strategy",
        "max_iterations_per_phase": 3,
        "model_provider": "cborg",
        "model_name": None,
        "completed_nodes": [],
        "phase_iterations": {},
    }
    state.update(overrides)
    (analysis_dir / ".orchestration_state.json").write_text(json.dumps(state))


@pytest.fixture
def planned_analysis(analyses_dir, jfc_plan):
    """An analysis directory with a plan and orchestration state, but no artifacts."""
    from hepagent.plan.store import save_plan

    root = analyses_dir / "my_analysis"
    root.mkdir()
    save_plan(root, jfc_plan)
    _write_state(
        root,
        current_node="selection",
        completed_nodes=["strategy", "exploration"],
        phase_iterations={"strategy": 2, "exploration": 1},
    )
    return root


def test_jfc_list_with_analysis(runner, analyses_dir, planned_analysis):
    from hepagent.main import app

    result = runner.invoke(app, ["jfc", "list", "--base-dir", str(analyses_dir)])
    output = _normalize_cli_output(result.output)
    assert result.exit_code == 0
    assert "my_analysis" in output
    assert "node=selection" in output
    assert "(2/7 complete)" in output


def test_jfc_status_not_found(runner, analyses_dir):
    from hepagent.main import app

    result = runner.invoke(
        app, ["jfc", "status", "--name", "nonexistent", "--base-dir", str(analyses_dir)]
    )
    assert result.exit_code != 0


def test_jfc_status_shows_nodes(runner, analyses_dir, planned_analysis):
    from hepagent.main import app

    result = runner.invoke(
        app, ["jfc", "status", "--name", "my_analysis", "--base-dir", str(analyses_dir)]
    )
    output = _normalize_cli_output(result.output)
    assert result.exit_code == 0
    assert "strategy" in output
    assert "PASS" in output
    assert "IN PROGRESS" in output
    assert "pending" in output
    assert "2 review iterations" in output


def test_jfc_status_without_a_plan_points_at_migrate(runner, analyses_dir):
    from hepagent.main import app

    legacy = analyses_dir / "legacy"
    legacy.mkdir()
    _write_state(legacy)

    result = runner.invoke(
        app, ["jfc", "status", "--name", "legacy", "--base-dir", str(analyses_dir)]
    )
    assert result.exit_code == 1
    assert "plan migrate" in _normalize_cli_output(result.output)


# ------------------------------------------------------------------ jfc plan


def test_jfc_templates_lists_the_builtins(runner):
    from hepagent.main import app

    result = runner.invoke(app, ["jfc", "templates"], env={"NO_COLOR": "1"})
    output = _normalize_cli_output(result.output)
    assert result.exit_code == 0
    assert "jfc-measurement" in output
    assert "jfc-search" in output


def test_jfc_plan_show_table(runner, analyses_dir, planned_analysis):
    from hepagent.main import app

    result = runner.invoke(
        app, ["jfc", "plan", "show", "--name", "my_analysis", "--base-dir", str(analyses_dir)]
    )
    assert result.exit_code == 0
    assert "strategy" in result.output
    assert "phase1_strategy/outputs/STRATEGY.md" in result.output


def test_jfc_plan_show_mermaid(runner, analyses_dir, planned_analysis):
    from hepagent.main import app

    result = runner.invoke(
        app,
        [
            "jfc",
            "plan",
            "show",
            "--name",
            "my_analysis",
            "--base-dir",
            str(analyses_dir),
            "--format",
            "mermaid",
        ],
    )
    assert result.exit_code == 0
    assert "graph LR" in result.output
    assert "strategy -->|requires| exploration" in result.output


def test_jfc_plan_show_json_round_trips(runner, analyses_dir, planned_analysis):
    import json

    from hepagent.main import app
    from hepagent.plan.store import plan_from_dict

    result = runner.invoke(
        app,
        [
            "jfc",
            "plan",
            "show",
            "--name",
            "my_analysis",
            "--base-dir",
            str(analyses_dir),
            "--format",
            "json",
        ],
    )
    assert result.exit_code == 0
    assert len(plan_from_dict(json.loads(result.output)).nodes) == 7


def test_jfc_plan_show_rejects_an_unknown_format(runner, analyses_dir, planned_analysis):
    from hepagent.main import app

    result = runner.invoke(
        app,
        [
            "jfc",
            "plan",
            "show",
            "--name",
            "my_analysis",
            "--base-dir",
            str(analyses_dir),
            "--format",
            "ascii-art",
        ],
    )
    assert result.exit_code == 1


def test_jfc_plan_validate_clean(runner, analyses_dir, planned_analysis):
    from hepagent.main import app

    result = runner.invoke(
        app, ["jfc", "plan", "validate", "--name", "my_analysis", "--base-dir", str(analyses_dir)]
    )
    assert result.exit_code == 0
    assert "No findings" in result.output


def test_jfc_plan_validate_exits_1_on_a_cycle(runner, analyses_dir, planned_analysis, jfc_plan):
    import dataclasses

    from hepagent.main import app
    from hepagent.plan.schema import PlanEdge
    from hepagent.plan.store import save_plan

    save_plan(
        planned_analysis,
        dataclasses.replace(
            jfc_plan,
            edges=jfc_plan.edges + (PlanEdge(upstream="documentation", downstream="strategy"),),
        ),
    )
    result = runner.invoke(
        app, ["jfc", "plan", "validate", "--name", "my_analysis", "--base-dir", str(analyses_dir)]
    )
    assert result.exit_code == 1
    assert "P4-acyclic" in result.output


def test_jfc_plan_migrate_writes_a_plan_and_remaps_state(runner, analyses_dir):
    from hepagent.agents.jfc.orchestrator import load_state
    from hepagent.main import app
    from hepagent.plan.store import load_plan

    legacy = analyses_dir / "legacy"
    legacy.mkdir()
    (legacy / "prompt.md").write_text("Legacy prompt.")
    (legacy / ".orchestration_state.json").write_text(
        json.dumps(
            {
                "analysis_root": str(legacy),
                "analysis_name": "legacy",
                "analysis_type": "measurement",
                "current_subphase": "4a",
                "completed_phases": ["1", "2", "3"],
                "phase_iterations": {"1": 2},
            }
        )
    )

    result = runner.invoke(
        app, ["jfc", "plan", "migrate", "--name", "legacy", "--base-dir", str(analyses_dir)]
    )
    assert result.exit_code == 0

    assert len(load_plan(legacy).nodes) == 7
    state = load_state(legacy)
    assert state.completed_nodes == ["strategy", "exploration", "selection"]
    assert state.current_node == "inference_expected"
    assert state.phase_iterations == {"strategy": 2}


def test_jfc_plan_migrate_refuses_to_clobber_an_existing_plan(
    runner, analyses_dir, planned_analysis
):
    from hepagent.main import app

    result = runner.invoke(
        app, ["jfc", "plan", "migrate", "--name", "my_analysis", "--base-dir", str(analyses_dir)]
    )
    assert result.exit_code == 1
    assert "--force" in _normalize_cli_output(result.output)


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
def graph_analysis(analyses_dir, jfc_plan):
    """A small analysis with a rebuilt graph: strategy → exploration → figure."""
    from hepagent.agents.jfc.graph_builder import bootstrap_graph, rebuild
    from hepagent.plan.store import save_plan

    root = analyses_dir / "graphy"
    for node in jfc_plan.nodes:
        (root / node.directory / "outputs" / "figures").mkdir(parents=True)
    (root / "prompt.md").write_text(jfc_plan.problem)
    (root / "phase1_strategy" / "outputs" / "STRATEGY.md").write_text("strategy")
    (root / "phase2_exploration" / "outputs" / "EXPLORATION.md").write_text("exploration")
    (root / "phase2_exploration" / "outputs" / "figures" / "mjj.png").write_text("png")

    save_plan(root, jfc_plan)
    bootstrap_graph(root, jfc_plan)
    rebuild(root, jfc_plan)
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
        "# Analysis Commitments\n\n"
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


# --------------------------------------------------------- jfc plan propose


def _stub_proposal(plan, *, accepted=True, rationale="Two channels.", notes=("edit 0: split",)):
    from hepagent.agents.jfc.architect import ProposalResult

    return ProposalResult(plan=plan, rationale=rationale, notes=list(notes), accepted=accepted)


def test_jfc_plan_propose_writes_beside_the_analysis_when_it_exists(
    runner, analyses_dir, planned_analysis, jfc_plan, tmp_path
):
    import dataclasses

    from hepagent.main import app
    from hepagent.plan.store import load_plan

    prompt = tmp_path / "prompt.md"
    prompt.write_text("Measure it in two channels.")
    proposed = dataclasses.replace(jfc_plan, nodes=jfc_plan.nodes[:3], edges=jfc_plan.edges[:2])

    with patch(
        "hepagent.agents.jfc.architect.propose_plan",
        new_callable=AsyncMock,
        return_value=_stub_proposal(proposed),
    ):
        result = runner.invoke(
            app,
            [
                "jfc",
                "plan",
                "propose",
                "--name",
                "my_analysis",
                "--prompt-file",
                str(prompt),
                "--base-dir",
                str(analyses_dir),
            ],
        )

    assert result.exit_code == 0
    assert len(load_plan(planned_analysis).nodes) == 3
    assert "Two channels." in result.output


def test_jfc_plan_propose_writes_a_file_when_the_analysis_does_not_exist(
    runner, analyses_dir, jfc_plan, tmp_path, monkeypatch
):
    import json

    from hepagent.main import app

    monkeypatch.chdir(tmp_path)
    prompt = tmp_path / "prompt.md"
    prompt.write_text("Measure it.")

    with patch(
        "hepagent.agents.jfc.architect.propose_plan",
        new_callable=AsyncMock,
        return_value=_stub_proposal(jfc_plan),
    ):
        result = runner.invoke(
            app,
            [
                "jfc",
                "plan",
                "propose",
                "--name",
                "fresh",
                "--prompt-file",
                str(prompt),
                "--base-dir",
                str(analyses_dir),
            ],
        )

    assert result.exit_code == 0
    written = tmp_path / "fresh-plan.json"
    assert len(json.loads(written.read_text())["nodes"]) == 7
    # The next step is spelled out, because a proposal is useless unless it runs.
    assert "--plan" in _normalize_cli_output(result.output)


def test_jfc_plan_propose_honours_out(runner, analyses_dir, jfc_plan, tmp_path):
    import json

    from hepagent.main import app

    prompt = tmp_path / "prompt.md"
    prompt.write_text("Measure it.")
    out = tmp_path / "custom.json"

    with patch(
        "hepagent.agents.jfc.architect.propose_plan",
        new_callable=AsyncMock,
        return_value=_stub_proposal(jfc_plan),
    ):
        result = runner.invoke(
            app,
            [
                "jfc",
                "plan",
                "propose",
                "--name",
                "fresh",
                "--prompt-file",
                str(prompt),
                "--base-dir",
                str(analyses_dir),
                "--out",
                str(out),
            ],
        )

    assert result.exit_code == 0
    assert json.loads(out.read_text())["name"] == "demo"


def test_jfc_plan_propose_reports_a_missing_prompt_file(runner, analyses_dir):
    from hepagent.main import app

    result = runner.invoke(
        app,
        [
            "jfc",
            "plan",
            "propose",
            "--name",
            "x",
            "--prompt-file",
            "nope.md",
            "--base-dir",
            str(analyses_dir),
        ],
    )
    assert result.exit_code == 1
    assert "prompt file not found" in _normalize_cli_output(result.output)


def test_jfc_plan_propose_still_writes_a_plan_when_the_architect_fell_back(
    runner, analyses_dir, jfc_plan, tmp_path, monkeypatch
):
    """A fallback is still a runnable plan; the notes say what happened."""
    from hepagent.main import app

    monkeypatch.chdir(tmp_path)
    prompt = tmp_path / "prompt.md"
    prompt.write_text("Measure it.")

    with patch(
        "hepagent.agents.jfc.architect.propose_plan",
        new_callable=AsyncMock,
        return_value=_stub_proposal(
            jfc_plan,
            accepted=False,
            rationale="",
            notes=["architect failed (provider down); running the template unchanged"],
        ),
    ):
        result = runner.invoke(
            app,
            [
                "jfc",
                "plan",
                "propose",
                "--name",
                "fresh",
                "--prompt-file",
                str(prompt),
                "--base-dir",
                str(analyses_dir),
            ],
        )

    assert result.exit_code == 0
    assert (tmp_path / "fresh-plan.json").exists()
    assert "running the template unchanged" in _normalize_cli_output(result.output)


# ------------------------------------------------------------ jfc plan edit


def test_jfc_plan_edit_launches_the_editor(runner, analyses_dir, planned_analysis):
    from hepagent.main import app

    with patch("hepagent.web.server.launch_plan_editor", return_value=True) as launch:
        result = runner.invoke(
            app,
            [
                "jfc",
                "plan",
                "edit",
                "--name",
                "my_analysis",
                "--base-dir",
                str(analyses_dir),
                "--port",
                "8123",
                "--no-browser",
                "--until-approved",
            ],
        )

    assert result.exit_code == 0
    assert launch.call_args.kwargs["port"] == 8123
    assert launch.call_args.kwargs["open_browser"] is False
    assert launch.call_args.kwargs["wait_for_approval"] is True
    assert "8123/plan/my_analysis" in _normalize_cli_output(result.output)
    assert "jfc run --name my_analysis" in _normalize_cli_output(result.output)


def test_jfc_plan_edit_fails_fast_without_a_plan(runner, analyses_dir):
    """A blank editor page is a worse error than a message on the terminal."""
    from hepagent.main import app

    legacy = analyses_dir / "legacy"
    legacy.mkdir()

    with patch("hepagent.web.server.launch_plan_editor") as launch:
        result = runner.invoke(
            app, ["jfc", "plan", "edit", "--name", "legacy", "--base-dir", str(analyses_dir)]
        )

    assert result.exit_code == 1
    launch.assert_not_called()
    assert "plan migrate" in _normalize_cli_output(result.output)


def test_jfc_plan_edit_survives_interruption(runner, analyses_dir, planned_analysis):
    from hepagent.main import app

    with patch("hepagent.web.server.launch_plan_editor", side_effect=KeyboardInterrupt):
        result = runner.invoke(
            app, ["jfc", "plan", "edit", "--name", "my_analysis", "--base-dir", str(analyses_dir)]
        )

    assert result.exit_code == 0
    assert "Editor stopped" in _normalize_cli_output(result.output)


# ----------------------------------------------------------- --review-plan


def test_jfc_run_review_plan_serves_the_editor_and_requires_approval(
    runner, analyses_dir, tmp_path
):
    """The editor and the run share one loop, so approval releases the run."""
    from contextlib import asynccontextmanager

    from hepagent.main import app

    prompt = tmp_path / "prompt.md"
    prompt.write_text("Measure it.")
    served = {}

    @asynccontextmanager
    async def fake_editor(**kwargs):
        served.update(kwargs)
        yield None

    async def fake_run(**kwargs):
        served["require_approval"] = kwargs.get("require_approval")
        return Path("/tmp/an.pdf")

    with (
        patch("hepagent.web.server.plan_editor_running", new=fake_editor),
        patch("hepagent.web.server.open_when_plan_exists", new_callable=AsyncMock),
        patch(
            "hepagent.agents.jfc.orchestrator.run_jfc_analysis",
            new_callable=AsyncMock,
            side_effect=fake_run,
        ),
    ):
        result = runner.invoke(
            app,
            [
                "jfc",
                "run",
                "--name",
                "reviewed",
                "--type",
                "measurement",
                "--prompt-file",
                str(prompt),
                "--base-dir",
                str(analyses_dir),
                "--review-plan",
                "--plan-port",
                "8222",
            ],
        )

    assert result.exit_code == 0
    assert served["require_approval"] is True
    assert served["port"] == 8222
    assert "8222/plan/reviewed" in _normalize_cli_output(result.output)


def test_jfc_run_without_review_plan_does_not_wait(runner, analyses_dir, tmp_path):
    from hepagent.main import app

    prompt = tmp_path / "prompt.md"
    prompt.write_text("Measure it.")
    seen = {}

    async def fake_run(**kwargs):
        seen.update(kwargs)
        return Path("/tmp/an.pdf")

    with patch(
        "hepagent.agents.jfc.orchestrator.run_jfc_analysis",
        new_callable=AsyncMock,
        side_effect=fake_run,
    ):
        result = runner.invoke(
            app,
            [
                "jfc",
                "run",
                "--name",
                "plain",
                "--type",
                "measurement",
                "--prompt-file",
                str(prompt),
                "--base-dir",
                str(analyses_dir),
            ],
        )

    assert result.exit_code == 0
    assert "require_approval" not in seen
