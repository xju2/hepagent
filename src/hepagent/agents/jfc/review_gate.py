"""JFC review gate: runs all phase reviewers concurrently then arbitrates."""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from agents import Runner
from hepagent.agents.common import AgentContext
from hepagent.agents.jfc.reviewers import (
    create_arbiter,
    create_bibtex_validator,
    create_constructive_reviewer,
    create_critical_reviewer,
    create_physics_reviewer,
    create_plot_validator,
    create_rendering_reviewer,
)
from hepagent.helpers import read_md


class PhaseEscalationError(Exception):
    """Raised when reviewers call for human escalation."""

    def __init__(self, phase: int | str, result: ReviewGateResult):
        self.phase = phase
        self.result = result
        super().__init__(f"Phase {phase} review escalated to human: {result.category_a_findings}")


class PhaseRegressionError(Exception):
    """Raised when a review finding traces its root cause to an earlier phase."""

    def __init__(
        self,
        detected_phase: int | str,
        origin_phase: int | str,
        symptom: str,
        result: ReviewGateResult,
    ):
        self.detected_phase = detected_phase
        self.origin_phase = origin_phase
        self.symptom = symptom
        self.result = result
        super().__init__(
            f"Phase {detected_phase} regression: root cause in Phase {origin_phase}: {symptom}"
        )


@dataclass
class ReviewGateResult:
    verdict: Literal["PASS", "ITERATE", "ESCALATE", "REGRESS"]
    category_a_findings: list[str] = field(default_factory=list)
    category_b_findings: list[str] = field(default_factory=list)
    adjudication_path: Path | None = None
    regression_origin_phase: int | str | None = None
    regression_symptom: str = ""


# Maps each phase to which reviewer factories to call
_PHASE_REVIEWERS: dict[int | str, list[str]] = {
    1: ["physics", "critical", "constructive"],
    2: ["plot"],
    3: ["critical", "plot"],
    "4a": ["physics", "critical", "constructive", "plot", "bibtex"],
    "4b": ["physics", "critical", "constructive", "plot", "bibtex"],
    "4c": ["critical", "plot"],
    5: ["physics", "critical", "constructive", "plot", "bibtex", "rendering"],
}

_ARBITER_PHASES = {1, "4a", "4b", 5}


async def _run_single_reviewer(
    reviewer_name: str,
    phase: int | str,
    analysis_root: Path,
    model_provider: str,
    model_name: str | None,
    context: AgentContext,
    max_turns: int = 20,
) -> str:
    """Run a single reviewer agent and return its output."""
    factory_map = {
        "physics": create_physics_reviewer,
        "critical": create_critical_reviewer,
        "constructive": create_constructive_reviewer,
        "plot": create_plot_validator,
        "bibtex": create_bibtex_validator,
        "rendering": lambda ar, mp, mn: create_rendering_reviewer(ar, mp, mn),
    }

    factory = factory_map.get(reviewer_name)
    if factory is None:
        return f"Error: unknown reviewer '{reviewer_name}'"

    if reviewer_name == "rendering":
        agent = create_rendering_reviewer(analysis_root, model_provider, model_name)
    else:
        agent = factory(phase, analysis_root, model_provider, model_name)

    task_prompt = (
        f"Review the Phase {phase} artifact for the JFC analysis. "
        f"Write your findings to the review/ directory as instructed in your system prompt."
    )
    result = await Runner.run(agent, task_prompt, context=context, max_turns=max_turns)
    return result.final_output or ""


def _parse_origin_phase(raw: str) -> int | str:
    try:
        return int(raw)
    except ValueError:
        return raw.lower()


def _parse_verdict_from_adjudication(
    adjudication_path: Path,
) -> tuple[
    Literal["PASS", "ITERATE", "ESCALATE", "REGRESS"],
    list[str],
    list[str],
    int | str | None,
]:
    """Parse verdict and findings from ADJUDICATION.md.

    Returns (verdict, cat_a_findings, cat_b_findings, regression_origin_phase).
    regression_origin_phase is non-None only when verdict is REGRESS.
    """
    content = read_md(adjudication_path)
    if not content:
        return "ITERATE", ["Adjudication file is empty or missing"], [], None

    # REGRESS(M) takes priority — checked before ESCALATE/PASS
    regress_match = re.search(r"\bREGRESS\(([^)]+)\)", content, re.IGNORECASE)
    if regress_match:
        origin_phase = _parse_origin_phase(regress_match.group(1).strip())
        cat_a: list[str] = re.findall(r"\|\s*A\s*\|[^|]*\|([^|]+)\|", content)
        cat_a = [f.strip() for f in cat_a if f.strip()]
        cat_b: list[str] = re.findall(r"\|\s*B\s*\|[^|]*\|([^|]+)\|", content)
        cat_b = [f.strip() for f in cat_b if f.strip()]
        return "REGRESS", cat_a, cat_b, origin_phase

    content_upper = content.upper()
    tail = content_upper.split()[-20:]
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


async def run_review_gate(
    phase: int | str,
    analysis_root: Path,
    model_provider: str = "cborg",
    model_name: str | None = None,
    max_turns: int = 20,
) -> ReviewGateResult:
    """
    Run all reviewers for the given phase concurrently, then run arbiter.

    Returns ReviewGateResult with verdict PASS/ITERATE/ESCALATE.
    Raises PhaseEscalationError if verdict is ESCALATE.

    Args:
        phase: Phase number or sub-phase string.
        analysis_root: Path to the analysis root directory.
        model_provider: Model provider for all reviewer agents.
        model_name: Specific model name.
    """
    reviewer_names = _PHASE_REVIEWERS.get(phase, ["critical"])
    context = AgentContext(agent_name="jfc_reviewer", active_skill="jfc")

    # Ensure review directory exists
    phase_dir_map = {
        1: "phase1_strategy",
        2: "phase2_exploration",
        3: "phase3_selection",
        "4a": "phase4a_inference_expected",
        "4b": "phase4b_inference_partial",
        "4c": "phase4c_inference_observed",
        5: "phase5_documentation",
    }
    phase_dir = phase_dir_map.get(phase, "")
    review_dir = analysis_root / phase_dir / "review" if phase_dir else analysis_root / "review"
    review_dir.mkdir(parents=True, exist_ok=True)

    # Run all reviewers concurrently
    tasks = [
        _run_single_reviewer(
            name, phase, analysis_root, model_provider, model_name, context, max_turns
        )
        for name in reviewer_names
    ]
    await asyncio.gather(*tasks, return_exceptions=True)

    # If this phase uses an arbiter, run it after reviewers complete
    adjudication_path = review_dir / "ADJUDICATION.md"
    regression_origin: int | str | None = None

    if phase in _ARBITER_PHASES:
        arbiter_agent = create_arbiter(phase, analysis_root, model_provider, model_name)
        arbiter_context = AgentContext(agent_name="jfc_arbiter", active_skill="jfc")
        arbiter_result = await Runner.run(
            arbiter_agent,
            f"Adjudicate the Phase {phase} review. Write ADJUDICATION.md to {review_dir}/.",
            context=arbiter_context,
            max_turns=max_turns,
        )
        # Parse from the written file
        if adjudication_path.exists():
            verdict, cat_a, cat_b, regression_origin = _parse_verdict_from_adjudication(
                adjudication_path
            )
        else:
            # Fall back to parsing arbiter output
            output = arbiter_result.final_output or ""
            output_upper = output.upper()
            regress_match = re.search(r"\bREGRESS\(([^)]+)\)", output, re.IGNORECASE)
            if regress_match:
                verdict = "REGRESS"
                regression_origin = _parse_origin_phase(regress_match.group(1).strip())
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
                regression_origin = _parse_origin_phase(regress_match.group(1).strip())
                break
            if "ESCALATE" in content_upper:
                verdict = "ESCALATE"
                break
            if "ITERATE" in content_upper or "CATEGORY A" in content_upper:
                verdict = "ITERATE"

    symptom = "; ".join(cat_a[:3]) if cat_a else "regression detected by reviewer"
    result = ReviewGateResult(
        verdict=verdict,
        category_a_findings=cat_a,
        category_b_findings=cat_b,
        adjudication_path=adjudication_path if adjudication_path.exists() else None,
        regression_origin_phase=regression_origin,
        regression_symptom=symptom,
    )

    if verdict == "ESCALATE":
        raise PhaseEscalationError(phase, result)

    if verdict == "REGRESS":
        raise PhaseRegressionError(phase, regression_origin, symptom, result)

    return result
