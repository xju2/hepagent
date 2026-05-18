"""JFC fixer agent — addresses review findings with minimum effective changes."""

from __future__ import annotations

from pathlib import Path

from agents import Agent, Runner
from hepagent.agent_helpers import update_logbook
from hepagent.agents.bash import execute_bash_command_with_confirmation
from hepagent.agents.common import AgentContext
from hepagent.helpers import get_repo_root, read_md
from hepagent.model_providers import get_model_provider
from hepagent.tools.common import read_resource
from hepagent.tools.jfc import get_jfc_tools

_JFC_SRC = get_repo_root() / "testarea" / "jfc" / "src"


def _create_fixer_agent(
    phase: int | str,
    analysis_root: Path,
    findings: list[str],
    model_provider: str,
    model_name: str | None,
) -> Agent[AgentContext]:
    role_def = read_md(_JFC_SRC / "agents" / "fixer.md")

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
    outputs_dir = analysis_root / phase_dir / "outputs" if phase_dir else analysis_root

    findings_text = "\n".join(f"- {f}" for f in findings) if findings else "See adjudication file."

    adjudication_path = (
        analysis_root / phase_dir / "review" / "ADJUDICATION.md" if phase_dir else None
    )

    instructions = "\n\n".join(
        filter(
            None,
            [
                f"# FIXER ROLE\n\n{role_def}" if role_def else "",
                f"# FINDINGS TO ADDRESS (Category A/B)\n\n{findings_text}",
                f"# ADJUDICATION FILE\n\n`{adjudication_path}`" if adjudication_path else "",
                f"# WORKING DIRECTORY\n\n"
                f"Analysis root: `{analysis_root}`\n"
                f"Outputs directory: `{outputs_dir}`\n"
                f"Experiment log: `{analysis_root}/experiment_log.md`\n\n"
                f"**Disposition: minimum effective changes.** Address each finding precisely. "
                f"Do not rewrite surrounding code or refactor working logic. "
                f"After fixing, append a summary of what was changed to the experiment log.",
            ],
        )
    )

    return Agent[AgentContext](
        name=f"JFC Fixer (Phase {phase})",
        instructions=instructions,
        model=get_model_provider(model_provider=model_provider, model_name=model_name),
        tools=[
            execute_bash_command_with_confirmation,
            read_resource,
            update_logbook,
            *get_jfc_tools(),
        ],
    )


async def run_fixer(
    phase: int | str,
    analysis_root: Path,
    findings: list[str],
    model_provider: str = "cborg",
    model_name: str | None = None,
) -> None:
    """
    Spawn a fixer agent to address Category A/B findings in-place.

    Fixer reads the current phase artifact + findings list, produces
    corrected artifact in place. Does NOT re-run the full executor.

    Args:
        phase: Phase number or sub-phase string.
        analysis_root: Path to the analysis root directory.
        findings: List of Category A/B finding strings from the arbiter.
        model_provider: Model provider name.
        model_name: Specific model name.
    """
    agent = _create_fixer_agent(phase, analysis_root, findings, model_provider, model_name)
    context = AgentContext(agent_name="jfc_fixer", active_skill="jfc")
    findings_summary = "\n".join(f"- {f}" for f in findings[:10])
    task = (
        f"Fix the following Category A/B findings from the Phase {phase} review:\n\n"
        f"{findings_summary}\n\n"
        f"Read the adjudication file and current artifact, then make minimum effective changes."
    )
    await Runner.run(agent, task, context=context, max_turns=30)
