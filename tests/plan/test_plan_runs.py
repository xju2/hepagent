"""Tests for the run supervisor behind the plan page.

The interesting behaviour is all about two threads: a run that blocks on a human
and a web request that unblocks it. These tests drive both sides for real —
threads, events, timeouts — because a mocked one would prove nothing about the
handoff that actually matters.
"""

from __future__ import annotations

import threading
import time

import pytest

from hepagent.plan import runs
from hepagent.plan.runs import RunAlreadyActive, RunCancelled, RunInteractionBackend, RunRegistry


def _wait_until(predicate, timeout: float = 5.0) -> bool:
    """Poll `predicate` until it holds. Threads make this the honest wait."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


@pytest.fixture
def registry() -> RunRegistry:
    return RunRegistry()


def test_a_finished_run_records_its_result(registry, tmp_path):
    handle = registry.start("zbb", tmp_path, lambda h: "note.pdf")

    assert _wait_until(lambda: handle.status == "done")
    assert handle.snapshot()["result"] == "note.pdf"
    assert handle.snapshot()["active"] is False


def test_a_failing_run_is_recorded_rather_than_raised(registry, tmp_path):
    """A run that dies must leave the server up and the page informed."""

    def explode(handle):
        raise RuntimeError("tectonic is not installed")

    handle = registry.start("zbb", tmp_path, explode)

    assert _wait_until(lambda: handle.status == "failed")
    assert "tectonic is not installed" in handle.snapshot()["error"]


def test_events_are_returned_incrementally(registry, tmp_path):
    def emit(handle):
        for index in range(3):
            handle.record("selection", f"step {index}")
        return "done"

    handle = registry.start("zbb", tmp_path, emit)
    assert _wait_until(lambda: handle.status == "done")

    everything = handle.snapshot()
    assert [e["message"] for e in everything["events"]] == ["step 0", "step 1", "step 2"]
    # What a polling page asks for: only what it has not seen.
    assert [e["message"] for e in handle.snapshot(since=2)["events"]] == ["step 2"]
    assert everything["latest_seq"] == 3


def test_a_second_run_for_the_same_analysis_is_refused(registry, tmp_path):
    release = threading.Event()
    registry.start("zbb", tmp_path, lambda h: release.wait(5) and "done")
    try:
        with pytest.raises(RunAlreadyActive):
            registry.start("zbb", tmp_path, lambda h: "done")
    finally:
        release.set()


def test_an_approval_blocks_the_run_until_the_page_answers(registry, tmp_path):
    answers = []

    def ask(handle):
        answers.append(handle.request_approval("rm -rf outputs", cwd="/a", thought="clean up"))
        return "done"

    handle = registry.start("zbb", tmp_path, ask)
    assert _wait_until(lambda: handle.snapshot()["prompt"] is not None)

    prompt = handle.snapshot()["prompt"]
    assert prompt["kind"] == "approval"
    assert prompt["cmd"] == "rm -rf outputs"
    assert handle.snapshot()["status"] == "blocked"

    assert handle.answer(prompt["id"], {"approved": False, "reason": "not that directory"}) is True
    assert _wait_until(lambda: handle.status == "done")
    assert answers[0].approved is False
    assert answers[0].reason == "not that directory"


def test_an_answer_to_a_prompt_that_moved_on_is_refused(registry, tmp_path):
    """Two tabs, or a slow click: the stale answer must not approve the new thing."""
    handle = registry.start("zbb", tmp_path, lambda h: h.request_approval("ls") and "done")
    assert _wait_until(lambda: handle.snapshot()["prompt"] is not None)

    assert handle.answer("nonexistent", {"approved": True}) is False
    assert handle.answer(handle.snapshot()["prompt"]["id"], {"approved": True}) is True


def test_unattended_runs_do_not_ask(registry, tmp_path):
    seen = []
    handle = registry.start(
        "zbb", tmp_path, lambda h: seen.append(h.request_approval("ls")) or "done", unattended=True
    )

    assert _wait_until(lambda: handle.status == "done")
    assert seen[0].approved is True
    assert handle.snapshot()["prompt"] is None


def test_a_question_returns_the_text_the_page_sent(registry, tmp_path):
    seen = []

    def ask(handle):
        seen.append(handle.ask("Approve the note?", choices=("APPROVE", "ITERATE")))
        return "done"

    handle = registry.start("zbb", tmp_path, ask)
    assert _wait_until(lambda: handle.snapshot()["prompt"] is not None)
    prompt = handle.snapshot()["prompt"]
    assert prompt["choices"] == ["APPROVE", "ITERATE"]

    handle.answer(prompt["id"], {"text": "ITERATE"})
    assert _wait_until(lambda: handle.status == "done")
    assert seen == ["ITERATE"]


def test_an_unanswered_prompt_gives_up_rather_than_pinning_the_thread(
    registry, tmp_path, monkeypatch
):
    monkeypatch.setattr(runs, "PROMPT_TIMEOUT_SECONDS", 0.05)
    seen = []
    handle = registry.start("zbb", tmp_path, lambda h: seen.append(h.request_approval("ls")) or "x")

    assert _wait_until(lambda: handle.status == "done")
    assert seen[0].approved is False  # a page that never answered is not an approval


def test_cancelling_unwinds_at_the_next_progress_report(registry, tmp_path):
    started, ran_second_node = threading.Event(), []

    def two_nodes(handle):
        handle.record("selection", "starting")
        started.set()
        _wait_until(lambda: handle.cancelled)
        handle.record("fit", "starting")  # raises RunCancelled
        ran_second_node.append(True)
        return "done"

    handle = registry.start("zbb", tmp_path, two_nodes)
    assert started.wait(5)
    handle.cancel()

    assert _wait_until(lambda: handle.status == "cancelled")
    assert ran_second_node == []


def test_cancelling_releases_a_run_blocked_on_a_human(registry, tmp_path):
    """Nobody is coming to answer, so the prompt must not hold the thread forever."""
    handle = registry.start("zbb", tmp_path, lambda h: h.request_approval("ls") and "done")
    assert _wait_until(lambda: handle.snapshot()["prompt"] is not None)

    handle.cancel()
    assert _wait_until(lambda: handle.status in {"cancelled", "done", "failed"})


def test_the_backend_is_the_bridge_between_a_tool_and_the_page(registry, tmp_path):
    """`RunInteractionBackend` is what a synchronous function tool actually calls."""
    handle = registry.start("zbb", tmp_path, lambda h: RunInteractionBackend(h).ask("Which run?"))
    assert _wait_until(lambda: handle.snapshot()["prompt"] is not None)

    handle.answer(handle.snapshot()["prompt"]["id"], {"text": "run 2"})
    assert _wait_until(lambda: handle.status == "done")
    assert handle.snapshot()["result"] == "run 2"


def test_node_status_is_what_the_page_colours_from(registry, tmp_path):
    def work(handle):
        handle.set_node_status("selection", "done")
        handle.set_node_status("fit", "running")
        return "done"

    handle = registry.start("zbb", tmp_path, work)
    assert _wait_until(lambda: handle.status == "done")
    assert handle.snapshot()["nodes"] == {"selection": "done", "fit": "running"}


def test_the_event_log_is_bounded(registry, tmp_path, monkeypatch):
    """A week-long run must not turn its progress log into a memory leak."""
    monkeypatch.setattr(runs, "MAX_EVENTS", 10)

    def chatty(handle):
        for index in range(50):
            handle.record("selection", f"step {index}")
        return "done"

    handle = registry.start("zbb", tmp_path, chatty)
    assert _wait_until(lambda: handle.status == "done")

    snapshot = handle.snapshot()
    assert len(snapshot["events"]) == 10
    assert snapshot["events"][-1]["message"] == "step 49"
    assert snapshot["dropped_events"] == 40


def test_record_raises_only_after_cancelling(registry, tmp_path):
    handle = runs.RunHandle("zbb", tmp_path)
    handle.record("selection", "fine")
    handle.cancel()
    with pytest.raises(RunCancelled):
        handle.record("selection", "too late")
