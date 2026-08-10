"""The façade the CLI and the web editor share, including the approval latch."""

from __future__ import annotations

import asyncio

import pytest
from plan_factory import make_node, make_plan

from hepagent.plan import service, store
from hepagent.plan.schema import PlanEdge


@pytest.fixture
def gate() -> service.PlanApprovalGate:
    """A private latch, so tests never touch the process-wide singleton."""
    return service.PlanApprovalGate()


@pytest.fixture
def analysis(tmp_path, fan_plan):
    """An analysis directory with a valid plan already saved."""
    store.save_plan(tmp_path, fan_plan)
    return tmp_path


# ------------------------------------------------------------------ the view


def test_the_view_carries_everything_the_editor_needs(analysis, gate):
    view = service.get_plan_view(analysis, gate=gate)
    assert view.plan["name"] == "demo"
    assert view.layout["root"] == [0, 0]
    assert view.order == ["root", "left", "right", "merge"]
    assert view.findings == []
    assert not view.blocking
    assert not view.approved


def test_the_view_is_json_ready(analysis, gate):
    import json

    assert json.loads(json.dumps(service.get_plan_view(analysis, gate=gate).to_dict()))


def test_findings_are_ordered_errors_first(tmp_path, gate):
    plan = make_plan(
        node_ids=("a", "b", "c"),
        edges=(("a", "c"), ("b", "c"), ("ghost", "c")),
    )
    store.save_plan(tmp_path, plan)
    view = service.get_plan_view(tmp_path, gate=gate)
    severities = [f["severity"] for f in view.findings]
    assert severities == sorted(severities, key=lambda s: s != "error")
    assert view.blocking


def test_getting_a_view_without_a_plan_raises(tmp_path, gate):
    with pytest.raises(store.PlanNotFoundError):
        service.get_plan_view(tmp_path, gate=gate)


def test_reviewer_names_are_checked_when_a_registry_is_supplied(tmp_path, gate):
    plan = make_plan(node_ids=("a",), edges=(), nodes=(make_node("a", reviewers=("nope",)),))
    store.save_plan(tmp_path, plan)
    assert not service.get_plan_view(tmp_path, gate=gate).blocking
    assert service.get_plan_view(tmp_path, known_reviewers={"critical"}, gate=gate).blocking


# -------------------------------------------------------------------- saving


def test_saving_an_edit_bumps_the_revision_and_returns_the_new_view(analysis, gate):
    payload = store.load_plan(analysis).to_dict()
    payload["nodes"][0]["label"] = "Renamed"
    view = service.put_plan(analysis, payload, gate=gate)
    assert view.plan["revision"] == 2
    assert view.plan["nodes"][0]["label"] == "Renamed"
    assert store.load_plan(analysis).require_node("root").label == "Renamed"


def test_a_plan_with_blocking_findings_is_still_saved(analysis, gate):
    """A user must be able to park half-finished work; approval is what refuses."""
    payload = store.load_plan(analysis).to_dict()
    payload["edges"].append(PlanEdge(upstream="merge", downstream="root").to_dict())
    view = service.put_plan(analysis, payload, gate=gate)
    assert view.blocking
    assert len(store.load_plan(analysis).edges) == 5


def test_saving_a_malformed_payload_raises_rather_than_writing(analysis, gate):
    before = store.load_plan(analysis)
    with pytest.raises(store.PlanFormatError):
        service.put_plan(analysis, {"nodes": []}, gate=gate)
    assert store.load_plan(analysis) == before


# ------------------------------------------------------------------ approval


def test_approving_a_clean_plan_releases_the_gate(analysis, gate):
    view = service.approve(analysis, gate=gate)
    assert view.approved
    assert gate.is_approved(analysis)


def test_approving_a_broken_plan_is_refused(analysis, gate):
    payload = store.load_plan(analysis).to_dict()
    payload["edges"].append(PlanEdge(upstream="merge", downstream="root").to_dict())
    service.put_plan(analysis, payload, gate=gate)

    with pytest.raises(service.PlanNotApprovable, match="blocking"):
        service.approve(analysis, gate=gate)
    assert not gate.is_approved(analysis)


def test_editing_after_approval_withdraws_it(analysis, gate):
    """What the orchestrator was released to run is no longer what is on disk."""
    service.approve(analysis, gate=gate)
    assert gate.is_approved(analysis)

    payload = store.load_plan(analysis).to_dict()
    payload["nodes"][0]["label"] = "Changed"
    view = service.put_plan(analysis, payload, gate=gate)

    assert not view.approved
    assert not gate.is_approved(analysis)


def test_the_view_reports_a_standing_approval(analysis, gate):
    service.approve(analysis, gate=gate)
    assert service.get_plan_view(analysis, gate=gate).approved


# ------------------------------------------------------------ the latch itself


async def test_wait_returns_once_the_plan_is_approved(tmp_path, gate):
    async def approve_soon():
        await asyncio.sleep(0)
        gate.approve(tmp_path)

    task = asyncio.create_task(approve_soon())
    assert await gate.wait(tmp_path, timeout=5)
    await task


async def test_wait_times_out_while_the_plan_is_unapproved(tmp_path, gate):
    assert not await gate.wait(tmp_path, timeout=0.01)


async def test_wait_returns_immediately_when_already_approved(tmp_path, gate):
    gate.approve(tmp_path)
    assert await gate.wait(tmp_path, timeout=0.01)


def test_relative_and_absolute_paths_are_the_same_latch(tmp_path, gate, monkeypatch):
    monkeypatch.chdir(tmp_path)
    gate.approve(tmp_path)
    assert gate.is_approved(".")


def test_roots_have_independent_latches(tmp_path, gate):
    one, two = tmp_path / "one", tmp_path / "two"
    one.mkdir()
    two.mkdir()
    gate.approve(one)
    assert gate.is_approved(one)
    assert not gate.is_approved(two)


def test_revoking_makes_the_next_wait_block_again(tmp_path, gate):
    gate.approve(tmp_path)
    gate.revoke(tmp_path)
    assert not gate.is_approved(tmp_path)


def test_reset_forgets_every_latch(tmp_path, gate):
    gate.approve(tmp_path)
    gate.reset()
    assert not gate.is_approved(tmp_path)


def test_the_module_singleton_is_a_gate():
    assert isinstance(service.APPROVAL_GATE, service.PlanApprovalGate)
