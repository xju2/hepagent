"""Review gate: runs a node's reviewers concurrently, then arbitrates.

Which reviewers run, whether an arbiter adjudicates, and where findings are
written all come from the `PlanNode` under review.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from agents import Runner
from hepagent.agents.common import AgentContext
from hepagent.agents.jfc.reviewers import REVIEWER_FACTORIES
from hepagent.graph.store import AnalysisGraph
from hepagent.graph.validation import REVIEW_RULES, GraphValidationReport, validate
from hepagent.helpers import read_md
from hepagent.plan.schema import AnalysisPlan, PlanNode
from hepagent.plan.store import resolve_plan


class PhaseEscalationError(Exception):
    """Raised when reviewers call for human escalation."""

    def __init__(self, phase: str, result: ReviewGateResult):
        self.phase = phase
        self.result = result
        super().__init__(f"Node {phase} review escalated to human: {result.category_a_findings}")


class PhaseRegressionError(Exception):
    """Raised when a review finding traces its root cause to an upstream node."""

    def __init__(
        self,
        detected_phase: str,
        origin_phase: str | None,
        symptom: str,
        result: ReviewGateResult,
    ):
        self.detected_phase = detected_phase
        self.origin_phase = origin_phase
        self.symptom = symptom
        self.result = result
        super().__init__(
            f"Node {detected_phase} regression: root cause in node {origin_phase}: {symptom}"
        )


@dataclass
class ReviewGateResult:
    verdict: Literal["PASS", "ITERATE", "ESCALATE", "REGRESS"]
    category_a_findings: list[str] = field(default_factory=list)
    category_b_findings: list[str] = field(default_factory=list)
    adjudication_path: Path | None = None
    regression_origin_phase: str | None = None
    regression_symptom: str = ""
    #: Every graph validation finding, blocking or advisory.
    graph_findings: list[str] = field(default_factory=list)
    #: The error-severity subset that forced the verdict.
    graph_blocking: list[str] = field(default_factory=list)


GRAPH_VALIDATION_FILENAME = "GRAPH_VALIDATION.md"


def write_graph_validation(analysis_root: Path, review_dir: Path) -> GraphValidationReport:
    """Validate the analysis graph and persist the report into the review directory.

    Reviewers have `read_file` and are told where this lives, so the graph's
    view of the analysis becomes evidence they can cite. Returns an empty report
    if the graph cannot be read — a missing graph must not block a review.

    Runs `REVIEW_RULES` rather than every rule: an unclosed commitment is due at
    the `commitments` gate, not at the review of the node that declared it.
    """
    try:
        report = validate(AnalysisGraph.load(analysis_root), rules=REVIEW_RULES)
    except Exception:  # noqa: BLE001 - graph problems must not break the gate
        return GraphValidationReport()

    try:
        (review_dir / GRAPH_VALIDATION_FILENAME).write_text(report.to_markdown(), encoding="utf-8")
    except OSError:
        pass
    return report


async def _run_single_reviewer(
    reviewer_name: str,
    node: PlanNode,
    analysis_root: Path,
    plan: AnalysisPlan,
    model_provider: str,
    model_name: str | None,
    context: AgentContext,
    max_turns: int = 20,
) -> str:
    """Run a single reviewer agent and return its output."""
    factory = REVIEWER_FACTORIES.get(reviewer_name)
    if factory is None:
        return f"Error: unknown reviewer '{reviewer_name}'"

    agent = factory(node, analysis_root, plan, model_provider, model_name)
    task_prompt = (
        f"Review the '{node.id}' artifact for this analysis. "
        f"Write your findings to the review/ directory as instructed in your system prompt."
    )
    result = await Runner.run(agent, task_prompt, context=context, max_turns=max_turns)
    return result.final_output or ""


def parse_verdict_from_adjudication(
    adjudication_path: Path,
) -> tuple[
    Literal["PASS", "ITERATE", "ESCALATE", "REGRESS"],
    list[str],
    list[str],
    str | None,
]:
    """Parse verdict and findings from ADJUDICATION.md.

    Returns (verdict, cat_a_findings, cat_b_findings, regression_origin).
    `regression_origin` is a plan node id, non-None only when the verdict is
    REGRESS. The tail of the document is matched case-insensitively and the id is
    lowered, because node ids are lowercase slugs but the arbiter's verdict line
    is often shouted.
    """
    content = read_md(adjudication_path)
    if not content:
        return "ITERATE", ["Adjudication file is empty or missing"], [], None

    content_upper = content.upper()
    tail = content_upper.split()[-20:]
    tail_text = " ".join(tail)

    # REGRESS(M) is only a verdict when it appears in the tail of the document,
    # not when mentioned in prose (e.g. "no trigger would require REGRESS(M)").
    regress_match = re.search(r"\bREGRESS\(([^)]+)\)", tail_text, re.IGNORECASE)
    if regress_match:
        origin_phase = regress_match.group(1).strip().lower()
        cat_a: list[str] = re.findall(r"\|\s*A\s*\|[^|]*\|([^|]+)\|", content)
        cat_a = [f.strip() for f in cat_a if f.strip()]
        cat_b: list[str] = re.findall(r"\|\s*B\s*\|[^|]*\|([^|]+)\|", content)
        cat_b = [f.strip() for f in cat_b if f.strip()]
        return "REGRESS", cat_a, cat_b, origin_phase

    if "ESCALATE" in tail or content_upper.rstrip().endswith("ESCALATE"):
        verdict: Literal["PASS", "ITERATE", "ESCALATE", "REGRESS"] = "ESCALATE"
    elif "PASS" in tail or content_upper.rstrip().endswith("PASS"):
        verdict = "PASS"
    else:
        verdict = "ITERATE"

    cat_a = re.findall(r"\|\s*A\s*\|[^|]*\|([^|]+)\|", content)
    cat_a = [f.strip() for f in cat_a if f.strip()]
    cat_b = re.findall(r"\|\s*B\s*\|[^|]*\|([^|]+)\|", content)
    cat_b = [f.strip() for f in cat_b if f.strip()]

    return verdict, cat_a, cat_b, None


def reviewers_for(node: PlanNode) -> list[str]:
    """The reviewer panel a node gets.

    A node that names no reviewers still gets the critical reviewer: an authored
    plan may omit the field, and nothing should pass entirely unexamined.
    """
    return list(node.reviewers) or ["critical"]


async def run_review_gate(
    node: PlanNode,
    analysis_root: Path,
    plan: AnalysisPlan | None = None,
    model_provider: str = "cborg",
    model_name: str | None = None,
    max_turns: int = 20,
) -> ReviewGateResult:
    """
    Run a node's reviewers concurrently, then its arbiter if it declares one.

    Returns ReviewGateResult with verdict PASS/ITERATE/ESCALATE.
    Raises PhaseEscalationError if verdict is ESCALATE, PhaseRegressionError if
    it is REGRESS.

    Args:
        node: The plan node under review. Its `reviewers` and `arbiter` fields
            decide who runs; a node declaring no reviewers gets the critical
            reviewer, so nothing passes entirely unexamined.
        analysis_root: Path to the analysis root directory.
        plan: The analysis plan. Read from `plan.json` when omitted.
        model_provider: Model provider for all reviewer agents.
        model_name: Specific model name.
    """
    from hepagent.agents.jfc.reviewers import create_arbiter

    plan = resolve_plan(analysis_root, plan)
    phase = node.id
    reviewer_names = reviewers_for(node)
    context = AgentContext(agent_name="jfc_reviewer", active_skill="jfc")

    review_dir = analysis_root / node.directory / "review"
    review_dir.mkdir(parents=True, exist_ok=True)

    # Validate the graph before reviewers run, so its findings are on disk for
    # them to read and for the arbiter to weigh.
    graph_report = write_graph_validation(analysis_root, review_dir)

    # Run all reviewers concurrently
    tasks = [
        _run_single_reviewer(
            name, node, analysis_root, plan, model_provider, model_name, context, max_turns
        )
        for name in reviewer_names
    ]
    await asyncio.gather(*tasks, return_exceptions=True)

    # If this node uses an arbiter, run it after the reviewers complete
    adjudication_path = review_dir / "ADJUDICATION.md"
    regression_origin: str | None = None

    if node.arbiter:
        arbiter_agent = create_arbiter(node, analysis_root, plan, model_provider, model_name)
        arbiter_context = AgentContext(agent_name="jfc_arbiter", active_skill="jfc")
        arbiter_result = await Runner.run(
            arbiter_agent,
            f"Adjudicate the '{node.id}' review. Write ADJUDICATION.md to {review_dir}/.",
            context=arbiter_context,
            max_turns=max_turns,
        )
        # Parse from the written file
        if adjudication_path.exists():
            verdict, cat_a, cat_b, regression_origin = parse_verdict_from_adjudication(
                adjudication_path
            )
        else:
            # Fall back to parsing arbiter output
            output = arbiter_result.final_output or ""
            output_upper = output.upper()
            regress_match = re.search(r"\bREGRESS\(([^)]+)\)", output, re.IGNORECASE)
            if regress_match:
                verdict = "REGRESS"
                regression_origin = regress_match.group(1).strip().lower()
            elif "ESCALATE" in output_upper:
                verdict = "ESCALATE"
            elif "PASS" in output_upper and "ITERATE" not in output_upper:
                verdict = "PASS"
            else:
                verdict = "ITERATE"
            cat_a, cat_b = [], []
    else:
        # No arbiter: parse verdict from reviewer outputs
        review_files = sorted(review_dir.glob("*.md"))
        verdict = "PASS"
        cat_a, cat_b = [], []
        for rf in review_files:
            content = read_md(rf)
            content_upper = content.upper()
            regress_match = re.search(r"\bREGRESS\(([^)]+)\)", content, re.IGNORECASE)
            if regress_match:
                verdict = "REGRESS"
                regression_origin = regress_match.group(1).strip().lower()
                break
            if "ESCALATE" in content_upper:
                verdict = "ESCALATE"
                break
            if "ITERATE" in content_upper or "CATEGORY A" in content_upper:
                verdict = "ITERATE"

    # A node cannot pass while the graph is provably broken, whatever the
    # reviewers concluded: a dangling edge, an unclosed commitment or a note
    # citing a figure that was never produced is a fact, not a judgement call.
    # Warnings were given to the arbiter above and are left for it to weigh.
    blocking = graph_report.blocking
    if blocking and verdict == "PASS":
        verdict = "ITERATE"
    if blocking:
        cat_a = cat_a + [f"[graph] {f.message}" for f in blocking]

    symptom = "; ".join(cat_a[:3]) if cat_a else "regression detected by reviewer"
    result = ReviewGateResult(
        verdict=verdict,
        category_a_findings=cat_a,
        category_b_findings=cat_b,
        adjudication_path=adjudication_path if adjudication_path.exists() else None,
        regression_origin_phase=regression_origin,
        regression_symptom=symptom,
        graph_findings=[f.message for f in graph_report.findings],
        graph_blocking=[f.message for f in blocking],
    )

    if verdict == "ESCALATE":
        raise PhaseEscalationError(phase, result)

    if verdict == "REGRESS":
        raise PhaseRegressionError(phase, regression_origin, symptom, result)

    return result
