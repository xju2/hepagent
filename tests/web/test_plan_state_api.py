"""Tests for the progress panel's data route.

The panel answers "what does this analysis know", which is a question about the
analysis directory rather than about a run. These tests pin that: the route
works with no run in the registry, before anything has been produced, and it
reports the strategy's process inventory once it exists.
"""

from __future__ import annotations

import json

import pytest

fastapi = pytest.importorskip("fastapi", reason="the plan editor needs the 'web' extra")
from fastapi.testclient import TestClient  # noqa: E402

from hepagent.agents.jfc.processes import ProcessInventory, save_inventory  # noqa: E402
from hepagent.plan.store import save_plan  # noqa: E402
from hepagent.web.plan_api import create_router  # noqa: E402

INVENTORY = {
    "processes": [
        {
            "id": "ggH",
            "role": "signal",
            "label": "gg -> H -> tautau",
            "datasets": [{"name": "GluGluToHToTauTau.root", "source": "prompt"}],
        },
        {
            "id": "dy",
            "role": "background",
            "category": "irreducible",
            "importance": "dominant",
            "datasets": [{"name": "DYJetsToLL.root", "source": "prompt"}],
        },
    ]
}


@pytest.fixture
def analyses(tmp_path, jfc_plan):
    base = tmp_path / "analyses"
    (base / "zbb").mkdir(parents=True)
    save_plan(base / "zbb", jfc_plan)
    return base


@pytest.fixture
def client(analyses):
    app = fastapi.FastAPI()
    app.include_router(create_router(base_dir=analyses))
    return TestClient(app)


def test_a_plan_that_never_ran_still_has_a_state(client, jfc_plan):
    """The panel is present from the moment the plan is, not from the first run."""
    body = client.get("/api/plan/zbb/state").json()

    assert [row["id"] for row in body["nodes"]] == list(jfc_plan.node_ids())
    assert all(row["produced"] is False for row in body["nodes"])
    assert body["processes"]["recorded"] is False


def test_the_recorded_inventory_reaches_the_page(client, analyses, jfc_plan):
    save_inventory(
        analyses / "zbb",
        jfc_plan.node("strategy"),
        ProcessInventory.from_json(json.dumps(INVENTORY)),
    )
    processes = client.get("/api/plan/zbb/state").json()["processes"]

    assert processes["recorded"] is True
    assert processes["node_id"] == "strategy"
    sections = {s["name"]: [p["id"] for p in s["processes"]] for s in processes["sections"]}
    assert sections["signal"] == ["ggH"]
    assert sections["irreducible"] == ["dy"]
    assert sections["reducible"] == []


def test_a_written_artifact_shows_as_produced(client, analyses, jfc_plan):
    node = jfc_plan.node("strategy")
    (analyses / "zbb" / node.outputs_dir).mkdir(parents=True, exist_ok=True)
    (analyses / "zbb" / node.artifact_path).write_text("# Strategy\n", encoding="utf-8")

    rows = {row["id"]: row for row in client.get("/api/plan/zbb/state").json()["nodes"]}
    assert rows["strategy"]["produced"] is True
    assert rows["exploration"]["produced"] is False


def test_an_analysis_without_a_plan_is_a_404(client, analyses):
    (analyses / "unplanned").mkdir()
    assert client.get("/api/plan/unplanned/state").status_code == 404


def test_a_name_that_escapes_the_base_directory_is_refused(client):
    """Invariant 9: the name in a plan URL is resolved, never concatenated."""
    assert client.get("/api/plan/..%2F..%2Fetc/state").status_code in (400, 404)
