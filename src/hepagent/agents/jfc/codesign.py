"""JFC codesign gate: human review of the analysis strategy after Phase 1 PASS."""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path
from typing import Literal

from agents import Agent, Runner
from hepagent.agents.common import AgentContext
from hepagent.helpers import read_md
from hepagent.model_providers import get_model_provider
from hepagent.tools.common import ask_user_for_info, read_file, write_review

_STRATEGY_REL = "phase1_strategy/outputs/STRATEGY.md"
_CODESIGN_DIR_REL = "phase1_strategy/codesign"


def create_codesign_agent(
    analysis_root: Path,
    model_provider: str = "cborg",
    model_name: str | None = None,
) -> Agent[AgentContext]:
    """Return agent that writes codesign summary and facilitates human review."""
    strategy_path = analysis_root / _STRATEGY_REL
    codesign_dir = analysis_root / _CODESIGN_DIR_REL
    summary_path = codesign_dir / "CODESIGN_SUMMARY.md"
    feedback_path = codesign_dir / "HUMAN_FEEDBACK.md"

    physics_prompt = read_md(analysis_root / "prompt.md")

    instructions = (
        "# CODESIGN AGENT ROLE\n\n"
        "You are the JFC Codesign Agent. After Phase 1 (Strategy) receives a PASS verdict, "
        "your role is to bridge the technical analysis strategy and the physics team by "
        "creating a human-readable summary, facilitating an interactive review session, "
        "and recording all open questions into a human-feedback artifact.\n\n"
        "## Your Workflow\n\n"
        "### Step 1 — Read the Strategy\n"
        f"Read the full analysis strategy from: `{strategy_path}`\n\n"
        "### Step 2 — Write the Codesign Summary\n"
        f"Write a concise, human-readable summary to: `{summary_path}`\n\n"
        "The summary must include the following clearly labelled sections:\n"
        "- **Physics Insights**: the key physics questions, observables, and chosen methodology\n"
        "- **Key Technical Choices**: analysis approach, statistical methods, control regions, "
        "systematic uncertainties\n"
        "- **Assumptions and Limitations**: what the strategy assumes and where it may be "
        "limited or fragile\n"
        "- **Areas for Improvement**: potential refinements or open questions\n\n"
        "Write for a physics postDoc or Junior faculty. "
        "Maximum 600 words.\n\n"
        "### Step 3 — Present the Summary and Invite Feedback\n"
        "Use `ask_user_for_info` to display the summary to the user and invite them to:\n"
        "- Ask questions about the analysis strategy\n"
        "- Provide feedback or raise concerns\n"
        "- Type DONE when they have no more questions\n\n"
        "Your prompt should include the full summary text followed by:\n"
        '  "Please read the summary above. Ask any questions, raise concerns, or type DONE '
        'to proceed."\n\n'
        "### Step 4 — Answer Questions Using the Strategy\n"
        "For each question or piece of feedback:\n"
        "- Answer it SOLELY based on the analysis strategy (`STRATEGY.md`), "
        "NOT based on the summary\n"
        "- If a question reveals a genuine gap or concern in the strategy, mark it as OPEN\n"
        "- Continue using `ask_user_for_info` to collect more questions until the user "
        "types DONE\n\n"
        "### Step 5 — Write the Human Feedback Artifact\n"
        f"Write all questions, answers, and open items to: `{feedback_path}`\n\n"
        "Structure the feedback file as:\n"
        "```\n"
        "# Human Feedback on Analysis Strategy\n\n"
        "## Summary of Concerns\n"
        "<one-paragraph overview of key themes in the feedback>\n\n"
        "## Q&A Log\n\n"
        "### Question 1\n"
        "**User:** <verbatim question>\n"
        "**Agent:** <your answer based on STRATEGY.md>\n"
        "**Status:** RESOLVED | OPEN\n\n"
        "...\n\n"
        "## Open Items\n"
        "<bulleted list of all OPEN items that may require strategy revision>\n"
        "```\n\n"
        "If there are no open items, write: `No open items — strategy appears sound.`\n\n"
        "## Available Information\n"
        f"- Physics prompt: `{analysis_root}/prompt.md`\n"
        f"- Analysis strategy (source of truth for answers): `{strategy_path}`\n"
        f"- Codesign output directory: `{codesign_dir}/`\n"
        + (f"\n## Physics Prompt\n\n{physics_prompt}" if physics_prompt else "")
    )

    return Agent[AgentContext](
        name="JFC Codesign Agent",
        instructions=instructions,
        model=get_model_provider(model_provider=model_provider, model_name=model_name),
        tools=[read_file, write_review, ask_user_for_info],
    )


def create_codesign_arbiter(
    analysis_root: Path,
    model_provider: str = "cborg",
    model_name: str | None = None,
) -> Agent[AgentContext]:
    """Return arbiter that reviews analysis strategy plus human feedback."""
    strategy_path = analysis_root / _STRATEGY_REL
    codesign_dir = analysis_root / _CODESIGN_DIR_REL
    feedback_path = codesign_dir / "HUMAN_FEEDBACK.md"
    adjudication_path = codesign_dir / "CODESIGN_ADJUDICATION.md"

    strategy_content = read_md(strategy_path)
    feedback_content = read_md(feedback_path)

    instructions = (
        "# CODESIGN ARBITER ROLE\n\n"
        "You are the JFC Codesign Arbiter. Your role is to review the Phase 1 analysis "
        "strategy together with human physicist feedback to determine whether the strategy "
        "is sound enough to proceed to Phase 2 (Exploration).\n\n"
        "## Context\n\n"
        "The analysis strategy was produced by Phase 1 and received a PASS from the "
        "automated review gate. The codesign agent then generated a human-readable summary "
        "and conducted an interactive review with the physics team. You now adjudicate "
        "whether the human feedback reveals issues that require strategy revision before "
        "Phase 2 begins. Note that some issues may not be solvable with the existing resources.\n\n"
        "## Your Task\n\n"
        "1. Review the analysis strategy (see STRATEGY section below)\n"
        "2. Review the human feedback (see HUMAN FEEDBACK section below)\n"
        "3. Assess each open item in the feedback for impact on Phase 2 readiness\n"
        "4. Write your adjudication\n\n"
        "## Verdict Options\n\n"
        "- **PASS**: The strategy is sound and human concerns have been adequately addressed, "
        "or any remaining open items do not require strategy revision before Phase 2. "
        "Proceed to Phase 2.\n"
        "- **ITERATE**: Human feedback reveals significant gaps, misalignments, or unresolved "
        "questions that require the strategy to be revised before Phase 2 can begin.\n\n"
        "## Output\n\n"
        f"Write your adjudication to: `{adjudication_path}`\n\n"
        "Structure:\n"
        "1. **Open Concerns**: enumerate each open item from the feedback\n"
        "2. **Impact Assessment**: for each concern, assess impact on Phase 2 readiness "
        "(blocking / non-blocking)\n"
        "3. **Final Adjudication**: brief summary of your decision\n"
        "4. Final line: exactly `PASS` or `ITERATE`\n\n"
        + (f"## STRATEGY\n\n{strategy_content[:6000]}\n\n" if strategy_content else "")
        + (
            f"## HUMAN FEEDBACK\n\n{feedback_content}\n\n"
            if feedback_content
            else "## HUMAN FEEDBACK\n\nNo human feedback recorded. Treat as PASS.\n\n"
        )
    )

    return Agent[AgentContext](
        name="JFC Codesign Arbiter",
        instructions=instructions,
        model=get_model_provider(model_provider=model_provider, model_name=model_name),
        tools=[read_file, write_review],
    )


def _parse_codesign_verdict(
    adjudication_path: Path, fallback_output: str
) -> Literal["PROCEED", "REVISE"]:
    """Parse the codesign arbiter verdict. Returns PROCEED or REVISE."""
    if adjudication_path.exists():
        content = adjudication_path.read_text(encoding="utf-8")
    else:
        content = fallback_output

    tail = content.upper().split()[-20:]
    tail_text = " ".join(tail)

    if re.search(r"\bITERATE\b", tail_text):
        return "REVISE"
    return "PROCEED"


async def run_codesign_gate(
    analysis_root: Path,
    model_provider: str = "cborg",
    model_name: str | None = None,
    max_turns: int | None = None,
    progress_callback: Callable[[str, str], None] | None = None,
) -> Literal["PROCEED", "REVISE"]:
    """Run the codesign gate after Phase 1 PASS.

    Generates a human-readable strategy summary, facilitates interactive human review,
    then runs the codesign arbiter on the strategy + human feedback.

    Returns "PROCEED" to continue to Phase 2, or "REVISE" to re-run Phase 1.
    """
    codesign_dir = analysis_root / _CODESIGN_DIR_REL
    codesign_dir.mkdir(parents=True, exist_ok=True)

    # Step 1: Codesign agent — summary generation and human interaction
    if progress_callback:
        progress_callback("codesign", "generating summary and conducting human review")

    codesign_agent = create_codesign_agent(analysis_root, model_provider, model_name)
    context = AgentContext(agent_name="jfc_codesign", active_skill="jfc")
    agent_turns = max_turns if max_turns is not None else 30

    await Runner.run(
        codesign_agent,
        (
            f"Generate the codesign summary for the analysis at {analysis_root}, "
            "present it to the user, answer their questions using STRATEGY.md, "
            "and record all feedback in HUMAN_FEEDBACK.md."
        ),
        context=context,
        max_turns=agent_turns,
    )

    # Step 2: Codesign arbiter — review strategy + human feedback
    if progress_callback:
        progress_callback("codesign", "arbiter reviewing strategy and human feedback")

    arbiter = create_codesign_arbiter(analysis_root, model_provider, model_name)
    arbiter_context = AgentContext(agent_name="jfc_codesign_arbiter", active_skill="jfc")
    arbiter_turns = max_turns if max_turns is not None else 20

    arbiter_result = await Runner.run(
        arbiter,
        f"Adjudicate the codesign review for the analysis at {analysis_root}.",
        context=arbiter_context,
        max_turns=arbiter_turns,
    )

    adjudication_path = codesign_dir / "CODESIGN_ADJUDICATION.md"
    verdict = _parse_codesign_verdict(adjudication_path, arbiter_result.final_output or "")

    if progress_callback:
        label = "PROCEED to Phase 2" if verdict == "PROCEED" else "REVISE strategy (Phase 1 re-run)"
        progress_callback("codesign", f"verdict: {label}")

    return verdict
