"""Phase executor agent factories for JFC analyses."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from agents import Agent
from hepagent.agent_helpers import update_logbook
from hepagent.agents.bash import execute_bash_command_with_confirmation
from hepagent.agents.common import AgentContext
from hepagent.agents.jfc._data import get_jfc_data_dir
from hepagent.helpers import read_md
from hepagent.model_providers import get_model_provider
from hepagent.tools.common import ask_user_for_info, read_resource, web_search
from hepagent.tools.jfc import get_jfc_tools

_JFC_SRC = get_jfc_data_dir()

# Canonical phase layout: phase -> (directory, prompt template, primary artifact).
# The graph builder reads this so the graph's dependency edges cannot drift from
# what executors actually read.
PHASE_SPECS: dict[int | str, tuple[str, str, str]] = {
    1: ("phase1_strategy", "phase1_claude.md", "STRATEGY.md"),
    2: ("phase2_exploration", "phase2_claude.md", "EXPLORATION.md"),
    3: ("phase3_selection", "phase3_claude.md", "SELECTION.md"),
    "4a": ("phase4a_inference_expected", "phase4_claude.md", "INFERENCE_EXPECTED.md"),
    "4b": ("phase4b_inference_partial", "phase4_claude.md", "INFERENCE_PARTIAL.md"),
    "4c": ("phase4c_inference_observed", "phase4_claude.md", "INFERENCE_OBSERVED.md"),
    5: ("phase5_documentation", "phase5_claude.md", "ANALYSIS_NOTE_5_v1.md"),
}

UPSTREAM_ARTIFACTS: dict[int | str, list[str]] = {
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
    paths = UPSTREAM_ARTIFACTS.get(phase, [])
    blocks = []
    for rel in paths:
        full = analysis_root / rel
        content = read_md(full)
        if content:
            blocks.append(f"### {rel}\n\n{content}")
    if not blocks:
        return ""
    return "## PRIOR PHASE ARTIFACTS\n\n" + "\n\n---\n\n".join(blocks)


def _note_graph_section(analysis_root: Path) -> str:
    """Render the graph slice the note writer must write from.

    Every figure the note references has to be on the manifest, and every number
    it quotes has to appear in the evidence digest. That is what makes the
    finished PDF reproducible from the graph rather than from prompt history.
    """
    from hepagent.graph.report import note_brief
    from hepagent.graph.store import AnalysisGraph

    try:
        graph = AnalysisGraph.load(analysis_root)
        if len(graph) == 0:
            return ""
        brief = note_brief(graph)
    except Exception:  # noqa: BLE001 - a broken graph must not block note writing
        return ""

    return (
        "# WRITE FROM THE ANALYSIS GRAPH\n\n"
        "The graph below is the source of record for this note. It is derived from\n"
        "the files on disk, so it is authoritative over any number or filename that\n"
        "appears in the phase artifacts.\n\n"
        "Binding rules:\n"
        "- Reference **only** figures listed in the manifest, by the exact path given.\n"
        "  A reference to anything else is a broken image and an untraceable claim.\n"
        "- Quote numbers **exactly** as they appear in the results digest. Where the\n"
        "  artifacts and the digest disagree, the digest wins — the JSON is what the\n"
        "  code actually produced.\n"
        "- Account for every commitment: state where each was met, or why it was\n"
        "  downscoped. A commitment marked still open must be named as an open issue.\n"
        "- Anchor each claim in something the graph records. If you cannot point to a\n"
        "  figure, a results value or an artifact, say so rather than asserting it.\n\n" + brief
    )


def _graph_contract_section(phase: int | str, analysis_root: Path) -> str:
    """Render the phase's graph write-back contract for the executor prompt."""
    from hepagent.tools.jfc.graph import contract_summary

    return (
        f"# GRAPH WRITE-BACK CONTRACT\n\n"
        f"This analysis keeps a durable provenance graph at "
        f"`{analysis_root}/graph/`. Artifacts, figures, result JSON files and "
        f"commitment-table rows are ingested from disk automatically — you do "
        f"not need to record those.\n\n"
        f"What you *must* record with `graph_add_node` / `graph_add_edge` is the "
        f"meaning the filesystem cannot show:\n"
        f"- the datasets you actually used, with their AMI tag and campaign;\n"
        f"- the selection or statistical method behind a result;\n"
        f"- the specific evidence that resolves each commitment you close, and "
        f"the documented reason for any commitment you downscope.\n\n"
        f"{contract_summary(str(phase))}\n\n"
        f"Calls outside this contract are refused. Use `graph_query` to inspect "
        f"existing nodes before linking to them — edges need real node ids on "
        f"both ends. Every major claim in your artifact should be reachable from "
        f"a `supports` or `derives_from` edge."
    )


def _assemble_executor_prompt(
    phase: int | str,
    analysis_root: Path,
    phase_dir: str,
    artifact_name: str,
    template_name: str,
    codesign_feedback: str | None = None,
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

    # 6. Graph write-back contract
    parts.append(_graph_contract_section(phase, analysis_root))

    # 7. Codesign human feedback (only present on revision runs)
    if codesign_feedback:
        parts.append(
            "# HUMAN FEEDBACK FROM CODESIGN REVIEW\n\n"
            "The analysis strategy was reviewed by physicists and open concerns were recorded. "
            "For each OPEN item below, investigate it using all tools available to you "
            "(web_search, execute_bash_command_with_confirmation, read_resource, read_file). "
            "Determine whether the concern can be resolved with existing resources "
            "(e.g. a published systematic uncertainty table, a simulation configuration file, "
            "or a data release note). Then do one of the following:\n\n"
            "- If the concern **can** be resolved: update the strategy to incorporate the "
            "relevant information and cite the source.\n"
            "- If the concern **cannot** be resolved because the resource genuinely does not "
            "exist (e.g. the MC generator provides no systematic uncertainty breakdown): "
            "document it explicitly as a known limitation in the strategy, state the reason, "
            "and propose a mitigation or alternative approach.\n\n"
            "Do not leave any OPEN item unaddressed. Do not remove content that was already "
            "correct — only revise the sections indicated by the feedback.\n\n" + codesign_feedback
        )

    return "\n\n".join(parts)


def create_phase_executor(
    phase: int | str,
    analysis_root: Path,
    model_provider: str = "cborg",
    model_name: str | None = None,
    codesign_feedback: str | None = None,
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
        codesign_feedback: Human feedback from the codesign gate (Phase 1 revisions only).
            When provided, appended as a mandatory revision directive to the executor prompt.
    """
    if phase not in PHASE_SPECS:
        raise ValueError(f"Unknown phase: {phase}. Valid: {list(PHASE_SPECS)}")

    phase_dir, template_name, artifact_name = PHASE_SPECS[phase]
    instructions = _assemble_executor_prompt(
        phase,
        analysis_root,
        phase_dir,
        artifact_name,
        template_name,
        codesign_feedback=codesign_feedback,
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
            web_search,
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

    # The graph is the note's source of record: it lists the figures that exist,
    # the numbers that are authoritative, and the commitments that must be
    # accounted for. Written after the task so its rules read as constraints.
    parts.append(_note_graph_section(analysis_root))

    if artifact_blocks:
        parts.append("# PHASE ARTIFACTS\n\n" + "\n\n---\n\n".join(artifact_blocks))

    instructions = "\n\n".join(parts)

    from hepagent.tools.jfc.graph import graph_query

    return Agent[AgentContext](
        name=f"JFC Note Writer (Phase {phase})",
        instructions=instructions,
        model=get_model_provider(model_provider=model_provider, model_name=model_name),
        # Read-only: no bash execution. graph_query lets it check provenance for
        # a claim without being able to write anything.
        tools=[read_resource, graph_query],
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
