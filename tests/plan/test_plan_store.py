"""Reading and writing plan.json, and the revision history beside it."""

from __future__ import annotations

import dataclasses
import json

import pytest
from plan_factory import make_node, make_plan

from hepagent.plan import store
from hepagent.plan.schema import AnalysisPlan


def test_load_without_a_plan_raises_plan_not_found(tmp_path):
    with pytest.raises(store.PlanNotFoundError):
        store.load_plan(tmp_path)


def test_has_plan_reports_whether_the_document_exists(tmp_path, plan):
    assert not store.has_plan(tmp_path)
    store.save_plan(tmp_path, plan)
    assert store.has_plan(tmp_path)


def test_save_then_load_round_trips(tmp_path, fan_plan):
    written = store.save_plan(tmp_path, fan_plan)
    loaded = store.load_plan(tmp_path)
    assert loaded == written
    assert loaded.node_ids() == ("root", "left", "right", "merge")


def test_first_save_starts_at_revision_one(tmp_path, plan):
    assert store.save_plan(tmp_path, plan).revision == 1


def test_each_save_bumps_the_revision(tmp_path, plan):
    store.save_plan(tmp_path, plan)
    store.save_plan(tmp_path, plan)
    assert store.save_plan(tmp_path, plan).revision == 3


def test_the_revision_on_the_incoming_plan_is_ignored(tmp_path, plan):
    """Revision is a property of the file, not of whatever the caller hands over."""
    store.save_plan(tmp_path, plan)
    written = store.save_plan(tmp_path, dataclasses.replace(plan, revision=99))
    assert written.revision == 2


def test_saving_archives_the_version_it_replaces(tmp_path, plan):
    store.save_plan(tmp_path, plan)  # revision 1
    store.save_plan(tmp_path, make_plan(node_ids=("a", "b", "c"), edges=(("a", "b"), ("b", "c"))))

    assert store.list_revisions(tmp_path) == [1]
    archived = store.load_revision(tmp_path, 1)
    assert archived.node_ids() == ("a", "b")
    assert store.load_plan(tmp_path).node_ids() == ("a", "b", "c")


def test_a_first_save_archives_nothing(tmp_path, plan):
    store.save_plan(tmp_path, plan)
    assert store.list_revisions(tmp_path) == []


def test_snapshot_false_skips_the_archive(tmp_path, plan):
    store.save_plan(tmp_path, plan)
    store.save_plan(tmp_path, plan, snapshot=False)
    assert store.list_revisions(tmp_path) == []


def test_loading_a_revision_that_was_never_archived_raises(tmp_path, plan):
    store.save_plan(tmp_path, plan)
    with pytest.raises(store.PlanNotFoundError):
        store.load_revision(tmp_path, 7)


def test_history_survives_many_saves(tmp_path, plan):
    for _ in range(4):
        store.save_plan(tmp_path, plan)
    assert store.list_revisions(tmp_path) == [1, 2, 3]


def test_updated_at_is_stamped_on_every_save(tmp_path, plan):
    written = store.save_plan(tmp_path, plan)
    assert written.updated_at


# ------------------------------------------------------------ malformed input


def test_unparseable_json_raises_plan_format_error(tmp_path):
    store.plan_path(tmp_path).write_text("{ not json", encoding="utf-8")
    with pytest.raises(store.PlanFormatError):
        store.load_plan(tmp_path)


def test_a_json_array_is_not_a_plan(tmp_path):
    store.plan_path(tmp_path).write_text("[]", encoding="utf-8")
    with pytest.raises(store.PlanFormatError):
        store.load_plan(tmp_path)


def test_a_plan_missing_required_fields_raises_plan_format_error(tmp_path):
    store.plan_path(tmp_path).write_text(json.dumps({"nodes": []}), encoding="utf-8")
    with pytest.raises(store.PlanFormatError):
        store.load_plan(tmp_path)


def test_an_unreadable_document_is_still_archived_rather_than_lost(tmp_path, plan):
    """A user who hand-edits plan.json into invalid JSON must not lose it when
    the editor next saves."""
    store.plan_path(tmp_path).write_text("{ corrupted", encoding="utf-8")
    store.save_plan(tmp_path, plan)
    assert (tmp_path / store.HISTORY_DIRNAME / "0000.json").read_text() == "{ corrupted"


# --------------------------------------------------------------- on-disk form


def test_the_document_is_human_readable_json(tmp_path, plan):
    store.save_plan(tmp_path, plan)
    text = store.plan_path(tmp_path).read_text(encoding="utf-8")
    assert text.startswith("{\n")
    assert text.endswith("\n")
    assert json.loads(text)["name"] == "demo"


def test_a_plan_written_by_a_newer_build_still_loads(tmp_path, plan):
    """Unknown keys are dropped rather than raising, at both levels."""
    payload = plan.to_dict()
    payload["invented_later"] = True
    payload["nodes"][0]["invented_later"] = True
    store.plan_path(tmp_path).write_text(json.dumps(payload), encoding="utf-8")
    assert store.load_plan(tmp_path).node_ids() == ("a", "b")


def test_plan_from_dict_reports_its_source(tmp_path):
    with pytest.raises(store.PlanFormatError, match="submitted plan"):
        store.plan_from_dict([], source="submitted plan")


def test_save_creates_the_analysis_directory_if_absent(tmp_path, plan):
    target = tmp_path / "nested" / "analysis"
    store.save_plan(target, plan)
    assert store.load_plan(target).name == "demo"


def test_prompts_survive_the_round_trip_verbatim(tmp_path):
    prompt = "# Heading\n\n- bullet with `code`\n\n```python\nprint('hi')\n```\n"
    plan = AnalysisPlan(
        name="demo",
        analysis_type="measurement",
        nodes=(make_node("a", prompt=prompt),),
    )
    store.save_plan(tmp_path, plan)
    assert store.load_plan(tmp_path).require_node("a").prompt == prompt
