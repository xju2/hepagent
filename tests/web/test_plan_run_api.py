"""Tests for launching and supervising a run from the plan page.

These cover the HTTP half only: what starts a run, what refuses to, and how a
question reaches the page and an answer reaches the run. The threading is
covered by `tests/plan/test_plan_runs.py`, and the real orchestrator is replaced
here by a launcher that finishes immediately — this suite is about the routes.
"""

from __future__ import annotations

import time

import pytest

fastapi = pytest.importorskip("fastapi", reason="the plan editor needs the 'web' extra")
from fastapi.testclient import TestClient  # noqa: E402

from hepagent.plan import runs  # noqa: E402
from hepagent.plan.service import PlanApprovalGate  # noqa: E402
from hepagent.plan.store import save_plan  # noqa: E402
from hepagent.web.plan_api import create_router  # noqa: E402


@pytest.fixture
def analyses(tmp_path, jfc_plan):
    base = tmp_path / "analyses"
    (base / "zbb").mkdir(parents=True)
    save_plan(base / "zbb", jfc_plan)
    return base


@pytest.fixture(autouse=True)
def clean_registry():
    """The registry is process-wide, exactly as it is in a running server."""
    runs.RUNS.reset()
    yield
    runs.RUNS.reset()


@pytest.fixture
def gate():
    return PlanApprovalGate()


def _client(analyses, gate, runner, launched=None):
    """A client whose launcher runs `runner` instead of a real analysis.

    `launched`, when given, collects the keyword arguments each launch was made
    with — which is how the per-node run is checked without a real orchestrator.
    """

    def launcher(
        analysis_root,
        *,
        name,
        model,
        unattended,
        max_iterations,
        max_turns=None,
        only_node=None,
    ):
        if launched is not None:
            launched.append(
                {
                    "only_node": only_node,
                    "unattended": unattended,
                    "name": name,
                    "model": model,
                    "max_iterations": max_iterations,
                    "max_turns": max_turns,
                }
            )
        return runs.RUNS.start(name, analysis_root, runner, unattended=unattended)

    app = fastapi.FastAPI()
    app.include_router(create_router(base_dir=analyses, gate=gate, launcher=launcher))
    return TestClient(app)


def _wait_until(predicate, timeout: float = 5.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


def test_no_run_yet_is_a_state_not_an_error(analyses, gate):
    """The editor asks on every load; "nothing is running" is its normal answer."""
    client = _client(analyses, gate, lambda h: "done")
    body = client.get("/api/plan/zbb/run").json()

    assert body["status"] == "idle"
    assert body["active"] is False


def test_a_run_cannot_start_before_the_plan_is_approved(analyses, gate):
    client = _client(analyses, gate, lambda h: "done")
    response = client.post("/api/plan/zbb/run", json={})

    assert response.status_code == 409
    assert "Approve the plan" in response.json()["detail"]


def test_approving_reports_whether_anything_was_waiting(analyses, gate):
    """With nobody waiting, the page must start the run itself — see plan.html."""
    client = _client(analyses, gate, lambda h: "done")
    body = client.post("/api/plan/zbb/approve").json()

    assert body["approved"] is True
    assert body["awaited"] is False


def test_an_approved_plan_runs_and_reports_progress(analyses, gate):
    def work(handle):
        handle.set_node_status("strategy", "done")
        handle.record("strategy", "PASS")
        return "note.pdf"

    client = _client(analyses, gate, work)
    client.post("/api/plan/zbb/approve")
    started = client.post("/api/plan/zbb/run", json={})
    assert started.status_code == 200

    assert _wait_until(lambda: client.get("/api/plan/zbb/run").json()["status"] == "done")
    body = client.get("/api/plan/zbb/run").json()
    assert body["result"] == "note.pdf"
    assert body["nodes"] == {"strategy": "done"}
    assert [e["message"] for e in body["events"]] == ["PASS"]


def test_events_are_polled_incrementally(analyses, gate):
    def work(handle):
        handle.record("strategy", "one")
        handle.record("strategy", "two")
        return "done"

    client = _client(analyses, gate, work)
    client.post("/api/plan/zbb/approve")
    client.post("/api/plan/zbb/run", json={})
    assert _wait_until(lambda: client.get("/api/plan/zbb/run").json()["status"] == "done")

    assert [e["message"] for e in client.get("/api/plan/zbb/run?since=1").json()["events"]] == [
        "two"
    ]


def test_a_second_run_is_refused_while_one_is_in_flight(analyses, gate):
    import threading

    release = threading.Event()
    client = _client(analyses, gate, lambda h: release.wait(5) and "done")
    client.post("/api/plan/zbb/approve")
    try:
        assert client.post("/api/plan/zbb/run", json={}).status_code == 200
        second = client.post("/api/plan/zbb/run", json={})
        assert second.status_code == 409
        assert "already in progress" in second.json()["detail"]
    finally:
        release.set()


def test_a_blocked_run_surfaces_its_question_and_takes_the_answer(analyses, gate):
    answers = []

    def work(handle):
        answers.append(handle.request_approval("root -l -q fit.C", cwd="/x", thought="fit"))
        return "done"

    client = _client(analyses, gate, work)
    client.post("/api/plan/zbb/approve")
    client.post("/api/plan/zbb/run", json={})

    assert _wait_until(lambda: client.get("/api/plan/zbb/run").json()["prompt"] is not None)
    body = client.get("/api/plan/zbb/run").json()
    assert body["status"] == "blocked"
    assert body["prompt"]["cmd"] == "root -l -q fit.C"

    answered = client.post(
        "/api/plan/zbb/run/answer", json={"id": body["prompt"]["id"], "approved": True}
    )
    assert answered.status_code == 200
    assert _wait_until(lambda: client.get("/api/plan/zbb/run").json()["status"] == "done")
    assert answers[0].approved is True


def test_a_stale_answer_is_refused(analyses, gate):
    client = _client(analyses, gate, lambda h: h.request_approval("ls") and "done")
    client.post("/api/plan/zbb/approve")
    client.post("/api/plan/zbb/run", json={})
    assert _wait_until(lambda: client.get("/api/plan/zbb/run").json()["prompt"] is not None)

    stale = client.post("/api/plan/zbb/run/answer", json={"id": "999", "approved": True})
    assert stale.status_code == 409


def test_answering_with_no_run_is_a_404(analyses, gate):
    client = _client(analyses, gate, lambda h: "done")
    assert client.post("/api/plan/zbb/run/answer", json={"id": "1"}).status_code == 404


def test_an_unattended_run_never_asks(analyses, gate):
    seen = []
    client = _client(analyses, gate, lambda h: seen.append(h.request_approval("ls")) or "done")
    client.post("/api/plan/zbb/approve")
    client.post("/api/plan/zbb/run", json={"unattended": True})

    assert _wait_until(lambda: client.get("/api/plan/zbb/run").json()["status"] == "done")
    assert seen[0].approved is True


def test_cancelling_a_run(analyses, gate):
    import threading

    started = threading.Event()

    def work(handle):
        handle.record("strategy", "starting")
        started.set()
        while not handle.cancelled:
            time.sleep(0.01)
        handle.record("strategy", "next node")  # raises RunCancelled
        return "done"

    client = _client(analyses, gate, work)
    client.post("/api/plan/zbb/approve")
    client.post("/api/plan/zbb/run", json={})
    assert started.wait(5)

    assert client.post("/api/plan/zbb/run/cancel").status_code == 200
    assert _wait_until(lambda: client.get("/api/plan/zbb/run").json()["status"] == "cancelled")


def test_cancelling_nothing_is_a_404(analyses, gate):
    client = _client(analyses, gate, lambda h: "done")
    assert client.post("/api/plan/zbb/run/cancel").status_code == 404


def test_run_routes_refuse_a_name_that_escapes_the_base_directory(analyses, gate):
    """The run routes take the same name a write route does, so they check it too."""
    client = _client(analyses, gate, lambda h: "done")
    assert client.get("/api/plan/..%2F..%2Fetc/run").status_code in (400, 404)


def test_one_node_can_be_run_without_approving_the_plan(analyses, gate):
    """The per-node Run button.

    Approval is a statement about the pipeline, and saving an edit withdraws it —
    so requiring it here would make the button unusable for the one thing it is
    for: testing the node you just edited.
    """
    launched: list[dict] = []
    client = _client(analyses, gate, lambda h: "strategy/outputs/STRATEGY.md", launched)

    response = client.post("/api/plan/zbb/run", json={"only_node": "strategy"})

    assert response.status_code == 200
    assert launched == [
        {
            "only_node": "strategy",
            "unattended": False,
            "name": "zbb",
            "model": None,
            "max_iterations": 3,
            "max_turns": None,
        }
    ]
    assert gate.is_approved(analyses / "zbb") is False


def test_a_whole_plan_run_still_needs_approval(analyses, gate):
    client = _client(analyses, gate, lambda h: "done")
    assert client.post("/api/plan/zbb/run", json={"only_node": ""}).status_code == 409


def test_a_blocking_finding_refuses_a_single_node_run(analyses, gate, jfc_plan):
    """The same bar approval clears: a plan that cannot run cannot run one node."""
    import dataclasses

    from hepagent.plan.schema import PlanEdge
    from hepagent.plan.store import save_plan

    launched: list[dict] = []
    client = _client(analyses, gate, lambda h: "done", launched)
    cyclic = dataclasses.replace(
        jfc_plan,
        edges=jfc_plan.edges + (PlanEdge(upstream="documentation", downstream="strategy"),),
    )
    save_plan(analyses / "zbb", cyclic)

    response = client.post("/api/plan/zbb/run", json={"only_node": "strategy"})

    assert response.status_code == 409
    assert "blocking" in response.json()["detail"]
    assert launched == []


def test_a_node_the_plan_does_not_have_is_a_bad_request(analyses, gate):
    def launcher(
        analysis_root,
        *,
        name,
        model,
        unattended,
        max_iterations,
        max_turns=None,
        only_node=None,
    ):
        raise ValueError(f"Plan 'zbb' has no node '{only_node}'.")

    app = fastapi.FastAPI()
    app.include_router(create_router(base_dir=analyses, gate=gate, launcher=launcher))
    client = TestClient(app)

    response = client.post("/api/plan/zbb/run", json={"only_node": "nonesuch"})

    assert response.status_code == 400
    assert "nonesuch" in response.json()["detail"]


def test_run_settings_reach_the_launcher(analyses, gate):
    """The dock header's model, iteration and turn settings are launch arguments.

    They only mean anything at launch, so a run that ignored them would look
    configured and behave as if it were not.
    """
    launched: list[dict] = []
    client = _client(analyses, gate, lambda h: "done", launched)
    client.post("/api/plan/zbb/approve")

    response = client.post(
        "/api/plan/zbb/run",
        json={"model": "amsc:gpt-5.5", "max_iterations": 5, "max_turns": 120},
    )

    assert response.status_code == 200
    assert launched[0]["model"] == "amsc:gpt-5.5"
    assert launched[0]["max_iterations"] == 5
    assert launched[0]["max_turns"] == 120


def test_a_blank_turn_cap_defers_to_the_role_defaults(analyses, gate):
    """Blank is not zero. The page sends null, and null must stay null: a 0 here
    would be a turn cap of zero rather than "let each role decide"."""
    launched: list[dict] = []
    client = _client(analyses, gate, lambda h: "done", launched)
    client.post("/api/plan/zbb/approve")

    response = client.post("/api/plan/zbb/run", json={"max_turns": None, "model": ""})

    assert response.status_code == 200
    assert launched[0]["max_turns"] is None
    assert launched[0]["model"] is None
