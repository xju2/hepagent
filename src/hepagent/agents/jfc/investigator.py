"""JFC investigator agent — scopes regression fixes."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from agents import Agent, Runner, WebSearchTool
from hepagent.agents.bash import execute_bash_command_with_confirmation
from hepagent.agents.common import AgentContext
from hepagent.agents.jfc._data import get_jfc_data_dir
from hepagent.helpers import read_md
from hepagent.model_providers import get_model_provider
from hepagent.tools.common import read_file, write_review
from hepagent.tools.jfc import get_jfc_tools

_JFC_SRC = get_jfc_data_dir()


@dataclass
class RegressionTicket:
    detected_phase: int | str
    origin_phase: int | str
    symptom: str
    fix_description: str = ""
    affected_phases: list[int | str] = field(default_factory=list)
    ticket_path: Path | None = None


def _parse_origin_phase(raw: str) -> int | str:
    try:
        return int(raw)
    except ValueError:
        return raw.lower()


def _parse_regression_ticket(ticket_path: Path) -> tuple[int | str | None, list[int | str]]:
    """Extract origin_phase and affected_phases from a written REGRESSION_TICKET.md.

    Expected sections in the ticket:
        ## Origin Phase
        3

        ## Affected Downstream Phases
        3, 4a, 4b
    """
    content = ticket_path.read_text(encoding="utf-8")

    origin_phase: int | str | None = None
    origin_match = re.search(r"##\s*Origin Phase\s*\n+([^\n#]+)", content)
    if origin_match:
        origin_phase = _parse_origin_phase(origin_match.group(1).strip())

    affected_phases: list[int | str] = []
    affected_match = re.search(r"##\s*Affected Downstream Phases\s*\n+([^\n#]+)", content)
    if affected_match:
        for part in affected_match.group(1).split(","):
            part = part.strip()
            if part:
                affected_phases.append(_parse_origin_phase(part))

    return origin_phase, affected_phases


async def run_investigator(
    detected_phase: int | str,
    origin_phase: int | str,
    symptom: str,
    analysis_root: Path,
    model_provider: str = "cborg",
    model_name: str | None = None,
) -> RegressionTicket:
    """
    Investigate a regression finding and produce a REGRESSION_TICKET.md.

    The ticket identifies the exact root cause, what must change in the origin
    phase, and which downstream phases are invalidated vs reusable.

    Args:
        detected_phase: Phase where the regression was detected (N).
        origin_phase: Earlier phase where the root cause is suspected (M).
        symptom: Description of the regression symptom from the reviewer.
        analysis_root: Path to the analysis root directory.
        model_provider: Model provider name.
        model_name: Specific model name.
    """
    role_def = read_md(_JFC_SRC / "agents" / "investigator.md")
    ticket_path = analysis_root / "regressions" / f"phase{detected_phase}" / "REGRESSION_TICKET.md"

    instructions = "\n\n".join(
        filter(
            None,
            [
                f"# INVESTIGATOR ROLE\n\n{role_def}"
                if role_def
                else (
                    "# INVESTIGATOR ROLE\n\n"
                    "You scope regression fixes. Read all phase artifacts from the detected phase "
                    "backward to the suspected origin phase to confirm the root cause. "
                    "Produce REGRESSION_TICKET.md identifying what must change, which downstream "
                    "artifacts are invalidated, and estimated rework scope."
                ),
                f"# REGRESSION DETECTED\n\n"
                f"Detected at phase: {detected_phase}\n"
                f"Suspected origin phase: {origin_phase}\n"
                f"Symptom: {symptom}",
                f"# ANALYSIS ROOT\n\n`{analysis_root}`",
                f"# OUTPUT\n\nWrite REGRESSION_TICKET.md to: `{ticket_path}`\n\n"
                f"The ticket MUST contain these sections (used by the orchestrator):\n\n"
                f"## Origin Phase\n"
                f"<single phase identifier, e.g. 3 or 4a>\n\n"
                f"## Affected Downstream Phases\n"
                f"<comma-separated list of phases to re-run in order, e.g. 3, 4a, 4b>\n\n"
                f"## Root Cause\n"
                f"<what exactly is wrong and where>\n\n"
                f"## Required Fix\n"
                f"<what the executor must change when Phase M is re-run>\n\n"
                f"## Reusable Artifacts\n"
                f"<which phase outputs are unaffected and can be skipped>\n\n"
                f"## Estimated Rework Scope\n"
                f"<agent-hours per phase>",
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
            WebSearchTool(),
            *get_jfc_tools(),
        ],
    )

    ticket_path.parent.mkdir(parents=True, exist_ok=True)
    context = AgentContext(agent_name="jfc_investigator", active_skill="jfc")
    task = (
        f"Investigate the regression detected at Phase {detected_phase}: {symptom}\n\n"
        f"Suspected root cause is in Phase {origin_phase}. "
        f"Trace through the phase artifacts to confirm the origin and identify all affected "
        f"downstream phases. Write REGRESSION_TICKET.md to {ticket_path}."
    )
    result = await Runner.run(agent, task, context=context, max_turns=20)

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
