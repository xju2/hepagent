"""Tests for the JFC codesign gate."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.fixture
def analysis_root(tmp_path):
    root = tmp_path / "test_analysis"
    strategy_dir = root / "phase1_strategy" / "outputs"
    strategy_dir.mkdir(parents=True)
    (root / "phase1_strategy" / "review").mkdir(parents=True)
    (strategy_dir / "STRATEGY.md").write_text(
        "# Analysis Strategy\n\n"
        "## Observable\nDimuon invariant mass.\n\n"
        "## Method\nTemplate fit with MC.\n\n"
        "## Systematics\nTracking efficiency: 1%.\n"
    )
    (root / "prompt.md").write_text("Measure Z boson cross-section.")
    return root


def test_create_codesign_agent(analysis_root):
    from hepagent.agents.jfc.codesign import create_codesign_agent

    agent = create_codesign_agent(analysis_root)
    assert "Codesign" in agent.name
    tool_names = [t.name for t in agent.tools]
    assert "read_file" in tool_names
    assert "write_review" in tool_names
    assert "ask_user_for_info" in tool_names
    assert "web_search" not in tool_names


def test_create_codesign_agent_instructions_contain_paths(analysis_root):
    from hepagent.agents.jfc.codesign import create_codesign_agent

    agent = create_codesign_agent(analysis_root)
    assert "STRATEGY.md" in agent.instructions
    assert "CODESIGN_SUMMARY.md" in agent.instructions
    assert "HUMAN_FEEDBACK.md" in agent.instructions


def test_create_codesign_arbiter(analysis_root):
    from hepagent.agents.jfc.codesign import create_codesign_arbiter

    agent = create_codesign_arbiter(analysis_root)
    assert "Arbiter" in agent.name
    tool_names = [t.name for t in agent.tools]
    assert "read_file" in tool_names
    assert "write_review" in tool_names


def test_create_codesign_arbiter_embeds_strategy(analysis_root):
    from hepagent.agents.jfc.codesign import create_codesign_arbiter

    agent = create_codesign_arbiter(analysis_root)
    assert "Dimuon invariant mass" in agent.instructions


def test_create_codesign_arbiter_no_feedback_file(analysis_root):
    from hepagent.agents.jfc.codesign import create_codesign_arbiter

    agent = create_codesign_arbiter(analysis_root)
    assert "No human feedback recorded" in agent.instructions


def test_create_codesign_arbiter_with_feedback(analysis_root):
    from hepagent.agents.jfc.codesign import create_codesign_arbiter

    codesign_dir = analysis_root / "phase1_strategy" / "codesign"
    codesign_dir.mkdir(parents=True)
    (codesign_dir / "HUMAN_FEEDBACK.md").write_text(
        "# Human Feedback\n\n## Open Items\n- OPEN: Why not unbinned fit?\n"
    )

    agent = create_codesign_arbiter(analysis_root)
    assert "unbinned fit" in agent.instructions


def test_parse_codesign_verdict_pass(tmp_path):
    from hepagent.agents.jfc.codesign import _parse_codesign_verdict

    adj = tmp_path / "CODESIGN_ADJUDICATION.md"
    adj.write_text("Assessment complete.\n\nFinal verdict:\n\nPASS")
    assert _parse_codesign_verdict(adj, "") == "PROCEED"


def test_parse_codesign_verdict_iterate(tmp_path):
    from hepagent.agents.jfc.codesign import _parse_codesign_verdict

    adj = tmp_path / "CODESIGN_ADJUDICATION.md"
    adj.write_text("Concerns found.\n\nFinal verdict:\n\nITERATE")
    assert _parse_codesign_verdict(adj, "") == "REVISE"


def test_parse_codesign_verdict_fallback_pass(tmp_path):
    from hepagent.agents.jfc.codesign import _parse_codesign_verdict

    missing = tmp_path / "nonexistent.md"
    assert _parse_codesign_verdict(missing, "Strategy is sound. PASS") == "PROCEED"


def test_parse_codesign_verdict_fallback_iterate(tmp_path):
    from hepagent.agents.jfc.codesign import _parse_codesign_verdict

    missing = tmp_path / "nonexistent.md"
    assert _parse_codesign_verdict(missing, "Needs revision. ITERATE") == "REVISE"


@pytest.mark.asyncio
async def test_run_codesign_gate_proceed(analysis_root):
    from hepagent.agents.jfc.codesign import run_codesign_gate

    codesign_dir = analysis_root / "phase1_strategy" / "codesign"

    def write_adj(*args, **kwargs):
        codesign_dir.mkdir(parents=True, exist_ok=True)
        (codesign_dir / "CODESIGN_ADJUDICATION.md").write_text("Strategy is sound.\n\nPASS")

    AsyncMock(side_effect=[MagicMock(final_output=""), write_adj()])

    with patch("hepagent.agents.jfc.codesign.Runner.run", new_callable=AsyncMock) as mock_runner:
        mock_runner.side_effect = [
            MagicMock(final_output="summary written"),
            MagicMock(final_output="PASS"),
        ]

        # Write PASS adjudication before the arbiter runs
        codesign_dir.mkdir(parents=True, exist_ok=True)
        (codesign_dir / "CODESIGN_ADJUDICATION.md").write_text("All clear.\n\nPASS")

        verdict = await run_codesign_gate(analysis_root)

    assert verdict == "PROCEED"


@pytest.mark.asyncio
async def test_run_codesign_gate_revise(analysis_root):
    from hepagent.agents.jfc.codesign import run_codesign_gate

    codesign_dir = analysis_root / "phase1_strategy" / "codesign"
    codesign_dir.mkdir(parents=True, exist_ok=True)
    (codesign_dir / "CODESIGN_ADJUDICATION.md").write_text("Open concerns found.\n\nITERATE")

    with patch("hepagent.agents.jfc.codesign.Runner.run", new_callable=AsyncMock) as mock_runner:
        mock_runner.return_value = MagicMock(final_output="ITERATE")
        verdict = await run_codesign_gate(analysis_root)

    assert verdict == "REVISE"


@pytest.mark.asyncio
async def test_run_codesign_gate_creates_codesign_dir(analysis_root):
    from hepagent.agents.jfc.codesign import run_codesign_gate

    codesign_dir = analysis_root / "phase1_strategy" / "codesign"
    assert not codesign_dir.exists()

    with patch("hepagent.agents.jfc.codesign.Runner.run", new_callable=AsyncMock) as mock_runner:
        mock_runner.return_value = MagicMock(final_output="PASS")
        await run_codesign_gate(analysis_root)

    assert codesign_dir.exists()


@pytest.mark.asyncio
async def test_run_codesign_gate_calls_progress(analysis_root):
    from hepagent.agents.jfc.codesign import run_codesign_gate

    codesign_dir = analysis_root / "phase1_strategy" / "codesign"
    codesign_dir.mkdir(parents=True, exist_ok=True)
    (codesign_dir / "CODESIGN_ADJUDICATION.md").write_text("PASS")

    progress_log: list[tuple[str, str]] = []

    def cb(phase, status):
        progress_log.append((phase, status))

    with patch("hepagent.agents.jfc.codesign.Runner.run", new_callable=AsyncMock) as mock_runner:
        mock_runner.return_value = MagicMock(final_output="PASS")
        await run_codesign_gate(analysis_root, progress_callback=cb)

    assert any("codesign" in p for p, _ in progress_log)


@pytest.mark.asyncio
async def test_orchestrator_runs_codesign_gate_on_phase1_pass(tmp_path):
    """Test that run_jfc_analysis calls run_codesign_gate after Phase 1 PASS."""
    from hepagent.agents.jfc.review_gate import ReviewGateResult

    root = tmp_path / "test_analysis"
    for phase in [
        "phase1_strategy",
        "phase2_exploration",
        "phase3_selection",
        "phase4a_inference_expected",
        "phase4b_inference_partial",
        "phase4c_inference_observed",
        "phase5_documentation",
    ]:
        (root / phase / "outputs").mkdir(parents=True)
        (root / phase / "review").mkdir(parents=True)
    (root / "prompt.md").write_text("Test physics prompt")
    (root / "COMMITMENTS.md").write_text(
        "| ID | Commitment | Status | Evidence | Phase Resolved |\n"
        "|----|-----------|--------|----------|---------------|\n"
        "| D1 | Test | resolved | Evidence | 1 |\n"
    )

    pass_result = ReviewGateResult(verdict="PASS")
    codesign_called: list[bool] = []

    async def mock_codesign_gate(root, **kwargs):
        codesign_called.append(True)
        return "PROCEED"

    with (
        patch("hepagent.agents.jfc.orchestrator._run_executor", new_callable=AsyncMock),
        patch(
            "hepagent.agents.jfc.orchestrator.run_review_gate",
            new_callable=AsyncMock,
            return_value=pass_result,
        ),
        patch(
            "hepagent.agents.jfc.orchestrator.run_codesign_gate",
            side_effect=mock_codesign_gate,
        ),
        patch("hepagent.agents.jfc.orchestrator.scaffold_jfc_analysis", new_callable=AsyncMock),
        patch(
            "hepagent.agents.jfc.orchestrator.check_phase1_commitments",
            return_value=MagicMock(all_resolved=True),
        ),
        patch(
            "hepagent.agents.jfc.orchestrator._run_note_writer_and_typesetter",
            new_callable=AsyncMock,
            return_value=None,
        ),
    ):
        from hepagent.agents.jfc.orchestrator import run_jfc_analysis

        # Run just Phase 1 and 2 (stop before 4a commitments check)
        try:
            await run_jfc_analysis(
                analysis_name="test_analysis",
                physics_prompt="Test prompt",
                analysis_type="measurement",
                base_dir=str(tmp_path),
                codesign=True,
                start_from_phase=1,
            )
        except Exception:
            pass  # May fail at later phases; we only care that codesign was called

    assert codesign_called, "run_codesign_gate was not called with codesign=True"


@pytest.mark.asyncio
async def test_orchestrator_skips_codesign_gate_when_disabled(tmp_path):
    """Test that run_jfc_analysis does NOT call run_codesign_gate when codesign=False."""
    from hepagent.agents.jfc.review_gate import ReviewGateResult

    root = tmp_path / "test_analysis"
    for phase in [
        "phase1_strategy",
        "phase2_exploration",
        "phase3_selection",
        "phase4a_inference_expected",
        "phase4b_inference_partial",
        "phase4c_inference_observed",
        "phase5_documentation",
    ]:
        (root / phase / "outputs").mkdir(parents=True)
        (root / phase / "review").mkdir(parents=True)
    (root / "prompt.md").write_text("Test physics prompt")
    (root / "COMMITMENTS.md").write_text(
        "| ID | Commitment | Status | Evidence | Phase Resolved |\n"
        "|----|-----------|--------|----------|---------------|\n"
        "| D1 | Test | resolved | Evidence | 1 |\n"
    )

    pass_result = ReviewGateResult(verdict="PASS")
    codesign_called: list[bool] = []

    async def mock_codesign_gate(root, **kwargs):
        codesign_called.append(True)
        return "PROCEED"

    with (
        patch("hepagent.agents.jfc.orchestrator._run_executor", new_callable=AsyncMock),
        patch(
            "hepagent.agents.jfc.orchestrator.run_review_gate",
            new_callable=AsyncMock,
            return_value=pass_result,
        ),
        patch(
            "hepagent.agents.jfc.orchestrator.run_codesign_gate",
            side_effect=mock_codesign_gate,
        ),
        patch("hepagent.agents.jfc.orchestrator.scaffold_jfc_analysis", new_callable=AsyncMock),
        patch(
            "hepagent.agents.jfc.orchestrator.check_phase1_commitments",
            return_value=MagicMock(all_resolved=True),
        ),
        patch(
            "hepagent.agents.jfc.orchestrator._run_note_writer_and_typesetter",
            new_callable=AsyncMock,
            return_value=None,
        ),
    ):
        from hepagent.agents.jfc.orchestrator import run_jfc_analysis

        try:
            await run_jfc_analysis(
                analysis_name="test_analysis",
                physics_prompt="Test prompt",
                analysis_type="measurement",
                base_dir=str(tmp_path),
                codesign=False,
            )
        except Exception:
            pass

    assert not codesign_called, "run_codesign_gate was called despite codesign=False"


def test_executor_receives_codesign_feedback_on_revise(analysis_root):
    """Codesign feedback must appear in the Phase 1 executor prompt on revision runs."""
    from hepagent.agents.jfc.executor import create_phase_executor

    feedback = "## Open Items\n- OPEN: Why template fit instead of unbinned likelihood?\n"
    agent = create_phase_executor(1, analysis_root, codesign_feedback=feedback)
    assert "HUMAN FEEDBACK FROM CODESIGN REVIEW" in agent.instructions
    assert "unbinned likelihood" in agent.instructions
    assert "known limitation" in agent.instructions


def test_executor_no_codesign_feedback_by_default(analysis_root):
    """Without codesign feedback, the executor prompt must not contain the feedback section."""
    from hepagent.agents.jfc.executor import create_phase_executor

    agent = create_phase_executor(1, analysis_root)
    assert "HUMAN FEEDBACK FROM CODESIGN REVIEW" not in agent.instructions


def test_jfc_run_help_shows_codesign():
    from typer.testing import CliRunner

    from hepagent.main import app

    runner = CliRunner()
    result = runner.invoke(app, ["jfc", "run", "--help"])
    assert result.exit_code == 0
    assert "--codesign" in result.output
