"""Evaluating a condition node: which branch does the analysis take?

A condition node is the only place a plan may loop, so what happens here decides
whether an optimization terminates. Two properties are deliberate.

**The metric is tried first, and costs no model call.** A number an upstream node
wrote to JSON is a fact; asking a model to read it back would only add a way to
get it wrong. The natural-language `question` is the fallback for what a number
cannot express.

**A condition never guesses.** When neither test can be evaluated the node falls
back to the plan's declared `on_exhaustion` and says so loudly. This is control
flow, not bookkeeping: silently picking a branch would send an analysis down a
path nobody chose, which is worse than stopping.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from agents import Agent, Runner
from hepagent.agents.common import AgentContext
from hepagent.helpers import read_md
from hepagent.model_providers import get_model_provider
from hepagent.plan.schema import AnalysisPlan, ConditionMetric, PlanNode
from hepagent.tools.common import read_file

#: Directory, relative to a condition node's own directory, holding one file per
#: evaluation. Real files keep the graph's node ids content-addressed.
DECISIONS_DIRNAME = "decisions"

Branch = Literal["true", "false"]
Source = Literal["metric", "judge", "exhaustion"]


@dataclass(frozen=True)
class ConditionOutcome:
    """What a condition decided, and on what basis.

    Args:
        branch: Which branch to route along.
        rationale: Human-readable reason, written to the decision record and
            injected into the next iteration of the loop body.
        source: What settled it — the metric, the judge agent, or the iteration
            budget running out.
        metric_value: The number read, when there was one. Carried forward as the
            previous value for an `improvement_*` comparison.
        escalate: True when the budget ran out and the plan asked for a human
            rather than a branch.
    """

    branch: Branch
    rationale: str
    source: Source
    metric_value: float | None = None
    escalate: bool = False


class ConditionExhausted(Exception):
    """Raised when a loop spends its budget and the plan says `escalate`."""

    def __init__(self, node_id: str, iterations: int):
        self.node_id = node_id
        self.iterations = iterations
        super().__init__(
            f"Condition '{node_id}' did not resolve within {iterations} iteration(s) "
            f"and its plan entry asks for human intervention. "
            f"Resume with start_from_phase={node_id}."
        )


# --------------------------------------------------------------- the metric


def read_metric_value(analysis_root: Path, metric: ConditionMetric) -> float | None:
    """Read `metric.key` out of `metric.source`, or None if it is not there.

    The key is a dotted path, so a nested `{"significance": {"expected": 3.1}}`
    is reachable as ``significance.expected``. Returns None rather than raising:
    a missing number is a reason to fall back to the judge, not a crash.
    """
    path = analysis_root / metric.source
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None

    current: object = payload
    for part in metric.key.split("."):
        if isinstance(current, dict) and part in current:
            current = current[part]
        else:
            return None
    if isinstance(current, bool) or not isinstance(current, int | float):
        return None
    return float(current)


def compare_metric(
    metric: ConditionMetric, value: float, previous: float | None
) -> tuple[bool, str]:
    """Turn a number into a branch, with the sentence explaining it.

    An `improvement_*` comparison on the first pass has nothing to compare
    against. That resolves to False — *not converged yet* — rather than to an
    unevaluable condition, because a loop whose first evaluation cannot be made
    would otherwise exhaust before doing any work.
    """
    if metric.compare == "above":
        return value > metric.value, f"{metric.key} = {value:g}, threshold {metric.value:g}"
    if metric.compare == "below":
        return value < metric.value, f"{metric.key} = {value:g}, threshold {metric.value:g}"

    if previous is None:
        return False, (
            f"{metric.key} = {value:g} on the first pass; there is no previous value "
            f"to measure an improvement against yet"
        )

    delta = value - previous
    detail = (
        f"{metric.key} moved {previous:g} → {value:g} (change {delta:+g}), "
        f"threshold {metric.value:g}"
    )
    if metric.compare == "improvement_above":
        return delta > metric.value, detail
    return delta < metric.value, detail


# ---------------------------------------------------------------- the judge


def _judge_prompt(node: PlanNode, iteration: int, limit: int, history: Sequence[float]) -> str:
    condition = node.condition
    question = condition.question if condition else ""
    seen = ", ".join(f"{v:g}" for v in history) or "none yet"
    return (
        "You decide whether one condition in a physics analysis holds. Answer the\n"
        "question below from the evidence on disk — read the files you need with\n"
        "`read_file` rather than assuming.\n\n"
        f"## Question\n\n{question}\n\n"
        f"## Where you are\n\n"
        f"This is evaluation {iteration} of at most {limit}. Values recorded on "
        f"earlier passes: {seen}.\n\n"
        "## How to answer\n\n"
        "Give two or three sentences of reasoning citing what you read, then end\n"
        "your reply with a final line containing exactly `YES` or `NO`:\n"
        "- `YES` — the condition holds.\n"
        "- `NO` — it does not.\n\n"
        "Answer only from evidence. If you cannot tell, answer `NO` and say why:\n"
        "an unproven condition is not a satisfied one."
    )


def parse_judge_verdict(output: str) -> bool | None:
    """Read YES/NO from the tail of a judge's reply, or None if it said neither.

    Only the tail is inspected, the way `parse_verdict_from_adjudication` does it,
    so the word "yes" inside the reasoning cannot decide a branch.
    """
    words = (output or "").upper().replace("*", " ").replace("`", " ").split()
    for word in reversed(words[-25:]):
        cleaned = word.strip(".,:;!()[]")
        if cleaned in ("YES", "NO"):
            return cleaned == "YES"
    return None


def create_condition_judge(
    node: PlanNode,
    model_provider: str = "cborg",
    model_name: str | None = None,
) -> Agent[AgentContext]:
    """The agent that answers a condition's natural-language question.

    Read-only by construction: a condition decides where the analysis goes next
    and must not also be able to change what it is deciding about.
    """
    return Agent[AgentContext](
        name=f"jfc_condition_{node.id}",
        instructions=(
            "You are a careful physics analyst judging whether a stated condition "
            "holds, given the artifacts of an ongoing analysis. You do not change "
            "anything; you read the evidence and answer the question."
        ),
        model=get_model_provider(model_provider=model_provider, model_name=model_name),
        tools=[read_file],
    )


# ----------------------------------------------------------------- evaluate


async def evaluate_condition(
    node: PlanNode,
    analysis_root: Path,
    plan: AnalysisPlan,
    iteration: int,
    history: Sequence[float] = (),
    model_provider: str = "cborg",
    model_name: str | None = None,
    max_turns: int = 10,
) -> ConditionOutcome:
    """Decide which branch `node` routes along on this pass.

    Args:
        node: The condition node being evaluated.
        analysis_root: The analysis root directory.
        plan: The analysis plan, for the judge's upstream context.
        iteration: 1-based count of evaluations of this condition so far.
        history: Metric values from earlier passes, oldest first.
        model_provider: Provider for the judge agent, if one is needed.
        model_name: Specific model for the judge agent.
        max_turns: Turn cap for the judge agent.

    Returns:
        The branch to take and why. Never raises on an unevaluable condition —
        the caller decides what an exhausted budget means.
    """
    condition = node.condition
    if condition is None:  # P10 blocks this; belt and braces for a hand-edited plan
        return ConditionOutcome(
            branch="true",
            rationale=f"Node '{node.id}' declares no condition; taking the true branch.",
            source="exhaustion",
        )

    if condition.metric is not None:
        value = read_metric_value(analysis_root, condition.metric)
        if value is not None:
            previous = history[-1] if history else None
            holds, detail = compare_metric(condition.metric, value, previous)
            return ConditionOutcome(
                branch="true" if holds else "false",
                rationale=f"{condition.metric.source}: {detail}.",
                source="metric",
                metric_value=value,
            )

    if condition.question.strip():
        answer, reply = await _ask_judge(
            node,
            analysis_root,
            plan,
            iteration,
            condition.max_iterations,
            history,
            model_provider,
            model_name,
            max_turns,
        )
        if answer is not None:
            return ConditionOutcome(
                branch="true" if answer else "false",
                rationale=reply,
                source="judge",
            )

    missing = (
        f"metric source '{condition.metric.source}' could not be read"
        if condition.metric is not None
        else "no metric is declared"
    )
    return _exhausted(
        node,
        f"Condition '{node.id}' could not be evaluated ({missing}, and no usable "
        f"answer from the question).",
    )


async def _ask_judge(
    node: PlanNode,
    analysis_root: Path,
    plan: AnalysisPlan,
    iteration: int,
    limit: int,
    history: Sequence[float],
    model_provider: str,
    model_name: str | None,
    max_turns: int,
) -> tuple[bool | None, str]:
    """Run the judge agent, returning its verdict and its reply."""
    from hepagent.agents.jfc.executor import _read_upstream_artifacts

    agent = create_condition_judge(node, model_provider, model_name)
    context = AgentContext(agent_name="jfc_condition", active_skill="jfc")
    task = "\n\n".join(
        filter(
            None,
            [
                _judge_prompt(node, iteration, limit, history),
                f"The analysis lives at {analysis_root}.",
                _read_upstream_artifacts(plan, node, analysis_root),
            ],
        )
    )
    try:
        result = await Runner.run(agent, task, context=context, max_turns=max_turns)
    except Exception as exc:  # noqa: BLE001 - an unreachable model is an unevaluable condition
        return None, f"The condition judge could not be run: {exc}"

    reply = result.final_output or ""
    return parse_judge_verdict(reply), reply.strip()


def exhaust(node: PlanNode) -> ConditionOutcome:
    """The outcome for a condition whose iteration budget has run out."""
    condition = node.condition
    limit = condition.max_iterations if condition else 0
    return _exhausted(
        node,
        f"Condition '{node.id}' did not resolve within its budget of {limit} iteration(s).",
    )


def _exhausted(node: PlanNode, reason: str) -> ConditionOutcome:
    """Apply the node's `on_exhaustion` policy to a condition that did not resolve."""
    action = node.condition.on_exhaustion if node.condition else "true"
    if action == "escalate":
        return ConditionOutcome(
            branch="true", rationale=f"{reason} Escalating.", source="exhaustion", escalate=True
        )
    return ConditionOutcome(
        branch="true" if action == "true" else "false",
        rationale=f"{reason} Taking the {action} branch, as the plan declares.",
        source="exhaustion",
    )


# ------------------------------------------------------------------ records


def record_evaluation(
    analysis_root: Path,
    node: PlanNode,
    iteration: int,
    outcome: ConditionOutcome,
    target: str | None,
) -> Path:
    """Write this evaluation to disk and refresh the node's running log.

    Two files, for two readers. The per-iteration record under `decisions/` is
    what the graph ingests — one file, one `decision` node, one content-addressed
    id, so re-ingesting an unchanged analysis appends nothing. The node's primary
    artifact is the running log a human (and the next pass of the loop body)
    reads.

    Returns:
        The per-iteration record's path.
    """
    decisions = analysis_root / node.directory / DECISIONS_DIRNAME
    decisions.mkdir(parents=True, exist_ok=True)
    record = decisions / f"iteration_{iteration:02d}.md"
    record.write_text(_render_evaluation(node, iteration, outcome, target), encoding="utf-8")

    log = analysis_root / node.artifact_path
    log.parent.mkdir(parents=True, exist_ok=True)
    entries = sorted(decisions.glob("iteration_*.md"))
    log.write_text(
        f"# {node.label}\n\n"
        f"Evaluations of this condition, most recent last.\n\n"
        + "\n\n---\n\n".join(read_md(entry) for entry in entries)
        + "\n",
        encoding="utf-8",
    )
    return record


def _render_evaluation(
    node: PlanNode, iteration: int, outcome: ConditionOutcome, target: str | None
) -> str:
    limit = node.condition.max_iterations if node.condition else 0
    lines = [
        f"## Evaluation {iteration} of at most {limit}",
        "",
        f"- **Result**: {outcome.branch}",
        f"- **Decided by**: {outcome.source}",
        f"- **Routes to**: {target or '(nowhere — no branch declared for this outcome)'}",
    ]
    if outcome.metric_value is not None:
        lines.append(f"- **Metric value**: {outcome.metric_value:g}")
    lines += ["", outcome.rationale.strip() or "(no rationale recorded)"]
    return "\n".join(lines) + "\n"
