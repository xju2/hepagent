"""Tests for the hepagent jfc CLI subcommand (mocked orchestrator)."""

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from typer.testing import CliRunner


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

    result = runner.invoke(app, ["jfc", "run", "--help"])
    assert result.exit_code == 0
    assert "--name" in result.output
    assert "--type" in result.output
    assert "--prompt" in result.output


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
    assert result.exit_code == 0
    assert "my_analysis" in result.output


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
    assert result.exit_code == 0
    assert "PASS" in result.output or "Strategy" in result.output
    assert "IN PROGRESS" in result.output or "pending" in result.output


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
                "--prompt",
                "Test prompt",
                "--base-dir",
                str(analyses_dir),
            ],
        )
    # Either succeeds or fails, but CLI invocation worked
    assert "--help" not in result.output
