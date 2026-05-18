"""Tests for JFC commitment checker."""

from pathlib import Path

import pytest


@pytest.fixture
def analysis_root(tmp_path):
    root = tmp_path / "test_analysis"
    root.mkdir()
    return root


def write_commitments(root: Path, rows: list[tuple]) -> None:
    lines = [
        "# Phase 1 Commitments\n",
        "| ID | Commitment | Status | Evidence | Phase Resolved |\n",
        "|----|-----------|--------|----------|---------------|\n",
    ]
    for row in rows:
        cid, text, status, evidence, phase = row
        lines.append(f"| {cid} | {text} | {status} | {evidence} | {phase} |\n")
    (root / "COMMITMENTS.md").write_text("".join(lines))


def test_no_commitments_file(analysis_root):
    from hepagent.agents.jfc.commitment_checker import check_phase1_commitments

    result = check_phase1_commitments(analysis_root)
    assert result.all_resolved is True


def test_all_resolved(analysis_root):
    from hepagent.agents.jfc.commitment_checker import check_phase1_commitments

    write_commitments(
        analysis_root,
        [
            ("D1", "Compare cut-based and MVA", "resolved", "chi2=1.3", "3"),
            ("D2", "Use unfolding", "resolved", "closure passed", "4a"),
        ],
    )
    result = check_phase1_commitments(analysis_root)
    assert result.all_resolved is True
    assert len(result.resolved) == 2
    assert len(result.pending) == 0


def test_pending_blocks(analysis_root):
    from hepagent.agents.jfc.commitment_checker import check_phase1_commitments

    write_commitments(
        analysis_root,
        [
            ("D1", "Compare cut-based and MVA", "resolved", "chi2=1.3", "3"),
            ("D2", "Use unfolding", "pending", "—", "—"),
        ],
    )
    result = check_phase1_commitments(analysis_root)
    assert result.all_resolved is False
    assert len(result.pending) == 1
    assert result.pending[0].id == "D2"
    assert "D2" in result.blocking_message


def test_downscoped_counts_as_resolved(analysis_root):
    from hepagent.agents.jfc.commitment_checker import check_phase1_commitments

    write_commitments(
        analysis_root,
        [
            ("D1", "Run PYTHIA 8", "downscoped", "Install failed: error X", "4a"),
        ],
    )
    result = check_phase1_commitments(analysis_root)
    assert result.all_resolved is True
    assert len(result.downscoped) == 1


def test_commitments_not_resolved_exception(analysis_root):
    from hepagent.agents.jfc.commitment_checker import (
        CommitmentsNotResolved,
        check_phase1_commitments,
    )

    write_commitments(
        analysis_root,
        [
            ("D1", "Will do X", "pending", "—", "—"),
        ],
    )
    result = check_phase1_commitments(analysis_root)
    with pytest.raises(CommitmentsNotResolved) as exc_info:
        if not result.all_resolved:
            raise CommitmentsNotResolved(result)
    assert "D1" in str(exc_info.value)
