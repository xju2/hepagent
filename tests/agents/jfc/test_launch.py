"""Tests for the launcher the plan page starts a run through.

Only the checks that happen *before* the worker thread exists are covered here:
what a run does once it is running belongs to `test_orchestrator.py`, and the
supervision around it to `tests/plan/test_plan_runs.py`.
"""

from __future__ import annotations

import pytest


def test_a_single_node_run_names_a_node_the_plan_has(jfc_analysis):
    """Caught here, before a thread is started, so the page gets 400 not a dead run."""
    from hepagent.agents.jfc.launch import start_analysis_run

    with pytest.raises(ValueError, match="nonesuch"):
        start_analysis_run(jfc_analysis, only_node="nonesuch")
