"""Evaluating a condition node.

The metric path is pure and runs here in full. The judge path needs a model, so
only its prompt assembly, its verdict parsing and its failure behaviour are
covered — the same line the rest of the JFC tests draw.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "plan"))

from plan_factory import make_condition, make_loop_plan  # noqa: E402

from hepagent.agents.jfc.condition import (  # noqa: E402
    ConditionOutcome,
    compare_metric,
    evaluate_condition,
    exhaust,
    parse_judge_verdict,
    read_metric_value,
    record_evaluation,
)
from hepagent.plan.schema import ConditionMetric  # noqa: E402


@pytest.fixture
def metric():
    return ConditionMetric(
        source="evaluate_dir/outputs/results/optimization.json",
        key="significance",
        compare="improvement_below",
        value=0.02,
    )


def write_results(root: Path, payload: dict, rel: str = "evaluate_dir/outputs/results") -> None:
    path = root / rel / "optimization.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


# ---------------------------------------------------------- reading a metric


def test_a_metric_is_read_out_of_the_results_json(tmp_path, metric):
    write_results(tmp_path, {"significance": 3.25})
    assert read_metric_value(tmp_path, metric) == 3.25


def test_a_dotted_key_reaches_a_nested_value(tmp_path):
    write_results(tmp_path, {"significance": {"expected": 4.5}})
    metric = ConditionMetric(
        source="evaluate_dir/outputs/results/optimization.json",
        key="significance.expected",
        compare="above",
    )
    assert read_metric_value(tmp_path, metric) == 4.5


def test_a_missing_file_reads_as_no_value_rather_than_raising(tmp_path, metric):
    assert read_metric_value(tmp_path, metric) is None


def test_malformed_json_reads_as_no_value(tmp_path, metric):
    path = tmp_path / "evaluate_dir/outputs/results/optimization.json"
    path.parent.mkdir(parents=True)
    path.write_text("{not json", encoding="utf-8")
    assert read_metric_value(tmp_path, metric) is None


def test_a_key_that_is_not_a_number_reads_as_no_value(tmp_path, metric):
    write_results(tmp_path, {"significance": "high"})
    assert read_metric_value(tmp_path, metric) is None


def test_a_boolean_is_not_a_metric(tmp_path, metric):
    """`True` is an int in Python, and would silently compare as 1."""
    write_results(tmp_path, {"significance": True})
    assert read_metric_value(tmp_path, metric) is None


# ------------------------------------------------------------- comparisons


@pytest.mark.parametrize(
    ("compare", "threshold", "value", "previous", "expected"),
    [
        ("above", 2.0, 3.0, None, True),
        ("above", 2.0, 1.0, None, False),
        ("below", 2.0, 1.0, None, True),
        ("below", 2.0, 3.0, None, False),
        # "the gain since last pass fell under 0.02" — the convergence test
        ("improvement_below", 0.02, 3.01, 3.0, True),
        ("improvement_below", 0.02, 3.5, 3.0, False),
        # "it is still gaining more than 0.02" — the keep-going test
        ("improvement_above", 0.02, 3.5, 3.0, True),
        ("improvement_above", 0.02, 3.01, 3.0, False),
        # A metric that got worse counts as no improvement.
        ("improvement_below", 0.02, 2.5, 3.0, True),
    ],
)
def test_each_comparison(compare, threshold, value, previous, expected):
    metric = ConditionMetric(source="r.json", key="s", compare=compare, value=threshold)
    holds, _detail = compare_metric(metric, value, previous)
    assert holds is expected


def test_an_improvement_test_on_the_first_pass_is_not_converged(metric):
    """Answering 'yes' with nothing to compare would end the loop before it ran."""
    holds, detail = compare_metric(metric, 3.0, None)
    assert holds is False
    assert "first pass" in detail


# ------------------------------------------------------------ judge parsing


@pytest.mark.parametrize(
    ("reply", "expected"),
    [
        ("The gain is 0.4%.\n\nNO", False),
        ("Converged.\n\nYES", True),
        ("...\n**YES**", True),
        ("...\n`no`", False),
        ("Improvement stalled. YES.", True),
        ("I cannot tell from the artifacts.", None),
        ("", None),
    ],
)
def test_the_verdict_is_read_from_the_tail(reply, expected):
    assert parse_judge_verdict(reply) is expected


def test_yes_in_the_reasoning_does_not_decide_the_branch():
    reply = "YES it improved on pass one, but not now. " + "filler " * 40 + "NO"
    assert parse_judge_verdict(reply) is False


# ------------------------------------------------------------- evaluation


@pytest.mark.asyncio
async def test_the_metric_settles_the_branch_without_a_model(tmp_path):
    """A model call here would only add a way to misread a number that is a fact."""
    plan = make_loop_plan()
    write_results(tmp_path, {"significance": 3.0})
    outcome = await evaluate_condition(
        plan.node("converged"), tmp_path, plan, iteration=2, history=[2.995]
    )
    assert outcome.source == "metric"
    assert outcome.branch == "true"
    assert outcome.metric_value == 3.0


@pytest.mark.asyncio
async def test_a_metric_still_improving_routes_along_the_false_branch(tmp_path):
    plan = make_loop_plan()
    write_results(tmp_path, {"significance": 3.5})
    outcome = await evaluate_condition(
        plan.node("converged"), tmp_path, plan, iteration=2, history=[3.0]
    )
    assert outcome.branch == "false"


@pytest.mark.asyncio
async def test_an_unreadable_metric_with_no_question_falls_back_to_exhaustion(tmp_path):
    plan = make_loop_plan(question="")
    outcome = await evaluate_condition(plan.node("converged"), tmp_path, plan, iteration=1)
    assert outcome.source == "exhaustion"
    assert "could not be read" in outcome.rationale


@pytest.mark.asyncio
async def test_an_unevaluable_condition_can_be_made_to_escalate(tmp_path):
    plan = make_loop_plan(question="", on_exhaustion="escalate")
    outcome = await evaluate_condition(plan.node("converged"), tmp_path, plan, iteration=1)
    assert outcome.escalate


@pytest.mark.asyncio
async def test_a_missing_metric_falls_through_to_the_judge(tmp_path, monkeypatch):
    plan = make_loop_plan(metric=None, question="Has it converged?")
    captured = {}

    async def fake_judge(*args, **kwargs):
        captured["called"] = True
        return True, "The gain fell to 0.3%.\n\nYES"

    monkeypatch.setattr("hepagent.agents.jfc.condition._ask_judge", fake_judge)
    outcome = await evaluate_condition(plan.node("converged"), tmp_path, plan, iteration=2)

    assert captured["called"]
    assert outcome.source == "judge"
    assert outcome.branch == "true"


@pytest.mark.asyncio
async def test_a_judge_that_will_not_answer_falls_back_to_exhaustion(tmp_path, monkeypatch):
    """An unproven condition must not be treated as a satisfied one."""
    plan = make_loop_plan(metric=None, question="Has it converged?")

    async def no_verdict(*args, **kwargs):
        return None, "I cannot tell."

    monkeypatch.setattr("hepagent.agents.jfc.condition._ask_judge", no_verdict)
    outcome = await evaluate_condition(plan.node("converged"), tmp_path, plan, iteration=1)
    assert outcome.source == "exhaustion"


def test_exhaustion_follows_the_plans_declared_action():
    assert exhaust(make_condition(on_exhaustion="true")).branch == "true"
    assert exhaust(make_condition(on_exhaustion="false")).branch == "false"
    assert exhaust(make_condition(on_exhaustion="escalate")).escalate


# ---------------------------------------------------------------- records


def test_an_evaluation_writes_a_per_iteration_record_and_a_running_log(tmp_path):
    node = make_condition()
    outcome = ConditionOutcome(
        branch="false", rationale="Still improving.", source="metric", metric_value=3.0
    )
    record = record_evaluation(tmp_path, node, 1, outcome, target="propose")

    assert record.name == "iteration_01.md"
    assert "Still improving." in record.read_text()
    assert "propose" in record.read_text()

    log = (tmp_path / node.artifact_path).read_text()
    assert "Evaluation 1" in log


def test_the_running_log_accumulates_every_pass_in_order(tmp_path):
    node = make_condition()
    for i, rationale in enumerate(["first pass", "second pass", "third pass"], start=1):
        record_evaluation(
            tmp_path,
            node,
            i,
            ConditionOutcome(branch="false", rationale=rationale, source="metric"),
            target="propose",
        )

    log = (tmp_path / node.artifact_path).read_text()
    assert log.index("first pass") < log.index("second pass") < log.index("third pass")


def test_re_recording_the_same_iteration_overwrites_rather_than_appends(tmp_path):
    """A re-run of one pass must not leave two records the graph would ingest twice."""
    node = make_condition()
    for rationale in ("first attempt", "corrected"):
        record_evaluation(
            tmp_path,
            node,
            1,
            ConditionOutcome(branch="true", rationale=rationale, source="metric"),
            target="inference",
        )

    decisions = sorted((tmp_path / node.directory / "decisions").glob("*.md"))
    assert [p.name for p in decisions] == ["iteration_01.md"]
    assert "corrected" in decisions[0].read_text()
