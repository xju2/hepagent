"""JFC fixer agent — addresses review findings with minimum effective changes."""

from __future__ import annotations

from pathlib import Path

from agents import Agent, Runner
from hepagent.agent_helpers import update_logbook
from hepagent.agents.bash import execute_bash_command_with_confirmation
from hepagent.agents.common import AgentContext
from hepagent.agents.jfc._data import get_jfc_data_dir
from hepagent.helpers import read_md
from hepagent.model_providers import get_model_provider
from hepagent.plan.schema import PlanNode
from hepagent.tools.common import read_resource, web_search
from hepagent.tools.jfc import get_jfc_tools

_JFC_SRC = get_jfc_data_dir()


def _create_fixer_agent(
    node: PlanNode,
    analysis_root: Path,
    findings: list[str],
    model_provider: str,
    model_name: str | None,
) -> Agent[AgentContext]:
    role_def = read_md(_JFC_SRC / "agents" / "fixer.md")

    outputs_dir = analysis_root / node.outputs_dir
    adjudication_path = analysis_root / node.directory / "review" / "ADJUDICATION.md"
    findings_text = "\n".join(f"- {f}" for f in findings) if findings else "See adjudication file."

    instructions = "\n\n".join(
        filter(
            None,
            [
                f"# FIXER ROLE\n\n{role_def}" if role_def else "",
                f"# FINDINGS TO ADDRESS (Category A/B)\n\n{findings_text}",
                f"# ADJUDICATION FILE\n\n`{adjudication_path}`",
                f"# WORKING DIRECTORY\n\n"
                f"Node: `{node.id}` — {node.label}\n"
                f"Analysis root: `{analysis_root}`\n"
                f"Outputs directory: `{outputs_dir}`\n"
                f"Primary artifact: `{analysis_root / node.artifact_path}`\n"
                f"Experiment log: `{analysis_root}/experiment_log.md`\n\n"
                f"**Disposition: minimum effective changes.** Address each finding precisely. "
                f"Do not rewrite surrounding code or refactor working logic. "
                f"After fixing, append a summary of what was changed to the experiment log.",
            ],
        )
    )

    return Agent[AgentContext](
        name=f"JFC Fixer ({node.label})",
        instructions=instructions,
        model=get_model_provider(model_provider=model_provider, model_name=model_name),
        tools=[
            execute_bash_command_with_confirmation,
            read_resource,
            update_logbook,
            web_search,
            *get_jfc_tools(),
        ],
    )


async def run_fixer(
    node: PlanNode,
    analysis_root: Path,
    findings: list[str],
    model_provider: str = "cborg",
    model_name: str | None = None,
    max_turns: int = 30,
) -> None:
    """
    Spawn a fixer agent to address Category A/B findings in-place.

    The fixer reads the node's current artifact and the findings list, and
    corrects the artifact in place. It does NOT re-run the full executor.

    Args:
        node: The plan node whose review raised the findings.
        analysis_root: Path to the analysis root directory.
        findings: List of Category A/B finding strings from the arbiter.
        model_provider: Model provider name.
        model_name: Specific model name.
    """
    agent = _create_fixer_agent(node, analysis_root, findings, model_provider, model_name)
    context = AgentContext(agent_name="jfc_fixer", active_skill="jfc")
    findings_summary = "\n".join(f"- {f}" for f in findings[:10])
    task = (
        f"Fix the following Category A/B findings from the '{node.id}' review:\n\n"
        f"{findings_summary}\n\n"
        f"Read the adjudication file and current artifact, then make minimum effective changes."
    )
    await Runner.run(agent, task, context=context, max_turns=max_turns)
