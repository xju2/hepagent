"""Tests for JFC review gate."""

import pytest


@pytest.fixture
def analysis_root(tmp_path):
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
    (root / "prompt.md").write_text("Test prompt")
    (root / "COMMITMENTS.md").write_text(
        "# Phase 1 Commitments\n\n"
        "| ID | Commitment | Status | Evidence | Phase Resolved |\n"
        "|----|-----------|--------|----------|---------------|\n"
    )
    return root


def test_review_gate_result_structure():
    from hepagent.agents.jfc.review_gate import ReviewGateResult

    r = ReviewGateResult(verdict="PASS", category_a_findings=[], category_b_findings=[])
    assert r.verdict == "PASS"
    assert r.category_a_findings == []


def test_phase_escalation_error():
    from hepagent.agents.jfc.review_gate import PhaseEscalationError, ReviewGateResult

    result = ReviewGateResult(verdict="ESCALATE", category_a_findings=["bad thing"])
    err = PhaseEscalationError(1, result)
    assert err.phase == 1
    assert err.result is result


def test_phase1_reviewer_composition():
    from hepagent.agents.jfc.review_gate import _PHASE_REVIEWERS

    phase1_reviewers = _PHASE_REVIEWERS[1]
    assert "physics" in phase1_reviewers
    assert "critical" in phase1_reviewers
    assert "constructive" in phase1_reviewers


def test_phase2_reviewer_composition():
    from hepagent.agents.jfc.review_gate import _PHASE_REVIEWERS

    phase2_reviewers = _PHASE_REVIEWERS[2]
    assert "plot" in phase2_reviewers
    assert "physics" not in phase2_reviewers


def test_phase4a_reviewer_composition():
    from hepagent.agents.jfc.review_gate import _PHASE_REVIEWERS

    phase4a_reviewers = _PHASE_REVIEWERS["4a"]
    assert "physics" in phase4a_reviewers
    assert "bibtex" in phase4a_reviewers
    assert "plot" in phase4a_reviewers


def test_phase5_reviewer_composition():
    from hepagent.agents.jfc.review_gate import _PHASE_REVIEWERS

    phase5_reviewers = _PHASE_REVIEWERS[5]
    assert "rendering" in phase5_reviewers
    assert "bibtex" in phase5_reviewers
    assert len(phase5_reviewers) == 6


def test_arbiter_phases():
    from hepagent.agents.jfc.review_gate import _ARBITER_PHASES

    assert 1 in _ARBITER_PHASES
    assert "4a" in _ARBITER_PHASES
    assert "4b" in _ARBITER_PHASES
    assert 5 in _ARBITER_PHASES
    assert 3 not in _ARBITER_PHASES


def test_parse_verdict_pass(tmp_path):
    from hepagent.agents.jfc.review_gate import _parse_verdict_from_adjudication

    adj = tmp_path / "ADJUDICATION.md"
    adj.write_text("# Adjudication\n\nAll findings reviewed.\n\nPASS")
    verdict, cat_a, cat_b, origin = _parse_verdict_from_adjudication(adj)
    assert verdict == "PASS"
    assert origin is None


def test_parse_verdict_iterate(tmp_path):
    from hepagent.agents.jfc.review_gate import _parse_verdict_from_adjudication

    adj = tmp_path / "ADJUDICATION.md"
    adj.write_text("# Adjudication\n\nCategory A findings found.\n\nITERATE")
    verdict, cat_a, cat_b, origin = _parse_verdict_from_adjudication(adj)
    assert verdict == "ITERATE"
    assert origin is None


def test_parse_verdict_regress_int(tmp_path):
    from hepagent.agents.jfc.review_gate import _parse_verdict_from_adjudication

    adj = tmp_path / "ADJUDICATION.md"
    adj.write_text("# Adjudication\n\nRoot cause in Phase 3.\n\nREGRESS(3)")
    verdict, cat_a, cat_b, origin = _parse_verdict_from_adjudication(adj)
    assert verdict == "REGRESS"
    assert origin == 3


def test_parse_verdict_regress_subphase(tmp_path):
    from hepagent.agents.jfc.review_gate import _parse_verdict_from_adjudication

    adj = tmp_path / "ADJUDICATION.md"
    adj.write_text("# Adjudication\n\nRoot cause in Phase 4a.\n\nREGRESS(4a)")
    verdict, cat_a, cat_b, origin = _parse_verdict_from_adjudication(adj)
    assert verdict == "REGRESS"
    assert origin == "4a"


def test_phase_regression_error():
    from hepagent.agents.jfc.review_gate import PhaseRegressionError, ReviewGateResult

    result = ReviewGateResult(verdict="REGRESS", regression_origin_phase=3)
    err = PhaseRegressionError("4a", 3, "bad selection cut", result)
    assert err.detected_phase == "4a"
    assert err.origin_phase == 3
    assert err.symptom == "bad selection cut"
    assert err.result is result
