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


def test_the_run_thread_carries_both_a_backend_and_a_sink(jfc_analysis, monkeypatch):
    """Questions reach the page, and so does everything the agents narrate.

    Both are thread-local, so the only thing worth asserting is that they are
    bound *on the worker thread* — a fixture that checked the calling thread
    would pass while the page stayed silent.
    """
    import time

    from hepagent import activity, interaction
    from hepagent.agents.jfc import launch
    from hepagent.plan.runs import RunActivitySink, RunInteractionBackend, RunRegistry

    seen: dict[str, object] = {}

    async def fake_run(**kwargs):
        seen["backend"] = interaction.current_backend()
        seen["sink"] = activity.current_sink()
        kwargs["progress_callback"]("strategy", "executor starting")
        return jfc_analysis / "note.pdf"

    monkeypatch.setattr("hepagent.agents.jfc.orchestrator.run_jfc_analysis", fake_run, raising=True)

    registry = RunRegistry()
    handle = launch.start_analysis_run(jfc_analysis, registry=registry)

    deadline = time.time() + 5
    while handle.status not in {"done", "failed", "cancelled"} and time.time() < deadline:
        time.sleep(0.01)

    assert handle.status == "done", handle.error
    assert isinstance(seen["backend"], RunInteractionBackend)
    assert isinstance(seen["sink"], RunActivitySink)
    # The progress callback also tells the handle where narration belongs.
    assert handle.current_node == "strategy"
