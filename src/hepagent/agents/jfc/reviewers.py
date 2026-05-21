"""Review agent factories for JFC analyses."""

from __future__ import annotations

from pathlib import Path

from agents import Agent
from hepagent.agents.bash import execute_bash_command_with_confirmation
from hepagent.agents.common import AgentContext
from hepagent.agents.jfc._data import get_jfc_data_dir
from hepagent.helpers import read_md
from hepagent.model_providers import get_model_provider
from hepagent.tools.common import read_resource

_JFC_SRC = get_jfc_data_dir()

_PHASE_DIR_MAP = {
    1: "phase1_strategy",
    2: "phase2_exploration",
    3: "phase3_selection",
    "4a": "phase4a_inference_expected",
    "4b": "phase4b_inference_partial",
    "4c": "phase4c_inference_observed",
    5: "phase5_documentation",
}

_EVIDENCE_MANDATE = """
EVIDENCE-BASED REVIEW (mandatory):
Every verification claim must cite specific evidence. Unacceptable patterns:
- "Verified" / "Confirmed" / "Checks out" — without a number or reference
- "Looks reasonable" / "Appears correct" — without stating what was checked

Acceptable patterns:
- "Closure chi2/ndf = 1.3/36 (p = 0.24) from results/closure.json — PASS"
- "Tracking systematic: 0.95% (artifact Table 7) matches results/systematics.json — consistent"
- "Figure 12 ratio panel: all bins within [0.85, 1.15] — acceptable data/MC agreement"

List each finding with:
## Findings

### Category A (Must Resolve)
- [Finding with specific evidence citation]

### Category B (Should Address)
- ...

### Category C (Suggestions)
- ...

## Verdict
PASS | ITERATE | ESCALATE
"""


def _read_agent_def(name: str) -> str:
    return read_md(_JFC_SRC / "agents" / f"{name}.md")


def _read_methodology(filename: str) -> str:
    return read_md(_JFC_SRC / "methodology" / filename)


def _read_artifact(analysis_root: Path, phase: int | str) -> str:
    phase_dir = _PHASE_DIR_MAP.get(phase, "")
    if not phase_dir:
        return ""
    artifact_names = {
        1: "STRATEGY.md",
        2: "EXPLORATION.md",
        3: "SELECTION.md",
        "4a": "INFERENCE_EXPECTED.md",
        "4b": "INFERENCE_PARTIAL.md",
        "4c": "INFERENCE_OBSERVED.md",
        5: "ANALYSIS_NOTE_5_v1.md",
    }
    name = artifact_names.get(phase, "")
    if not name:
        return ""
    path = analysis_root / phase_dir / "outputs" / name
    content = read_md(path)
    return content[:6000] if len(content) > 6000 else content


def _physics_prompt(analysis_root: Path) -> str:
    return read_md(analysis_root / "prompt.md")


def create_physics_reviewer(
    phase: int | str,
    analysis_root: Path,
    model_provider: str = "cborg",
    model_name: str | None = None,
) -> Agent[AgentContext]:
    """
    Return a physics reviewer agent for the given phase.

    Physics reviewer receives ONLY the physics prompt + artifact.
    No methodology spec, no conventions. Reviews physics on merits only.
    """
    role_def = _read_agent_def("physics_reviewer")
    artifact = _read_artifact(analysis_root, phase)
    prompt = _physics_prompt(analysis_root)

    phase_dir = _PHASE_DIR_MAP.get(phase, "")
    review_dir = analysis_root / phase_dir / "review" if phase_dir else analysis_root / "review"

    instructions = "\n\n".join(
        filter(
            None,
            [
                f"# PHYSICS REVIEWER ROLE\n\n{role_def}" if role_def else "",
                f"# PHYSICS PROMPT\n\n{prompt}" if prompt else "",
                f"# ARTIFACT UNDER REVIEW\n\n{artifact}" if artifact else "",
                f"# OUTPUT\n\nWrite your review to: `{review_dir}/physics_review.md`",
                _EVIDENCE_MANDATE,
            ],
        )
    )

    return Agent[AgentContext](
        name=f"JFC Physics Reviewer (Phase {phase})",
        instructions=instructions,
        model=get_model_provider(model_provider=model_provider, model_name=model_name),
        tools=[],  # read-only; no tools needed
    )


def create_critical_reviewer(
    phase: int | str,
    analysis_root: Path,
    model_provider: str = "cborg",
    model_name: str | None = None,
) -> Agent[AgentContext]:
    """
    Return a critical reviewer agent ("bad cop") for the given phase.

    Has full context: methodology §6 + applicable §3 + artifact + conventions.
    """
    role_def = _read_agent_def("critical_reviewer")
    review_sec = _read_methodology("06-review.md")
    phase_sec = _read_methodology("03-phases.md")
    artifact = _read_artifact(analysis_root, phase)
    prompt = _physics_prompt(analysis_root)
    commitments = read_md(analysis_root / "COMMITMENTS.md")

    phase_dir = _PHASE_DIR_MAP.get(phase, "")
    review_dir = analysis_root / phase_dir / "review" if phase_dir else analysis_root / "review"

    instructions = "\n\n".join(
        filter(
            None,
            [
                f"# CRITICAL REVIEWER ROLE\n\n{role_def}" if role_def else "",
                f"# PHYSICS PROMPT\n\n{prompt}" if prompt else "",
                f"# REVIEW METHODOLOGY (§6)\n\n{review_sec[:4000]}" if review_sec else "",
                f"# PHASE SPECIFICATION (§3)\n\n{phase_sec[:3000]}" if phase_sec else "",
                f"# COMMITMENTS.md\n\n{commitments}" if commitments else "",
                f"# ARTIFACT UNDER REVIEW\n\n{artifact}" if artifact else "",
                f"# OUTPUT\n\nWrite your review to: `{review_dir}/critical_review.md`",
                _EVIDENCE_MANDATE,
            ],
        )
    )

    return Agent[AgentContext](
        name=f"JFC Critical Reviewer (Phase {phase})",
        instructions=instructions,
        model=get_model_provider(model_provider=model_provider, model_name=model_name),
        tools=[read_resource],
    )


def create_constructive_reviewer(
    phase: int | str,
    analysis_root: Path,
    model_provider: str = "cborg",
    model_name: str | None = None,
) -> Agent[AgentContext]:
    """
    Return a constructive reviewer agent ("good cop") for the given phase.

    Same context as critical; focuses on strengthening clarity, validation, presentation.
    """
    role_def = _read_agent_def("constructive_reviewer")
    review_sec = _read_methodology("06-review.md")
    artifact = _read_artifact(analysis_root, phase)
    prompt = _physics_prompt(analysis_root)

    phase_dir = _PHASE_DIR_MAP.get(phase, "")
    review_dir = analysis_root / phase_dir / "review" if phase_dir else analysis_root / "review"

    instructions = "\n\n".join(
        filter(
            None,
            [
                f"# CONSTRUCTIVE REVIEWER ROLE\n\n{role_def}" if role_def else "",
                f"# PHYSICS PROMPT\n\n{prompt}" if prompt else "",
                f"# REVIEW METHODOLOGY (§6)\n\n{review_sec[:4000]}" if review_sec else "",
                f"# ARTIFACT UNDER REVIEW\n\n{artifact}" if artifact else "",
                f"# OUTPUT\n\nWrite your review to: `{review_dir}/constructive_review.md`",
                _EVIDENCE_MANDATE,
            ],
        )
    )

    return Agent[AgentContext](
        name=f"JFC Constructive Reviewer (Phase {phase})",
        instructions=instructions,
        model=get_model_provider(model_provider=model_provider, model_name=model_name),
        tools=[read_resource],
    )


def create_plot_validator(
    phase: int | str,
    analysis_root: Path,
    model_provider: str = "cborg",
    model_name: str | None = None,
) -> Agent[AgentContext]:
    """
    Return a plot validator agent for the given phase.

    Has execute_bash_command_with_confirmation to run lint_plots.py and
    inspect figure files. RED FLAG findings are auto-Category A.
    """
    role_def = _read_agent_def("plot_validator")
    phase_dir = _PHASE_DIR_MAP.get(phase, "")
    figures_dir = analysis_root / phase_dir / "outputs" / "figures" if phase_dir else None
    review_dir = analysis_root / phase_dir / "review" if phase_dir else analysis_root / "review"
    lint_script = _JFC_SRC / "conventions" / "lint_plots.py"

    instructions = "\n\n".join(
        filter(
            None,
            [
                f"# PLOT VALIDATOR ROLE\n\n{role_def}" if role_def else "",
                f"# FIGURES DIRECTORY\n\n`{figures_dir}`" if figures_dir else "",
                f"# LINT SCRIPT\n\nRun: `python {lint_script} {figures_dir}`\n"
                f"Any RED FLAG line in the output is automatically Category A."
                if lint_script.exists()
                else "",
                f"# OUTPUT\n\nWrite your validation to: `{review_dir}/plot_validation.md`\n"
                f"List each figure with its status. 'Figures look fine' is not acceptable.",
                _EVIDENCE_MANDATE,
            ],
        )
    )

    return Agent[AgentContext](
        name=f"JFC Plot Validator (Phase {phase})",
        instructions=instructions,
        model=get_model_provider(model_provider=model_provider, model_name=model_name),
        tools=[execute_bash_command_with_confirmation],
    )


def create_bibtex_validator(
    phase: int | str,
    analysis_root: Path,
    model_provider: str = "cborg",
    model_name: str | None = None,
) -> Agent[AgentContext]:
    """
    Return a bibtex validator agent for the given phase.

    Validates citations in the AN against the references.bib file.
    """
    role_def = _read_agent_def("bibtex_validator")
    phase_dir = _PHASE_DIR_MAP.get(phase, "")
    bib_path = analysis_root / "phase5_documentation" / "outputs" / "references.bib"
    review_dir = analysis_root / phase_dir / "review" if phase_dir else analysis_root / "review"

    instructions = "\n\n".join(
        filter(
            None,
            [
                f"# BIBTEX VALIDATOR ROLE\n\n{role_def}" if role_def else "",
                f"# REFERENCES FILE\n\n`{bib_path}`",
                f"# OUTPUT\n\nWrite your validation to: `{review_dir}/bibtex_validation.md`",
                _EVIDENCE_MANDATE,
            ],
        )
    )

    return Agent[AgentContext](
        name=f"JFC BibTeX Validator (Phase {phase})",
        instructions=instructions,
        model=get_model_provider(model_provider=model_provider, model_name=model_name),
        tools=[execute_bash_command_with_confirmation],
    )


def create_rendering_reviewer(
    analysis_root: Path,
    model_provider: str = "cborg",
    model_name: str | None = None,
) -> Agent[AgentContext]:
    """
    Return a rendering reviewer agent (Phase 5 only).

    Compiles and inspects the PDF for rendering issues.
    """
    role_def = _read_agent_def("rendering_reviewer")
    review_sec_excerpt = _read_methodology("06-review.md")
    review_dir = analysis_root / "phase5_documentation" / "review"

    instructions = "\n\n".join(
        filter(
            None,
            [
                f"# RENDERING REVIEWER ROLE\n\n{role_def}" if role_def else "",
                f"# RENDERING CHECKLIST (from §6.4.3)\n\n{review_sec_excerpt[2000:4000]}"
                if review_sec_excerpt
                else "",
                f"# OUTPUT\n\nWrite your review to: `{review_dir}/rendering_review.md`\n\n"
                f"Check: zero unresolved cross-references, TOC pages correct,"
                f" no raw LaTeX visible, title symbols render correctly,"
                f" no $\\pm$ with dollar signs in body text,"
                f" all composite figure panels legible.",
                _EVIDENCE_MANDATE,
            ],
        )
    )

    return Agent[AgentContext](
        name="JFC Rendering Reviewer",
        instructions=instructions,
        model=get_model_provider(model_provider=model_provider, model_name=model_name),
        tools=[execute_bash_command_with_confirmation],
    )


def create_arbiter(
    phase: int | str,
    analysis_root: Path,
    model_provider: str = "cborg",
    model_name: str | None = None,
) -> Agent[AgentContext]:
    """
    Return an arbiter agent that adjudicates multiple reviewer outputs.

    Input: all reviewer finding documents (written to review/ directory).
    Output: ADJUDICATION.md with structured table and final verdict.
    """
    role_def = _read_agent_def("arbiter")
    review_sec = _read_methodology("06-review.md")
    phase_dir = _PHASE_DIR_MAP.get(phase, "")
    review_dir = analysis_root / phase_dir / "review" if phase_dir else analysis_root / "review"
    commitments = read_md(analysis_root / "COMMITMENTS.md")

    # Load all review outputs written so far
    review_files = []
    if review_dir.exists():
        for review_file in sorted(review_dir.glob("*.md")):
            content = read_md(review_file)
            if content:
                review_files.append(f"### {review_file.name}\n\n{content[:2000]}")

    instructions = "\n\n".join(
        filter(
            None,
            [
                f"# ARBITER ROLE\n\n{role_def}" if role_def else "",
                f"# REVIEW METHODOLOGY (§6)\n\n{review_sec[:5000]}" if review_sec else "",
                f"# COMMITMENTS.md\n\n{commitments}" if commitments else "",
                "# REVIEWER OUTPUTS\n\n" + "\n\n---\n\n".join(review_files) if review_files else "",
                f"# OUTPUT\n\nWrite your adjudication to: `{review_dir}/ADJUDICATION.md`\n\n"
                f"Produce a structured adjudication table then end with:\n"
                f"PASS | ITERATE (list Category A items) | ESCALATE\n\n"
                f"At Phase 4a, additionally: before rendering verdict, verify all commitments "
                f"in COMMITMENTS.md are either resolved or formally downscoped. "
                f"Any pending commitment is automatically Category A.",
                _EVIDENCE_MANDATE,
            ],
        )
    )

    return Agent[AgentContext](
        name=f"JFC Arbiter (Phase {phase})",
        instructions=instructions,
        model=get_model_provider(model_provider=model_provider, model_name=model_name),
        tools=[read_resource, execute_bash_command_with_confirmation],
    )
