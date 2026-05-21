"""Phase executor agent factories for JFC analyses."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from agents import Agent, WebSearchTool
from hepagent.agent_helpers import update_logbook
from hepagent.agents.bash import execute_bash_command_with_confirmation
from hepagent.agents.common import AgentContext
from hepagent.agents.jfc._data import get_jfc_data_dir
from hepagent.helpers import read_md
from hepagent.model_providers import get_model_provider
from hepagent.tools.common import ask_user_for_info, read_resource
from hepagent.tools.jfc import get_jfc_tools

_JFC_SRC = get_jfc_data_dir()

_PHASE_NAME_MAP = {
    1: ("phase1_strategy", "phase1_claude.md", "STRATEGY.md"),
    2: ("phase2_exploration", "phase2_claude.md", "EXPLORATION.md"),
    3: ("phase3_selection", "phase3_claude.md", "SELECTION.md"),
    "4a": ("phase4a_inference_expected", "phase4_claude.md", "INFERENCE_EXPECTED.md"),
    "4b": ("phase4b_inference_partial", "phase4_claude.md", "INFERENCE_PARTIAL.md"),
    "4c": ("phase4c_inference_observed", "phase4_claude.md", "INFERENCE_OBSERVED.md"),
    5: ("phase5_documentation", "phase5_claude.md", "ANALYSIS_NOTE_5_v1.md"),
}

_UPSTREAM_ARTIFACTS: dict[int | str, list[str]] = {
    1: [],
    2: ["phase1_strategy/outputs/STRATEGY.md"],
    3: ["phase1_strategy/outputs/STRATEGY.md", "phase2_exploration/outputs/EXPLORATION.md"],
    "4a": [
        "phase1_strategy/outputs/STRATEGY.md",
        "phase2_exploration/outputs/EXPLORATION.md",
        "phase3_selection/outputs/SELECTION.md",
        "COMMITMENTS.md",
    ],
    "4b": [
        "phase1_strategy/outputs/STRATEGY.md",
        "phase3_selection/outputs/SELECTION.md",
        "phase4a_inference_expected/outputs/INFERENCE_EXPECTED.md",
        "COMMITMENTS.md",
    ],
    "4c": [
        "phase3_selection/outputs/SELECTION.md",
        "phase4a_inference_expected/outputs/INFERENCE_EXPECTED.md",
        "phase4b_inference_partial/outputs/INFERENCE_PARTIAL.md",
        "COMMITMENTS.md",
    ],
    5: [
        "phase1_strategy/outputs/STRATEGY.md",
        "phase3_selection/outputs/SELECTION.md",
        "phase4c_inference_observed/outputs/INFERENCE_OBSERVED.md",
        "COMMITMENTS.md",
    ],
}


def _read_jfc_file(relative: str) -> str:
    path = _JFC_SRC / relative
    return read_md(path)


def _read_upstream_artifacts(phase: int | str, analysis_root: Path) -> str:
    paths = _UPSTREAM_ARTIFACTS.get(phase, [])
    blocks = []
    for rel in paths:
        full = analysis_root / rel
        content = read_md(full)
        if content:
            blocks.append(f"### {rel}\n\n{content}")
    if not blocks:
        return ""
    return "## PRIOR PHASE ARTIFACTS\n\n" + "\n\n---\n\n".join(blocks)


def _assemble_executor_prompt(
    phase: int | str,
    analysis_root: Path,
    phase_dir: str,
    artifact_name: str,
    template_name: str,
) -> str:
    parts = []

    # 1. Role definition
    executor_role = _read_jfc_file("agents/executor.md")
    if executor_role:
        parts.append(f"# EXECUTOR ROLE DEFINITION\n\n{executor_role}")

    # 2. Phase template
    phase_template = _read_jfc_file(f"templates/{template_name}")
    if phase_template:
        parts.append(f"# PHASE SPECIFICATION\n\n{phase_template}")

    # 3. Physics prompt
    prompt_path = analysis_root / "prompt.md"
    physics_prompt = read_md(prompt_path)
    if physics_prompt:
        parts.append(f"# PHYSICS PROMPT\n\n{physics_prompt}")

    # 4. Upstream artifacts
    upstream = _read_upstream_artifacts(phase, analysis_root)
    if upstream:
        parts.append(upstream)

    # 5. Working directory instruction
    outputs_dir = analysis_root / phase_dir / "outputs"
    src_dir = analysis_root / phase_dir / "src"
    parts.append(
        f"# WORKING DIRECTORY\n\n"
        f"Write all outputs to: `{outputs_dir}/`\n"
        f"Primary artifact: `{outputs_dir}/{artifact_name}`\n"
        f"Write analysis code to: `{src_dir}/`\n"
        f"Write figures to: `{outputs_dir}/figures/`\n"
        f"Append to experiment log: `{analysis_root}/experiment_log.md`\n"
        f"Analysis root: `{analysis_root}/`"
    )

    return "\n\n".join(parts)


def create_phase_executor(
    phase: int | str,
    analysis_root: Path,
    model_provider: str = "cborg",
    model_name: str | None = None,
) -> Agent[AgentContext]:
    """
    Return a role agent configured to execute a specific JFC phase.

    Assembles the system prompt from executor.md, the phase template,
    the physics prompt, and upstream artifacts.

    Args:
        phase: Phase number (1, 2, 3, 5) or sub-phase string ("4a", "4b", "4c").
        analysis_root: Path to the analysis root directory.
        model_provider: Model provider name (default "cborg").
        model_name: Specific model name (default: provider's default).
    """
    if phase not in _PHASE_NAME_MAP:
        raise ValueError(f"Unknown phase: {phase}. Valid: {list(_PHASE_NAME_MAP)}")

    phase_dir, template_name, artifact_name = _PHASE_NAME_MAP[phase]
    instructions = _assemble_executor_prompt(
        phase, analysis_root, phase_dir, artifact_name, template_name
    )

    return Agent[AgentContext](
        name=f"JFC Phase {phase} Executor",
        instructions=instructions,
        model=get_model_provider(model_provider=model_provider, model_name=model_name),
        tools=[
            execute_bash_command_with_confirmation,
            read_resource,
            update_logbook,
            ask_user_for_info,
            WebSearchTool(),
            *get_jfc_tools(),
        ],
    )


def create_note_writer(
    phase: Literal["4a", "4b", "4c", "5"],
    analysis_root: Path,
    model_provider: str = "cborg",
    model_name: str | None = None,
) -> Agent[AgentContext]:
    """
    Return a role agent that writes the analysis note for a given phase.

    The note writer reads all phase artifacts and produces the markdown AN.
    No bash execution tools — pure prose generation.

    Args:
        phase: Phase for which to write the AN ("4a", "4b", "4c", or "5").
        analysis_root: Path to the analysis root directory.
        model_provider: Model provider name.
        model_name: Specific model name.
    """
    role_def = _read_jfc_file("agents/note_writer.md")

    # Phase-specific AN file target
    an_targets = {
        "4a": "phase4a_inference_expected/outputs/ANALYSIS_NOTE_4a_v1.md",
        "4b": "phase4b_inference_partial/outputs/ANALYSIS_NOTE_4b_v1.md",
        "4c": "phase4c_inference_observed/outputs/ANALYSIS_NOTE_4c_v1.md",
        "5": "phase5_documentation/outputs/ANALYSIS_NOTE_5_v1.md",
    }
    an_target = an_targets.get(phase, f"ANALYSIS_NOTE_{phase}_v1.md")

    # Load all available phase artifacts
    artifact_paths = [
        "phase1_strategy/outputs/STRATEGY.md",
        "phase2_exploration/outputs/EXPLORATION.md",
        "phase3_selection/outputs/SELECTION.md",
        "phase4a_inference_expected/outputs/INFERENCE_EXPECTED.md",
        "phase4b_inference_partial/outputs/INFERENCE_PARTIAL.md",
        "phase4c_inference_observed/outputs/INFERENCE_OBSERVED.md",
        "COMMITMENTS.md",
    ]
    artifact_blocks = []
    for rel in artifact_paths:
        full = analysis_root / rel
        content = read_md(full)
        if content:
            artifact_blocks.append(f"### {rel}\n\n{content[:4000]}")

    physics_prompt = read_md(analysis_root / "prompt.md")

    parts = []
    if role_def:
        parts.append(f"# NOTE WRITER ROLE\n\n{role_def}")
    if physics_prompt:
        parts.append(f"# PHYSICS PROMPT\n\n{physics_prompt}")
    parts.append(
        f"# YOUR TASK\n\n"
        f"Write the complete analysis note for Phase {phase}.\n"
        f"Output: `{analysis_root / an_target}`\n\n"
        f"Requirements:\n"
        f"- Minimum 4 equations (observable definition, correction, systematic"
        f" evaluation, fit model)\n"
        f"- Every result with context (comparison to published values, chi2, resolving power)\n"
        f"- Validation summary table with chi2/ndf, p-value, verdict\n"
        f"- Number consistency: all values must match machine-readable outputs\n"
        f"- Figure composition annotations: `<!-- COMPOSE: NxM grid -->`"
        f" for related figure groups\n"
        f"- Figure references must match existing files in the figures/ directories"
    )
    if artifact_blocks:
        parts.append("# PHASE ARTIFACTS\n\n" + "\n\n---\n\n".join(artifact_blocks))

    instructions = "\n\n".join(parts)

    return Agent[AgentContext](
        name=f"JFC Note Writer (Phase {phase})",
        instructions=instructions,
        model=get_model_provider(model_provider=model_provider, model_name=model_name),
        tools=[read_resource],  # read-only: no bash execution
    )


def create_typesetter(
    analysis_root: Path,
    model_provider: str = "cborg",
    model_name: str | None = None,
) -> Agent[AgentContext]:
    """
    Return a role agent that compiles the analysis note to PDF.

    System prompt from typesetter.md. Has bash execution tools.
    Runs: pandoc → postprocess_tex.py → tectonic.

    Args:
        analysis_root: Path to the analysis root directory.
        model_provider: Model provider name.
        model_name: Specific model name.
    """
    role_def = _read_jfc_file("agents/typesetter.md")
    preamble_path = _JFC_SRC / "conventions" / "preamble.tex"
    postprocess_path = _JFC_SRC / "conventions" / "postprocess_tex.py"

    parts = []
    if role_def:
        parts.append(f"# TYPESETTER ROLE\n\n{role_def}")

    parts.append(
        f"# TYPESETTING WORKFLOW\n\n"
        f"Analysis root: `{analysis_root}`\n"
        f"Preamble: `{preamble_path}`\n"
        f"Postprocessor: `{postprocess_path}`\n\n"
        f"Pipeline for each AN markdown file:\n"
        f"1. `pandoc <AN.md> -o <AN.tex> --standalone --include-in-header {preamble_path} "
        f"--number-sections --toc`\n"
        f"2. `python {postprocess_path} <AN.tex>` (deterministic fixes)\n"
        f"3. Typesetter does judgment-requiring work (figure grouping, longtable conversion)\n"
        f"4. `tectonic <AN.tex> --outdir <output_dir>` → compiled PDF\n"
        f"5. Read compiled PDF, fix issues, recompile (max 3 iterations)\n\n"
        f"Rules:\n"
        f"- Combine related figures into side-by-side layouts (see COMPOSE annotations)\n"
        f"- Never modify physics content — layout and formatting only\n"
        f"- If a physics issue is found, flag it for the note writer, do not fix it"
    )

    instructions = "\n\n".join(parts)

    return Agent[AgentContext](
        name="JFC Typesetter",
        instructions=instructions,
        model=get_model_provider(model_provider=model_provider, model_name=model_name),
        tools=[execute_bash_command_with_confirmation, *get_jfc_tools()],
    )
