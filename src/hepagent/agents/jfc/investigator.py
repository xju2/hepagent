"""JFC investigator agent — scopes regression fixes."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from agents import Agent, Runner
from hepagent.agents.bash import execute_bash_command_with_confirmation
from hepagent.agents.common import AgentContext
from hepagent.agents.jfc._data import get_jfc_data_dir
from hepagent.helpers import read_md
from hepagent.model_providers import get_model_provider
from hepagent.tools.common import read_resource
from hepagent.tools.jfc import get_jfc_tools

_JFC_SRC = get_jfc_data_dir()


@dataclass
class RegressionTicket:
    origin_phase: int | str
    symptom: str
    fix_description: str
    affected_phases: list[int | str]
    ticket_path: Path | None = None


async def run_investigator(
    regression_phase: int | str,
    symptom: str,
    analysis_root: Path,
    model_provider: str = "cborg",
    model_name: str | None = None,
) -> RegressionTicket:
    """
    Investigate a regression finding traceable to an earlier phase.

    Returns a RegressionTicket describing which earlier phase to re-run
    and what specifically to fix.

    Args:
        regression_phase: Phase where the regression was detected.
        symptom: Description of the regression symptom.
        analysis_root: Path to the analysis root directory.
        model_provider: Model provider name.
        model_name: Specific model name.
    """
    role_def = read_md(_JFC_SRC / "agents" / "investigator.md")
    ticket_path = (
        analysis_root / f"phase{regression_phase}_regression_ticket" / "REGRESSION_TICKET.md"
    )

    instructions = "\n\n".join(
        filter(
            None,
            [
                f"# INVESTIGATOR ROLE\n\n{role_def}"
                if role_def
                else (
                    "# INVESTIGATOR ROLE\n\n"
                    "You scope regression fixes. Read all phase artifacts from the regression "
                    "trigger phase backward to find the origin. Produce REGRESSION_TICKET.md "
                    "identifying: what must change, which downstream artifacts are invalidated, "
                    "estimated rework scope."
                ),
                f"# REGRESSION DETECTED\n\n"
                f"Detected at phase: {regression_phase}\n"
                f"Symptom: {symptom}",
                f"# ANALYSIS ROOT\n\n`{analysis_root}`",
                f"# OUTPUT\n\nWrite REGRESSION_TICKET.md to: `{ticket_path.parent}/`\n\n"
                f"Include:\n"
                f"- Origin phase (where the root cause lives)\n"
                f"- What must change in that phase\n"
                f"- Which downstream artifacts are invalidated vs reusable\n"
                f"- Estimated rework scope (agent-hours per phase)",
            ],
        )
    )

    agent = Agent[AgentContext](
        name="JFC Investigator",
        instructions=instructions,
        model=get_model_provider(model_provider=model_provider, model_name=model_name),
        tools=[
            execute_bash_command_with_confirmation,
            read_resource,
            *get_jfc_tools(),
        ],
    )

    ticket_path.parent.mkdir(parents=True, exist_ok=True)
    context = AgentContext(agent_name="jfc_investigator", active_skill="jfc")
    task = (
        f"Investigate the regression detected at Phase {regression_phase}: {symptom}\n\n"
        f"Trace the root cause through the phase artifacts and produce REGRESSION_TICKET.md."
    )
    result = await Runner.run(agent, task, context=context, max_turns=20)

    return RegressionTicket(
        origin_phase=regression_phase,
        symptom=symptom,
        fix_description=result.final_output or "",
        affected_phases=[],  # parsed from ticket by orchestrator
        ticket_path=ticket_path if ticket_path.exists() else None,
    )
