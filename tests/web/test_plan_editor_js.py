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
from hepagent.plan.validate import PlanVocabulary

HARNESS = Path(__file__).with_name("plan_editor_harness.mjs")
PAGE = Path("src/hepagent/web/static/plan.html").resolve()

#: Stands in for what the server derives from this installation. Fixed here so
#: the page's dropdowns are asserted against a known list rather than against
#: whatever tools and skills happen to be installed on the machine running CI.
VOCABULARY = PlanVocabulary(
    reviewers=["bibtex", "constructive", "critical", "physics", "plot", "rendering"],
    tools=["graph_query", "read_phase_artifact", "run_pixi_task"],
    skills=["nyx", "widgets"],
    mcp_servers=["lep-corpus"],
    platforms=["cborg", "openai"],
)

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
    view.write_text(json.dumps(build_view(plan, vocabulary=VOCABULARY).to_dict()), encoding="utf-8")

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
    """Column 0 belongs to the prompt, so the plan's own first column is column 1."""
    assert observations["layout_position"] == {"x": 340, "y": 40}
    assert observations["dragged_position"] == {"x": 500, "y": 12}


# --------------------------------------------------------------------- drag


def test_a_dragged_node_keeps_the_point_it_was_grabbed_by(observations):
    """Grabbed 60px inside its own corner, it stays 60px inside all the way.

    Its corner is at x=340, not x=40: the prompt owns column 0.
    """
    drag = observations["drag"]
    assert drag["position"] == {"x": 380, "y": 120}
    assert drag["transform"] == "translate(380,120)"


def test_dragging_does_not_rebuild_the_element_it_is_dragging(observations):
    """A re-render mid-move destroys the group holding the pointer capture.

    That is what made a drag stutter and then drop the node: the browser fires
    `lostpointercapture` the moment the captured element leaves the document, so
    every frame the drag had to be re-acquired. The move updates the group in
    place instead, and only re-renders once the pointer is released.
    """
    drag = observations["drag"]
    assert drag["group_survived_the_move"] is True
    assert drag["rerendered_on_release"] is True


def test_edges_follow_the_node_while_it_is_dragged(observations):
    """The node's right edge is at 380+210, and the dragged node's own slot on it.

    Not its middle: the strategy node feeds five others, so each of those edges
    leaves from its own slot down the side — see the routing tests below.
    """
    assert observations["drag"]["edge_path"].startswith("M 590 133.95")


def test_no_two_edges_are_drawn_as_the_same_line(observations):
    """The complaint this routing exists for.

    The seven-phase template is a chain on one row where nearly every node feeds
    nearly every later one. Drawn as a straight run between node sides, an edge
    skipping a column landed exactly on top of the short edges it overflew: the
    picture showed six arrows for fifteen dependencies, and the missing ones only
    appeared once a node had been dragged out of the line.
    """
    routing = observations["routing"]
    assert routing["distinct_paths"] == routing["edges"] == 15


def test_no_edge_is_drawn_across_a_node_it_does_not_touch(observations):
    """An edge that would cut through a box bows under it instead.

    Checked against the path the page actually emitted, re-flattened in the
    harness, so this fails if the routing is merely *meant* to clear the boxes.
    """
    assert observations["routing"]["through_a_box"] == []


def test_the_canvas_grows_around_the_routed_edges(observations):
    """Detoured edges run below the lowest node, so the boxes do not bound the
    drawing any more — sizing to them alone would clip the arcs off the page."""
    routing = observations["routing"]
    assert routing["lowest_point"] > 40 + 62  # below the row of boxes
    assert routing["canvas_height"] >= routing["lowest_point"] + 40


def test_a_drag_can_leave_the_current_canvas_bounds(observations):
    """The canvas grows under the pointer, so a node cannot hit an invisible wall."""
    assert observations["drag"]["canvas_width"] >= 380 + 210 + 40


def test_a_completed_drag_marks_the_document_unsaved(observations):
    drag = observations["drag"]
    assert drag["position_after_release"] == {"x": 380, "y": 120}
    assert drag["dirty_after_release"] is True


# ------------------------------------------------------------------- prompt


def test_the_physics_prompt_is_drawn_as_the_head_of_the_graph(observations):
    """The question is the analysis's starting point, so it is drawn as one."""
    node = observations["prompt_node"]
    assert node["drawn"] is True
    assert node["label"] == "Physics prompt"
    # Column 0, level with the node it feeds — which the drag above left at y=120.
    assert node["position"] == {"x": 40, "y": 120}
    # Everything with nothing blocking it hangs off the prompt.
    assert node["seeds"] == ["strategy"]


def test_clicking_the_prompt_opens_its_panel(observations):
    assert observations["prompt_panel_from_canvas"] == "Physics prompt"


def test_the_physics_prompt_is_editable(observations):
    """The complaint this answers: the prompt could be read and not changed."""
    panel = observations["prompt_panel"]
    assert panel["heading"] == "Physics prompt"
    assert panel["editable"] is True
    assert "Z->bb cross section" in panel["body"]


def test_editing_the_prompt_rewrites_the_plans_own_question(observations):
    edit = observations["prompt_edit"]
    assert edit["problem"] == "Measure the Z->bb cross section at 91 GeV."
    assert edit["dirty"] is True


def test_the_panel_shows_the_wordings_the_prompt_has_had(observations):
    """Traceability: what we asked before, from `plan.history/`."""
    assert observations["prompt_panel"]["history"] == ["Measure something with b jets."]


def test_the_prompt_can_be_dragged_and_remembers_where(observations):
    drag = observations["prompt_drag"]
    assert drag["position"] == {"x": 240, "y": 220}
    assert drag["metadata"] == {"x": 240, "y": 220}


def test_selecting_a_node_replaces_the_prompt_with_its_editor(observations):
    assert observations["side_heading_while_selecting"].startswith("Node · ")


# ---------------------------------------------------------------- new nodes


def test_a_new_node_is_usable_immediately(observations):
    """A node with no reviewers or prompt would be a trap, not a starting point."""
    added = observations["added"]
    assert added["id"] == "node1"
    assert added["artifact"] == "NODE1.md"
    assert added["reviewers"] == ["critical"]
    assert added["has_prompt"] is True
    assert observations["side_heading"] == "Node · node1"


def test_a_new_node_lands_on_empty_canvas(observations):
    """It used to be dropped on the origin, hidden under the first node."""
    added = observations["added"]
    assert added["overlaps_an_existing_node"] is False
    assert added["position"] != {"x": 40, "y": 40}


# ------------------------------------------------------------- auto-layout


def test_auto_layout_sends_the_plan_on_screen(observations):
    """Including nodes added since the last save — which is the whole point.

    The old button only dropped the stored coordinates and fell back to the
    layout that came with the view, so a node the server had never seen had no
    entry there and stacked up on the origin. Nothing moved until a save sent
    the plan through the same layering the button now calls directly.
    """
    relayout = observations["relayout"]
    assert (relayout["method"], relayout["url"]) == ("POST", "/api/plan/zbb/layout")
    assert relayout["carries_the_unsaved_node"] is True


def test_auto_layout_applies_the_grid_the_server_returned(observations):
    """The stub answers with a grid the stale layout could not have produced."""
    relayout = observations["relayout"]
    assert relayout["position"] == {"x": 40 + 3 * 300, "y": 40 + 92}
    assert relayout["every_node_placed"] is True
    assert relayout["overlaps"] is False


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


def test_approving_saved_work_starts_the_run(observations):
    """Approval with nobody waiting on the latch has to start the run itself.

    `jfc run --review-plan` leaves an orchestrator blocked on the gate and the
    response says so; a plan page opened on its own leaves nothing waiting, and
    approving used to be a no-op in that case.
    """
    assert observations["approve_flow_clean"] == [
        "POST /api/plan/zbb/approve",
        "POST /api/plan/zbb/run",
    ]


def test_approving_unsaved_work_saves_it_first(observations):
    """Otherwise the run would start from the plan on disk, not the one on screen."""
    assert observations["dirty_before_approve"] is True
    assert observations["approve_flow_dirty"] == [
        "PUT /api/plan/zbb",
        "POST /api/plan/zbb/approve",
        "POST /api/plan/zbb/run",
    ]


# ------------------------------------------------------------------ running


def test_a_running_analysis_paints_the_plan_it_runs(observations):
    run = observations["run"]
    assert run["panel_hidden"] is False
    assert run["state"] == "running"
    assert "1/" in run["detail"]  # one node complete
    assert run["log_rows"] == 2
    assert "run-done" in run["done_node_class"]
    assert "run-running" in run["running_node_class"]


def test_a_run_in_flight_blocks_a_second_launch(observations):
    assert observations["run"]["approve_disabled_while_running"] is True


def test_a_blocked_run_asks_for_approval_on_the_page(observations):
    """The terminal prompt has no reader here; the dialog is the only way to answer."""
    ask = observations["ask"]
    assert ask["hidden"] is False
    assert ask["cmd"] == "root -l -q fit.C"
    assert ask["cmd_hidden"] is False
    assert ask["input_hidden"] is True  # an approval is two buttons, not free text
    assert [label.split(" ")[-1] for label in ask["actions"]] == ["Approve", "Reject"]


def test_answering_names_the_prompt_it_answers(observations):
    """A stale answer must not approve whatever the run has moved on to."""
    answer = observations["ask_answer"]
    assert answer["calls"] == ["POST /api/plan/zbb/run/answer"]
    assert answer["body"]["id"] == "1"
    assert answer["body"]["approved"] is True
    assert answer["hidden_after"] is True


# ------------------------------------------------------------------ conditions


def test_add_condition_creates_a_routing_node_with_a_budget(observations):
    added = observations["condition_added"]
    assert added["kind"] == "condition"
    assert added["budget"] >= 1
    assert added["on_exhaustion"] in ("true", "false", "escalate")
    # A condition is evaluated, not executed and reviewed.
    assert added["reviewers"] == []


def test_a_condition_is_drawn_as_a_diamond(observations):
    render = observations["condition_render"]
    assert "condition" in render["classes"]
    assert render["shapes"][0] == "polygon"


def test_the_page_classifies_the_back_branch_the_way_the_server_does(observations):
    """A loop the user cannot see is a loop they cannot fix."""
    classified = observations["classified"]
    assert classified["total_branches"] == 2
    assert classified["back"] == ["check1->exploration"]


def test_branch_edges_are_drawn_distinctly_and_labelled(observations):
    render = observations["edge_render"]
    assert render["branches"] == 2
    assert render["loops"] == 1  # only the back branch is dashed
    assert sorted(render["labels"]) == ["no", "yes"]


def test_the_condition_panel_exposes_the_break_condition(observations):
    panel = " ".join(observations["condition_side"])
    assert "Max iterations" in panel
    assert "When the budget runs out" in panel
    assert "Question" in panel


def test_selecting_a_loop_edge_says_that_it_loops(observations):
    side = observations["loop_edge_side"]
    assert "Loops" in side
    assert "rewinds" in side


# ----------------------------------------------------------------- node kind


def test_the_panel_exposes_every_node_kind(observations):
    kind = observations["kind"]
    assert kind["options"] == ["work", "gate", "condition"]
    assert kind["initial"] == "work"


def test_switching_a_node_to_condition_gives_it_a_condition(observations):
    """P10 refuses a condition node with nothing to evaluate, so the switch seeds one."""
    after = observations["kind"]["after_switch"]
    assert after["kind"] == "condition"
    assert after["has_condition"] and after["budget"] == 3
    assert after["heading"].startswith("Condition")
    assert after["shape"] == "polygon"  # redrawn as a diamond
    assert after["dirty"]


def test_switching_a_node_away_from_condition_drops_the_condition(observations):
    """The mirror of P10: a condition on a node that is not one would never run."""
    after = observations["kind"]["after_switch_back"]
    assert after["kind"] == "gate"
    assert after["condition"] is None
    assert after["shape"] == "rect"
    assert after["heading"].startswith("Node")


# --------------------------------------------------------------- capabilities


def test_capabilities_sit_alongside_gates_and_reviewers(observations):
    assert observations["capabilities"]["labels_after_restrict"] == [
        "Reviewers",
        "Gates",
        "Function tools",
        "Skills",
        "MCP servers",
    ]


def test_the_tool_allowlist_is_off_until_the_node_restricts_it(observations):
    """`null` means the default set; the picker only appears once a list is meant."""
    caps = observations["capabilities"]
    assert caps["tools_before"] is None
    assert caps["picker_before"] is False
    assert caps["has_restrict_toggle"]
    assert caps["tools_after_restrict"] == []
    assert caps["tools_after_unrestrict"] is None


def test_the_pickers_offer_the_served_catalog(observations):
    caps = observations["capabilities"]
    assert caps["tool_options"] == ["graph_query", "read_phase_artifact", "run_pixi_task"]
    assert caps["skill_options"] == ["nyx", "widgets"]
    assert caps["mcp_options"] == ["lep-corpus"]
    # Reviewers come from the same catalog rather than a list baked into the page.
    assert "rendering" in caps["reviewer_options"]


def test_picking_a_capability_adds_it_and_removes_it_from_the_dropdown(observations):
    caps = observations["capabilities"]
    assert caps["tools_after_pick"] == ["graph_query"]
    assert caps["tags_after_pick"] == ["graph_query"]
    assert "graph_query" not in caps["options_after_pick"]
    assert caps["tools_after_remove"] == []
    assert caps["skills_after_pick"] == ["nyx"]


def test_a_new_node_starts_with_no_skills_or_mcp_servers(observations):
    assert observations["capabilities"]["mcp_before"] == []


# ---------------------------------------------------------- platform & model


def test_the_platform_dropdown_offers_the_served_platforms(observations):
    """Including the empty choice, which is how a node inherits the run's model."""
    model = observations["model"]
    assert model["before"] is None
    assert model["platform_options"] == ["", "cborg", "openai"]


def test_models_are_listed_only_once_a_platform_is_picked(observations):
    """The plan view must not pay for a provider round-trip nobody asked for."""
    model = observations["model"]
    assert model["no_request_before_a_platform_is_picked"]
    assert model["model_requests"] == ["/api/platforms/cborg/models"]


def test_picking_a_platform_alone_means_its_default_model(observations):
    """`"cborg:"` is the spec the runtime reads as "that platform, its default"."""
    assert observations["model"]["after_platform"] == "cborg:"


def test_the_model_dropdown_offers_what_the_platform_serves(observations):
    model = observations["model"]
    assert model["model_options"] == ["", "big-model", "small-model"]
    assert "default-model" in model["default_option_text"]
    assert model["after_model"] == "cborg:big-model"


def test_switching_platform_drops_the_model_it_was_listed_from(observations):
    assert observations["model"]["after_switch"] == "openai:"


def test_clearing_the_platform_returns_the_node_to_the_runs_model(observations):
    model = observations["model"]
    assert model["after_clear"] is None
    # With no platform there is nothing to list, so the field is free text.
    assert model["free_text_when_no_platform"]


# ------------------------------------------------------------- progress panel


def test_the_progress_strip_shows_every_plan_node(observations):
    """The strip is the analysis at a glance: one pill per node, always present."""
    steps = observations["progress"]["steps"]
    assert [s["label"] for s in steps] == ["Strategy", "Selection"]


def test_a_written_artifact_marks_its_step_produced(observations):
    """'Produced' is a claim about the filesystem, which is what the server sends."""
    steps = observations["progress"]["steps"]
    assert "produced" in steps[0]["classes"]
    assert "produced" not in steps[1]["classes"]


def test_the_backgrounds_are_grouped_by_how_they_evade_the_selection(observations):
    """The three categories are the panel's whole reason for existing."""
    groups = observations["progress"]["groups"]
    assert [g["title"] for g in groups] == [
        "Signal",
        "Irreducible background",
        "Reducible background",
        "Instrumental background",
        "Observed data",
    ]
    by_title = {g["title"]: g for g in groups}
    assert by_title["Irreducible background"]["rows"] == ["Z/gamma* -> ll"]
    assert by_title["Instrumental background"]["rows"] == ["W+jets"]


def test_a_category_with_nothing_in_it_still_shows(observations):
    """ "No reducible background" is a claim the strategy made, not an absence."""
    groups = {g["title"]: g for g in observations["progress"]["groups"]}
    assert groups["Reducible background"]["count"] == "0"
    assert groups["Reducible background"]["rows"] == ["none identified"]


def test_the_dock_opens_on_progress_and_switches_to_the_log(observations):
    progress = observations["progress"]
    assert progress["log_hidden_by_default"] is True
    assert progress["log_shown_after_click"] is True
    assert progress["progress_hidden_after_click"] is True


def test_the_dock_can_be_resized_by_dragging_its_grip(observations):
    """240px default, dragged 120px upward — both tab bodies follow the same var."""
    dock = observations["dock"]
    assert dock["resizing_class"] is True
    assert dock["height_while_dragging"] == "360px"
    assert dock["resizing_class_after_release"] is False


def test_double_clicking_the_grip_restores_the_default_height(observations):
    assert observations["dock"]["height_after_reset"] == "240px"


# ------------------------------------------------------------------- the run log


def test_every_narrated_step_becomes_its_own_row(observations):
    """The log is the main stage, so it shows what the terminal shows."""
    log = observations["run_log"]
    assert log["rows"] == 4
    assert log["classes"] == [
        "e info k-progress",
        "e info k-tool",
        "e warning k-result",
        "e info k-message",
    ]


def test_a_step_with_a_body_folds_it_behind_the_line_that_names_it(observations):
    """A command's output belongs one click away, not on the server's terminal."""
    log = observations["run_log"]
    assert log["tags"] == ["div", "details", "details", "details"]
    assert log["tool_summary"] == ["selection", "Phase Executor", "root -l -q fit.C"]
    assert "fit the mass peak" in log["tool_detail"]


def test_a_failed_command_reads_as_a_warning(observations):
    assert observations["run_log"]["warning_class"] == "e warning k-result"


def test_narration_alone_does_not_re_read_the_analysis(observations):
    """Only a node boundary changes what the Progress tab has to say."""
    log = observations["run_log"]
    assert log["state_calls_after_narration"] == 0
    assert log["state_calls_after_boundary"] == 1


def test_the_filter_hides_rows_without_dropping_them(observations):
    """A class on the container, so a reader's scroll position survives it."""
    log = observations["run_log"]
    assert log["filter_visible"] is True
    assert log["filter_class"] is True
    # Nothing is removed: filtering is CSS, and re-rendering thousands of rows
    # on a dropdown change would throw away where the reader was.
    assert log["rows_after_filter"] == log["rows_before_filter"]
    assert log["filter_class_after_all"] is False


def test_a_selected_node_leads_with_run_delete_and_save(observations):
    """Three buttons at the top of the panel, in that order.

    At the bottom of a form whose last field is a screenful of executor prompt,
    they are buttons nobody finds.
    """
    actions = observations["node_actions"]
    assert actions["class"] == "actions-row"
    assert actions["labels"] == ["Run", "Delete", "Save"]


def test_save_is_dead_until_there_is_something_to_save(observations):
    """And it wakes up without rebuilding the form the user is typing into."""
    actions = observations["node_actions"]
    assert actions["save_disabled_when_clean"] is True
    assert actions["save_disabled_after_edit"] is False
    assert actions["same_row_after_edit"] is True


def test_running_one_node_saves_first_and_never_approves_the_plan(observations):
    """Run launches this node alone; approval is a claim about the pipeline."""
    run = observations["node_run"]
    assert run["calls"][:2] == ["PUT /api/plan/zbb", "POST /api/plan/zbb/run"]
    assert not any("approve" in call for call in run["calls"])
    assert run["body"]["only_node"] == "strategy"


def test_a_node_cannot_be_run_twice_over(observations):
    """The button greys out while a run is in flight, and the handler agrees."""
    run = observations["node_run"]
    assert run["run_disabled_while_running"] is True
    assert run["calls_while_running"] == 0


def test_delete_takes_the_node_and_the_edges_that_touched_it(observations):
    deleted = observations["node_delete"]
    assert deleted["edges_it_had"] > 0
    assert deleted["still_present"] is False
    assert deleted["edges_left"] == 0
    # Nothing is selected afterwards, so the panel falls back to the prompt.
    assert deleted["panel_heading"] == "Physics prompt"


# ------------------------------------------------------- predefined nodes


def test_the_library_is_fetched_once_and_grouped_by_pipeline(observations):
    """One request on first open, none on filtering or re-opening."""
    library = observations["library"]
    assert library["fetches"] == ["GET /api/plan/zbb/predefined"]
    assert library["open"] is True
    assert library["entries"] == [
        "group:jfc-measurement — Seven-phase measurement",
        "row:Strategy",
        "group:jfc-search — Search pipeline",
        "row:Limits",
    ]
    assert library["refetched_on_filter"] == 0
    assert library["refetched_on_reopen"] == 0
    assert library["hidden_after_close"] is True


def test_the_filter_narrows_the_library_in_the_browser(observations):
    assert observations["library"]["filtered"] == ["Limits"]


def test_picking_a_predefined_node_inserts_a_copy_of_the_whole_node(observations):
    """Prompt, reviewers and contract come with it — that is the point of the library."""
    inserted = observations["library"]["inserted"]
    assert inserted["added"] == 1
    assert inserted["prompt"] == "Choose a technique."
    assert inserted["reviewers"] == ["physics"]
    assert inserted["contract"] == {"node_types": ["commitment"], "edge_types": ["commits_to"]}


def test_an_inserted_node_never_collides_with_one_the_plan_already_has(observations):
    """The plan already runs `strategy` out of `phase1_strategy`.

    A duplicate id is refused by the validator, and a shared directory would put
    two nodes' artifacts on top of each other.
    """
    inserted = observations["library"]["inserted"]
    assert inserted["id"] == "strategy_2"
    assert inserted["directory"] == "phase1_strategy_2"


def test_inserting_places_selects_and_dirties_the_plan(observations):
    inserted = observations["library"]["inserted"]
    assert inserted["placed"] is True
    assert inserted["overlaps_an_existing_node"] is False
    assert inserted["closed_after_pick"] is True
    assert inserted["selected_heading"] == "Node · strategy_2"
    assert inserted["dirty"] is True
