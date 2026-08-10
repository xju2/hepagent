"""Tests for the plan editor's HTTP routes.

Skipped when FastAPI is absent — CI runs without extras, and the decisions these
routes translate are already covered by `tests/plan/test_plan_service.py`. What
is under test here is the translation itself: status codes, path safety, and the
route ordering that a Chainlit-hosted mount depends on.
"""

from __future__ import annotations

import dataclasses

import pytest

fastapi = pytest.importorskip("fastapi", reason="the plan editor needs the 'web' extra")
from fastapi.testclient import TestClient  # noqa: E402

from hepagent.plan.schema import PlanEdge  # noqa: E402
from hepagent.plan.service import PlanApprovalGate  # noqa: E402
from hepagent.plan.store import load_plan, save_plan  # noqa: E402
from hepagent.web.plan_api import create_router, mount  # noqa: E402


@pytest.fixture
def analyses(tmp_path, jfc_plan):
    """An analyses directory holding one planned analysis."""
    base = tmp_path / "analyses"
    (base / "zbb").mkdir(parents=True)
    save_plan(base / "zbb", jfc_plan)
    (base / "no_plan").mkdir()
    return base


@pytest.fixture
def gate():
    return PlanApprovalGate()


@pytest.fixture
def client(analyses, gate):
    app = fastapi.FastAPI()
    app.include_router(create_router(base_dir=analyses, gate=gate))
    return TestClient(app)


# --------------------------------------------------------------------- read


def test_get_plan_returns_the_view_the_editor_draws_from(client):
    body = client.get("/api/plan/zbb").json()
    assert {"plan", "layout", "order", "findings", "blocking", "approved"} <= body.keys()
    assert len(body["plan"]["nodes"]) == 7
    assert body["order"][0] == "strategy"
    assert body["layout"]["strategy"] == [0, 0]
    assert body["blocking"] is False
    assert body["approved"] is False


def test_get_plan_404s_for_an_unknown_analysis(client):
    assert client.get("/api/plan/ghost").status_code == 404


def test_get_plan_404s_when_the_analysis_has_no_plan(client):
    response = client.get("/api/plan/no_plan")
    assert response.status_code == 404
    assert "plan" in response.json()["detail"].lower()


def test_the_editor_page_is_served_and_self_contained(client):
    response = client.get("/plan/zbb")
    assert response.status_code == 200
    html = response.text
    assert "<svg" in html
    # No external requests: a strict-CSP-free page still must not phone home.
    for scheme in ("https://", "http://cdn", "//cdn."):
        assert f'src="{scheme}' not in html
        assert f'href="{scheme}' not in html


def test_the_editor_page_404s_for_an_unknown_analysis(client):
    assert client.get("/plan/ghost").status_code == 404


def test_list_plans_reports_only_analyses_that_have_one(client, analyses):
    body = client.get("/api/plan").json()
    assert [a["name"] for a in body["analyses"]] == ["zbb"]
    assert body["base_dir"] == str(analyses)


# ----------------------------------------------------------------- traversal


@pytest.mark.parametrize("name", ["../secret", "..%2Fsecret", "a/../../etc"])
def test_a_name_that_escapes_the_base_directory_is_refused(client, name):
    """The name comes from a URL, and PUT writes files."""
    for path in (f"/api/plan/{name}", f"/plan/{name}"):
        assert client.get(path).status_code in (400, 404)


def test_a_traversing_name_cannot_write_outside_the_base(client, tmp_path, jfc_plan):
    outside = tmp_path / "outside"
    outside.mkdir()
    response = client.put("/api/plan/../outside", json={"plan": jfc_plan.to_dict()})
    assert response.status_code in (400, 404)
    assert not (outside / "plan.json").exists()


# -------------------------------------------------------------------- write


def test_put_saves_the_plan_and_returns_the_fresh_view(client, analyses, jfc_plan):
    trimmed = dataclasses.replace(
        jfc_plan,
        nodes=tuple(n for n in jfc_plan.nodes if n.id in {"strategy", "exploration"}),
        edges=tuple(
            e for e in jfc_plan.edges if {e.upstream, e.downstream} <= {"strategy", "exploration"}
        ),
    )
    body = client.put("/api/plan/zbb", json={"plan": trimmed.to_dict()}).json()

    assert len(body["plan"]["nodes"]) == 2
    assert body["plan"]["revision"] == 2  # the fixture's save was revision 1
    assert len(load_plan(analyses / "zbb").nodes) == 2


def test_put_accepts_a_bare_plan_document_too(client, analyses, jfc_plan):
    """The editor sends `{plan: ...}`; a script may reasonably send the plan."""
    body = client.put("/api/plan/zbb", json=jfc_plan.to_dict()).json()
    assert len(body["plan"]["nodes"]) == 7


def test_put_saves_a_broken_plan_but_reports_it(client, analyses, jfc_plan):
    """Half-finished work has to survive a reload; approval is what refuses."""
    cyclic = dataclasses.replace(
        jfc_plan,
        edges=jfc_plan.edges + (PlanEdge(upstream="documentation", downstream="strategy"),),
    )
    body = client.put("/api/plan/zbb", json={"plan": cyclic.to_dict()}).json()

    assert body["blocking"] is True
    assert any(f["rule"] == "P4-acyclic" for f in body["findings"])
    assert len(load_plan(analyses / "zbb").edges) == len(cyclic.edges)


def test_put_422s_on_a_payload_that_is_not_a_plan(client):
    assert client.put("/api/plan/zbb", json={"plan": {"nodes": "not a list"}}).status_code == 422


def test_findings_carry_the_node_so_the_editor_can_highlight_it(client, jfc_plan):
    orphaned = dataclasses.replace(
        jfc_plan,
        edges=jfc_plan.edges + (PlanEdge(upstream="documentation", downstream="strategy"),),
    )
    body = client.put("/api/plan/zbb", json={"plan": orphaned.to_dict()}).json()
    errors = [f for f in body["findings"] if f["severity"] == "error"]
    assert errors and errors[0]["node_id"]


def test_errors_sort_before_warnings(client, jfc_plan):
    cyclic = dataclasses.replace(
        jfc_plan,
        edges=jfc_plan.edges + (PlanEdge(upstream="documentation", downstream="strategy"),),
    )
    findings = client.put("/api/plan/zbb", json={"plan": cyclic.to_dict()}).json()["findings"]
    severities = [f["severity"] for f in findings]
    assert severities == sorted(severities, key=lambda s: s != "error")


# ------------------------------------------------------------------ approve


def test_approve_releases_the_gate(client, analyses, gate):
    body = client.post("/api/plan/zbb/approve").json()
    assert body["approved"] is True
    assert gate.is_approved(analyses / "zbb")


def test_approve_409s_while_a_blocking_finding_stands(client, analyses, gate, jfc_plan):
    """The button is disabled for the same reason; the API must not be a bypass."""
    cyclic = dataclasses.replace(
        jfc_plan,
        edges=jfc_plan.edges + (PlanEdge(upstream="documentation", downstream="strategy"),),
    )
    client.put("/api/plan/zbb", json={"plan": cyclic.to_dict()})

    response = client.post("/api/plan/zbb/approve")
    assert response.status_code == 409
    assert "blocking" in response.json()["detail"]
    assert not gate.is_approved(analyses / "zbb")


def test_saving_after_approval_withdraws_it(client, analyses, gate, jfc_plan):
    """What the orchestrator was released to run is no longer what is on disk."""
    client.post("/api/plan/zbb/approve")
    assert gate.is_approved(analyses / "zbb")

    body = client.put("/api/plan/zbb", json={"plan": jfc_plan.to_dict()}).json()
    assert body["approved"] is False
    assert not gate.is_approved(analyses / "zbb")


def test_approval_state_is_reported_on_read(client):
    assert client.get("/api/plan/zbb").json()["approved"] is False
    client.post("/api/plan/zbb/approve")
    assert client.get("/api/plan/zbb").json()["approved"] is True


# -------------------------------------------------------------------- mount


def test_mount_inserts_routes_ahead_of_an_existing_catch_all(analyses):
    """Chainlit registers its SPA catch-all at import time; ours must win.

    Starlette matches in order, so a router added with `include_router` after a
    `/{full_path:path}` route is never reached for a GET. This is the bug the
    front-insertion in `mount` exists to avoid, and it is invisible until the
    editor is served from Chainlit rather than standalone.
    """
    app = fastapi.FastAPI()

    @app.get("/{full_path:path}")
    async def catch_all(full_path: str):
        return {"spa": full_path}

    mount(app, base_dir=analyses)
    client = TestClient(app)

    assert client.get("/api/plan/zbb").json()["plan"]["name"] == "demo"
    assert client.get("/plan/zbb").text.startswith("<!doctype html>")
    # The catch-all still serves everything else.
    assert client.get("/anything-else").json() == {"spa": "anything-else"}


def test_include_router_after_a_catch_all_would_not_have_worked(analyses):
    """Pins the hazard itself, so a refactor back to `include_router` fails here."""
    app = fastapi.FastAPI()

    @app.get("/{full_path:path}")
    async def catch_all(full_path: str):
        return {"spa": full_path}

    app.include_router(create_router(base_dir=analyses))
    assert TestClient(app).get("/api/plan/zbb").json() == {"spa": "api/plan/zbb"}
