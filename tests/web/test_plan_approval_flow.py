"""The approval handshake, end to end.

`run_jfc_analysis(require_approval=True)` blocks before the first node until the
editor releases the latch. That crosses two subsystems — the FastAPI route and
the orchestrator's `await gate.wait(...)` — and neither side's own tests would
catch a mismatch, so it is exercised here against a real HTTP client.

No model is called: the first node's executor and review gate are stubbed.
"""

from __future__ import annotations

import asyncio
import dataclasses
from unittest.mock import AsyncMock, patch

import pytest

fastapi = pytest.importorskip("fastapi", reason="the plan editor needs the 'web' extra")
from fastapi.testclient import TestClient  # noqa: E402

from hepagent.plan.schema import PlanEdge  # noqa: E402
from hepagent.plan.service import PlanApprovalGate  # noqa: E402
from hepagent.plan.store import save_plan  # noqa: E402
from hepagent.web.plan_api import create_router  # noqa: E402


@pytest.fixture
def analysis(tmp_path, jfc_plan):
    """A scaffolded-enough analysis: a plan and a prompt, nothing run yet."""
    base = tmp_path / "analyses"
    root = base / "zbb"
    root.mkdir(parents=True)
    (root / "prompt.md").write_text(jfc_plan.problem, encoding="utf-8")
    save_plan(root, jfc_plan)
    return base, root


@pytest.fixture
def gate():
    return PlanApprovalGate()


@pytest.fixture
def client(analysis, gate):
    base, _ = analysis
    app = fastapi.FastAPI()
    app.include_router(create_router(base_dir=base, gate=gate))
    return TestClient(app)


async def _run(base, gate, progress):
    """Start an analysis that waits for approval, with the agents stubbed out."""
    from hepagent.agents.jfc.orchestrator import run_jfc_analysis

    with (
        patch("hepagent.agents.jfc.orchestrator.scaffold_jfc_analysis", new_callable=AsyncMock),
        patch("hepagent.agents.jfc.orchestrator._run_executor", new_callable=AsyncMock),
        patch(
            "hepagent.agents.jfc.orchestrator._run_note_writer_and_typesetter",
            new_callable=AsyncMock,
            return_value=None,
        ),
        patch(
            "hepagent.agents.jfc.orchestrator._human_gate",
            new_callable=AsyncMock,
            return_value=True,
        ),
        patch("hepagent.agents.jfc.orchestrator.run_review_gate", new_callable=AsyncMock) as gated,
        patch("hepagent.agents.jfc.orchestrator._final_pdf", return_value="an.pdf"),
    ):
        from hepagent.agents.jfc.review_gate import ReviewGateResult

        gated.return_value = ReviewGateResult(verdict="PASS")
        return await run_jfc_analysis(
            analysis_name="zbb",
            physics_prompt="Measure it.",
            analysis_type="measurement",
            base_dir=str(base),
            progress_callback=progress,
            require_approval=True,
            approval_gate=gate,
        )


@pytest.mark.asyncio
async def test_the_run_waits_until_the_editor_approves(analysis, gate, client):
    base, root = analysis
    progress: list[tuple[str, str]] = []

    task = asyncio.create_task(_run(base, gate, lambda n, m: progress.append((n, m))))

    # Give the orchestrator a chance to reach the latch, then confirm it did.
    for _ in range(80):
        if any("waiting for the plan" in m for _, m in progress):
            break
        await asyncio.sleep(0.02)
    assert any("waiting for the plan" in m for _, m in progress), "the run did not wait"
    assert not task.done()
    assert not gate.is_approved(root)

    assert client.post("/api/plan/zbb/approve").status_code == 200

    await asyncio.wait_for(task, timeout=10)
    assert any("approved — starting" in m for _, m in progress)
    assert any(node == "strategy" for node, _ in progress)


@pytest.mark.asyncio
async def test_an_already_approved_plan_does_not_wait(analysis, gate, client):
    base, _ = analysis
    assert client.post("/api/plan/zbb/approve").status_code == 200

    progress: list[tuple[str, str]] = []
    await asyncio.wait_for(_run(base, gate, lambda n, m: progress.append((n, m))), timeout=10)
    assert not any("waiting for the plan" in m for _, m in progress)


@pytest.mark.asyncio
async def test_the_run_picks_up_edits_made_while_it_waited(analysis, gate, client, jfc_plan):
    """Approval releases what is *on disk*, not what was there when the run began."""
    base, root = analysis
    progress: list[tuple[str, str]] = []
    task = asyncio.create_task(_run(base, gate, lambda n, m: progress.append((n, m))))

    for _ in range(80):
        if any("waiting for the plan" in m for _, m in progress):
            break
        await asyncio.sleep(0.02)
    assert not task.done()

    trimmed = dataclasses.replace(
        jfc_plan,
        nodes=tuple(n for n in jfc_plan.nodes if n.id in {"strategy", "exploration"}),
        edges=(PlanEdge(upstream="strategy", downstream="exploration"),),
    )
    assert client.put("/api/plan/zbb", json={"plan": trimmed.to_dict()}).status_code == 200
    assert client.post("/api/plan/zbb/approve").status_code == 200

    await asyncio.wait_for(task, timeout=10)
    ran = [node for node, _ in progress]
    assert "exploration" in ran
    assert "selection" not in ran  # the node the user deleted never ran


@pytest.mark.asyncio
async def test_saving_while_the_run_waits_does_not_release_it(analysis, gate, client, jfc_plan):
    """A save withdraws approval, so an edit cannot accidentally start the run."""
    base, root = analysis
    progress: list[tuple[str, str]] = []
    task = asyncio.create_task(_run(base, gate, lambda n, m: progress.append((n, m))))

    for _ in range(80):
        if any("waiting for the plan" in m for _, m in progress):
            break
        await asyncio.sleep(0.02)

    assert client.put("/api/plan/zbb", json={"plan": jfc_plan.to_dict()}).status_code == 200
    await asyncio.sleep(0.1)
    assert not task.done()
    assert not gate.is_approved(root)

    client.post("/api/plan/zbb/approve")
    await asyncio.wait_for(task, timeout=10)


@pytest.mark.asyncio
async def test_a_blocking_finding_cannot_release_the_run(analysis, gate, client, jfc_plan):
    base, root = analysis
    progress: list[tuple[str, str]] = []
    task = asyncio.create_task(_run(base, gate, lambda n, m: progress.append((n, m))))

    for _ in range(80):
        if any("waiting for the plan" in m for _, m in progress):
            break
        await asyncio.sleep(0.02)

    cyclic = dataclasses.replace(
        jfc_plan,
        edges=jfc_plan.edges + (PlanEdge(upstream="documentation", downstream="strategy"),),
    )
    client.put("/api/plan/zbb", json={"plan": cyclic.to_dict()})
    assert client.post("/api/plan/zbb/approve").status_code == 409

    await asyncio.sleep(0.1)
    assert not task.done(), "a plan with a cycle must not start a run"

    client.put("/api/plan/zbb", json={"plan": jfc_plan.to_dict()})
    client.post("/api/plan/zbb/approve")
    await asyncio.wait_for(task, timeout=10)
