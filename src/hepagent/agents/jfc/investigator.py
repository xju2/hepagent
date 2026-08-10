"""JFC investigator agent — scopes regression fixes."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from agents import Agent, Runner
from hepagent.agents.bash import execute_bash_command_with_confirmation
from hepagent.agents.common import AgentContext
from hepagent.agents.jfc._data import get_jfc_data_dir
from hepagent.helpers import read_md
from hepagent.model_providers import get_model_provider
from hepagent.plan.schema import AnalysisPlan
from hepagent.plan.store import resolve_plan
from hepagent.tools.common import read_file, web_search, write_review
from hepagent.tools.jfc import get_jfc_tools

_JFC_SRC = get_jfc_data_dir()


@dataclass
class RegressionTicket:
    """What the investigator concluded about a regression.

    Phases are plan node ids throughout.
    """

    detected_phase: str
    origin_phase: str
    symptom: str
    fix_description: str = ""
    affected_phases: list[str] = field(default_factory=list)
    ticket_path: Path | None = None


def _parse_regression_ticket(ticket_path: Path) -> tuple[str | None, list[str]]:
    """Extract origin and affected node ids from a written REGRESSION_TICKET.md.

    Expected sections in the ticket:
        ## Origin Phase
        selection

        ## Affected Downstream Phases
        selection, inference_expected, inference_partial
    """
    content = ticket_path.read_text(encoding="utf-8")

    origin_phase: str | None = None
    origin_match = re.search(r"##\s*Origin Phase\s*\n+([^\n#]+)", content)
    if origin_match:
        origin_phase = origin_match.group(1).strip().lower() or None

    affected_phases: list[str] = []
    affected_match = re.search(r"##\s*Affected Downstream Phases\s*\n+([^\n#]+)", content)
    if affected_match:
        for part in affected_match.group(1).split(","):
            cleaned = part.strip().lower()
            if cleaned:
                affected_phases.append(cleaned)

    return origin_phase, affected_phases


async def run_investigator(
    detected_phase: str,
    origin_phase: str,
    symptom: str,
    analysis_root: Path,
    plan: AnalysisPlan | None = None,
    model_provider: str = "cborg",
    model_name: str | None = None,
    max_turns: int = 20,
) -> RegressionTicket:
    """
    Investigate a regression finding and produce a REGRESSION_TICKET.md.

    The ticket identifies the exact root cause, what must change in the origin
    node, and which downstream nodes are invalidated vs reusable.

    Args:
        detected_phase: Node id where the regression was detected.
        origin_phase: Upstream node id where the root cause is suspected.
        symptom: Description of the regression symptom from the reviewer.
        analysis_root: Path to the analysis root directory.
        plan: The analysis plan. Read from `plan.json` when omitted.
        model_provider: Model provider name.
        model_name: Specific model name.
    """
    plan = resolve_plan(analysis_root, plan)
    role_def = read_md(_JFC_SRC / "agents" / "investigator.md")
    ticket_path = analysis_root / "regressions" / str(detected_phase) / "REGRESSION_TICKET.md"
    valid_ids = ", ".join(plan.node_ids()) or "(the plan declares no nodes)"

    instructions = "\n\n".join(
        filter(
            None,
            [
                f"# INVESTIGATOR ROLE\n\n{role_def}"
                if role_def
                else (
                    "# INVESTIGATOR ROLE\n\n"
                    "You scope regression fixes. Read the artifacts from the detected node "
                    "backward to the suspected origin node to confirm the root cause. "
                    "Produce REGRESSION_TICKET.md identifying what must change, which downstream "
                    "artifacts are invalidated, and estimated rework scope."
                ),
                f"# REGRESSION DETECTED\n\n"
                f"Detected at node: {detected_phase}\n"
                f"Suspected origin node: {origin_phase}\n"
                f"Symptom: {symptom}",
                f"# ANALYSIS ROOT\n\n`{analysis_root}`",
                f"# ANALYSIS PLAN\n\nValid node ids: {valid_ids}",
                f"# OUTPUT\n\nWrite REGRESSION_TICKET.md to: `{ticket_path}`\n\n"
                f"The ticket MUST contain these sections (used by the orchestrator):\n\n"
                f"## Origin Phase\n"
                f"<a single node id from the analysis plan>\n\n"
                f"## Affected Downstream Phases\n"
                f"<comma-separated node ids to re-run, in order>\n\n"
                f"## Root Cause\n"
                f"<what exactly is wrong and where>\n\n"
                f"## Required Fix\n"
                f"<what the executor must change when node M is re-run>\n\n"
                f"## Reusable Artifacts\n"
                f"<which node outputs are unaffected and can be skipped>\n\n"
                f"## Estimated Rework Scope\n"
                f"<agent-hours per node>",
            ],
        )
    )

    agent = Agent[AgentContext](
        name="JFC Investigator",
        instructions=instructions,
        model=get_model_provider(model_provider=model_provider, model_name=model_name),
        tools=[
            execute_bash_command_with_confirmation,
            read_file,
            write_review,
            web_search,
            *get_jfc_tools(),
        ],
    )

    ticket_path.parent.mkdir(parents=True, exist_ok=True)
    context = AgentContext(agent_name="jfc_investigator", active_skill="jfc")
    task = (
        f"Investigate the regression detected at node {detected_phase}: {symptom}\n\n"
        f"Suspected root cause is in node {origin_phase}. "
        f"Trace through the artifacts to confirm the origin and identify all affected "
        f"downstream nodes. Write REGRESSION_TICKET.md to {ticket_path}."
    )
    result = await Runner.run(agent, task, context=context, max_turns=max_turns)

    # Parse the written ticket for structured data
    parsed_origin, affected_phases = (
        _parse_regression_ticket(ticket_path) if ticket_path.exists() else (origin_phase, [])
    )

    return RegressionTicket(
        detected_phase=detected_phase,
        origin_phase=parsed_origin if parsed_origin is not None else origin_phase,
        symptom=symptom,
        fix_description=result.final_output or "",
        affected_phases=affected_phases,
        ticket_path=ticket_path if ticket_path.exists() else None,
    )
