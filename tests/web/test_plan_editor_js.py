"""Tests for the plan editor page itself.

`plan.html` is one self-contained page with its script inline, which would
otherwise be the only untested surface in the plan layer — and it is the surface
a physicist actually touches. These tests run the page's JavaScript in Node
against a stub DOM (`plan_editor_harness.mjs`), so the drag, add, connect and
save paths are exercised rather than assumed.

Skipped when Node is unavailable. The page is still checked for self-containment
by `test_plan_api.py`, which needs no JavaScript runtime.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from hepagent.plan.service import build_view

HARNESS = Path(__file__).with_name("plan_editor_harness.mjs")
PAGE = Path("src/hepagent/web/static/plan.html").resolve()

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="needs Node")


@pytest.fixture(scope="module")
def script(tmp_path_factory) -> Path:
    """The page's inline script, extracted to a file Node can load."""
    html = PAGE.read_text(encoding="utf-8")
    blocks = re.findall(r"<script>(.*?)</script>", html, re.S)
    assert len(blocks) == 1, "the page should carry exactly one inline script"
    path = tmp_path_factory.mktemp("editor") / "plan.js"
    path.write_text(blocks[0], encoding="utf-8")
    return path


@pytest.fixture(scope="module")
def observations(script, tmp_path_factory) -> dict:
    """Run the page against a stub DOM and return what it did."""
    from hepagent.plan.templates import instantiate

    plan = instantiate(
        "jfc-measurement",
        analysis_name="zbb",
        analysis_type="measurement",
        physics_prompt="Measure the Z->bb cross section.",
    )
    view = tmp_path_factory.mktemp("editor-view") / "view.json"
    view.write_text(json.dumps(build_view(plan).to_dict()), encoding="utf-8")

    result = subprocess.run(
        ["node", str(HARNESS), str(script), str(view)],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, f"harness failed:\n{result.stderr}"
    return json.loads(result.stdout)


def test_the_page_script_parses(script):
    """A syntax error would render a blank editor with nothing in the log."""
    result = subprocess.run(
        ["node", "--check", str(script)], capture_output=True, text=True, timeout=60
    )
    assert result.returncode == 0, result.stderr


def test_the_page_loads_the_plan_and_labels_itself(observations):
    assert observations["title"] == "zbb — analysis plan"
    assert "measurement" in observations["subtitle"]
    assert "7 nodes" in observations["subtitle"]
    assert observations["nodes"] == 7


def test_every_node_and_edge_is_drawn(observations):
    """One group per node, one path plus one click target per edge, plus defs."""
    assert observations["svg_children"] == observations["svg_expected"]


def test_a_clean_plan_loads_saved_and_approvable(observations):
    assert observations["state_after_load"] == "saved"
    assert observations["approve_disabled_after_load"] is False
    assert observations["findings_rows"] == 1  # the "no findings" row


def test_positions_come_from_the_layout_until_a_node_is_dragged(observations):
    assert observations["layout_position"] == {"x": 40, "y": 40}
    assert observations["dragged_position"] == {"x": 500, "y": 12}


def test_relayout_restores_the_computed_position(observations):
    assert observations["position_after_relayout"] == {"x": 40, "y": 40}


def test_a_new_node_is_usable_immediately(observations):
    """A node with no reviewers or prompt would be a trap, not a starting point."""
    added = observations["added"]
    assert added["id"] == "node1"
    assert added["artifact"] == "NODE1.md"
    assert added["reviewers"] == ["critical"]
    assert added["has_prompt"] is True
    assert observations["side_heading"] == "Node · node1"


def test_editing_marks_the_document_unsaved_and_blocks_approval(observations):
    assert observations["state_after_edit"] == "unsaved"
    assert observations["approve_disabled_after_edit"] is True


def test_connect_mode_creates_one_blocking_edge(observations):
    assert observations["edge_added"] == 1
    assert observations["new_edge"]["kind"] == "requires"
    assert observations["new_edge"]["inject"] == "full"
    assert observations["new_edge"]["upstream"] == "strategy"


def test_a_self_edge_is_refused_in_the_page(observations):
    """The validator would catch it, but not before the user saved."""
    assert observations["self_edge_refused"] is True


def test_saving_sends_the_whole_document(observations):
    put = observations["put"]
    assert put["url"] == "/api/plan/zbb"
    assert put["wraps_plan"] is True
    assert put["carries_new_node"] is True
    assert put["carries_new_edge"] is True


def test_approving_saved_work_goes_straight_through(observations):
    assert observations["approve_flow_clean"] == ["POST /api/plan/zbb/approve"]


def test_approving_unsaved_work_saves_it_first(observations):
    """Otherwise the run would start from the plan on disk, not the one on screen."""
    assert observations["dirty_before_approve"] is True
    assert observations["approve_flow_dirty"] == [
        "PUT /api/plan/zbb",
        "POST /api/plan/zbb/approve",
    ]
