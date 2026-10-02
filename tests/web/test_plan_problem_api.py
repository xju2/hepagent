"""The route behind the prompt panel's history.

The physics prompt is edited on the plan page like any other field, so the page
has to be able to answer "what did we ask before?". It does that from
`plan.history/` — the prompt is a field of the plan — which is what these tests
pin.
"""

from __future__ import annotations

import dataclasses

import pytest

fastapi = pytest.importorskip("fastapi", reason="the plan editor needs the 'web' extra")
from fastapi.testclient import TestClient  # noqa: E402

from hepagent.plan.store import read_prompt_file, save_plan  # noqa: E402
from hepagent.web.plan_api import create_router  # noqa: E402


@pytest.fixture
def analyses(tmp_path, jfc_plan):
    base = tmp_path / "analyses"
    (base / "zbb").mkdir(parents=True)
    save_plan(base / "zbb", dataclasses.replace(jfc_plan, problem="First question."))
    save_plan(base / "zbb", dataclasses.replace(jfc_plan, problem="Second question."))
    return base


@pytest.fixture
def client(analyses):
    app = fastapi.FastAPI()
    app.include_router(create_router(base_dir=analyses))
    return TestClient(app)


def test_every_wording_the_prompt_has_had_newest_first(client):
    revisions = client.get("/api/plan/zbb/problem").json()["revisions"]
    assert [entry["problem"] for entry in revisions] == [
        "Second question.",
        "First question.",
    ]
    assert revisions[0]["current"] is True


def test_saving_an_edited_prompt_rewrites_the_file_the_agents_read(client, analyses, jfc_plan):
    payload = dict(jfc_plan.to_dict(), problem="Third question.")
    assert client.put("/api/plan/zbb", json={"plan": payload}).status_code == 200

    assert read_prompt_file(analyses / "zbb") == "Third question."
    revisions = client.get("/api/plan/zbb/problem").json()["revisions"]
    assert [entry["problem"] for entry in revisions][:2] == [
        "Third question.",
        "Second question.",
    ]


def test_an_unknown_analysis_is_a_404(client):
    assert client.get("/api/plan/nope/problem").status_code == 404


def test_a_name_that_escapes_the_base_directory_is_refused(client):
    assert client.get("/api/plan/..%2F..%2Fetc/problem").status_code in (400, 404)
