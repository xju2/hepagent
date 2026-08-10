"""Review agent factories for JFC analyses."""

from __future__ import annotations

from pathlib import Path

from agents import Agent
from hepagent.agents.common import AgentContext
from hepagent.agents.jfc._data import get_jfc_data_dir
from hepagent.graph.report import phase_brief
from hepagent.graph.store import AnalysisGraph
from hepagent.graph.validation import validate
from hepagent.helpers import read_md
from hepagent.model_providers import get_model_provider
from hepagent.tools.common import read_file, write_review

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
PASS | ITERATE | ESCALATE | REGRESS(M)

Use REGRESS(M) only when a finding reveals that the root cause lives in an earlier Phase M
that requires re-execution (e.g. a wrong selection cut from Phase 3 causing bad yields in Phase 4a).
M must be a valid phase identifier: 1, 2, 3, 4a, 4b, 4c, or 5.
Write the verdict as exactly: REGRESS(M) — e.g. REGRESS(3) or REGRESS(4a).
"""


def _make_agent(
    name: str,
    sections: list[str],
    model_provider: str,
    model_name: str | None,
) -> Agent[AgentContext]:
    instructions = "\n\n".join(filter(None, sections))
    return Agent[AgentContext](
        name=name,
        instructions=instructions,
        model=get_model_provider(model_provider=model_provider, model_name=model_name),
        tools=[read_file, write_review],
    )


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


def _graph_section(analysis_root: Path, phase: int | str, for_arbiter: bool = False) -> str:
    """Render the graph's view of this phase for a reviewer or arbiter prompt.

    Gives the reviewer the four things the graph can settle that prose cannot:
    what has provenance, which figures actually exist, which commitments are
    still open, and the machine-readable numbers the note must match.
    """
    try:
        graph = AnalysisGraph.load(analysis_root)
        if len(graph) == 0:
            return ""
        report = validate(graph)
        brief = phase_brief(graph, str(phase))
    except Exception:  # noqa: BLE001 - a broken graph must not break review
        return ""

    parts = [
        "# ANALYSIS GRAPH",
        "",
        "The analysis keeps a provenance graph recording what produced what. Use it",
        "as evidence: it is derived from the files on disk, not from prose.",
        "",
        brief,
        report.to_markdown(),
    ]

    if for_arbiter:
        parts.append(
            "## How to use the graph findings\n\n"
            "Findings marked **error** are enforced in code — the phase cannot pass while\n"
            "they stand, whatever you conclude, so do not spend your verdict on them.\n"
            "Findings marked **warning** are yours to weigh: judge whether each reflects a\n"
            "real defect for this phase or is an expected consequence of work not done yet.\n"
            "If an error traces back to an earlier phase rather than this one, that is what\n"
            "REGRESS(M) is for."
        )
    else:
        parts.append(
            "## Checks the graph lets you make\n\n"
            "- Does every important claim in the artifact trace to a node with lineage?\n"
            "- Does every plot referenced correspond to a figure listed above?\n"
            "- Is every commitment either resolved or downscoped, with evidence?\n"
            "- Does every number in the artifact match the machine-readable values above?\n\n"
            "Cite the specific node, path or value when you raise a finding."
        )

    return "\n\n".join(parts)


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

    return _make_agent(
        name=f"JFC Physics Reviewer (Phase {phase})",
        sections=[
            f"# PHYSICS REVIEWER ROLE\n\n{role_def}" if role_def else "",
            f"# PHYSICS PROMPT\n\n{prompt}" if prompt else "",
            f"# ARTIFACT UNDER REVIEW\n\n{artifact}" if artifact else "",
            f"# OUTPUT\n\nWrite your review to: `{review_dir}/physics_review.md`",
            _EVIDENCE_MANDATE,
        ],
        model_provider=model_provider,
        model_name=model_name,
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

    return _make_agent(
        name=f"JFC Critical Reviewer (Phase {phase})",
        sections=[
            f"# CRITICAL REVIEWER ROLE\n\n{role_def}" if role_def else "",
            f"# PHYSICS PROMPT\n\n{prompt}" if prompt else "",
            f"# REVIEW METHODOLOGY (§6)\n\n{review_sec[:4000]}" if review_sec else "",
            f"# PHASE SPECIFICATION (§3)\n\n{phase_sec[:3000]}" if phase_sec else "",
            f"# COMMITMENTS.md\n\n{commitments}" if commitments else "",
            _graph_section(analysis_root, phase),
            f"# ARTIFACT UNDER REVIEW\n\n{artifact}" if artifact else "",
            f"# OUTPUT\n\nWrite your review to: `{review_dir}/critical_review.md`",
            _EVIDENCE_MANDATE,
        ],
        model_provider=model_provider,
        model_name=model_name,
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

    return _make_agent(
        name=f"JFC Constructive Reviewer (Phase {phase})",
        sections=[
            f"# CONSTRUCTIVE REVIEWER ROLE\n\n{role_def}" if role_def else "",
            f"# PHYSICS PROMPT\n\n{prompt}" if prompt else "",
            f"# REVIEW METHODOLOGY (§6)\n\n{review_sec[:4000]}" if review_sec else "",
            f"# ARTIFACT UNDER REVIEW\n\n{artifact}" if artifact else "",
            f"# OUTPUT\n\nWrite your review to: `{review_dir}/constructive_review.md`",
            _EVIDENCE_MANDATE,
        ],
        model_provider=model_provider,
        model_name=model_name,
    )


def create_plot_validator(
    phase: int | str,
    analysis_root: Path,
    model_provider: str = "cborg",
    model_name: str | None = None,
) -> Agent[AgentContext]:
    """
    Return a plot validator agent for the given phase.

    Has read_file to inspect figure-related files. RED FLAG findings are auto-Category A.
    """
    role_def = _read_agent_def("plot_validator")
    phase_dir = _PHASE_DIR_MAP.get(phase, "")
    figures_dir = analysis_root / phase_dir / "outputs" / "figures" if phase_dir else None
    review_dir = analysis_root / phase_dir / "review" if phase_dir else analysis_root / "review"
    lint_script = _JFC_SRC / "conventions" / "lint_plots.py"

    return _make_agent(
        name=f"JFC Plot Validator (Phase {phase})",
        sections=[
            f"# PLOT VALIDATOR ROLE\n\n{role_def}" if role_def else "",
            f"# FIGURES DIRECTORY\n\n`{figures_dir}`" if figures_dir else "",
            (
                f"# LINT SCRIPT\n\nRun: `python {lint_script} {figures_dir}`\n"
                f"Any RED FLAG line in the output is automatically Category A."
                if lint_script.exists()
                else ""
            ),
            _graph_section(analysis_root, phase),
            f"# OUTPUT\n\nWrite your validation to: `{review_dir}/plot_validation.md`\n"
            f"List each figure with its status. 'Figures look fine' is not acceptable.\n"
            f"Every figure the graph lists must be accounted for, and every figure an\n"
            f"analysis note references must appear in that list.",
            _EVIDENCE_MANDATE,
        ],
        model_provider=model_provider,
        model_name=model_name,
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

    return _make_agent(
        name=f"JFC BibTeX Validator (Phase {phase})",
        sections=[
            f"# BIBTEX VALIDATOR ROLE\n\n{role_def}" if role_def else "",
            f"# REFERENCES FILE\n\n`{bib_path}`",
            f"# OUTPUT\n\nWrite your validation to: `{review_dir}/bibtex_validation.md`",
            _EVIDENCE_MANDATE,
        ],
        model_provider=model_provider,
        model_name=model_name,
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

    return _make_agent(
        name="JFC Rendering Reviewer",
        sections=[
            f"# RENDERING REVIEWER ROLE\n\n{role_def}" if role_def else "",
            (
                f"# RENDERING CHECKLIST (from §6.4.3)\n\n{review_sec_excerpt[2000:4000]}"
                if review_sec_excerpt
                else ""
            ),
            f"# OUTPUT\n\nWrite your review to: `{review_dir}/rendering_review.md`\n\n"
            f"Check: zero unresolved cross-references, TOC pages correct,"
            f" no raw LaTeX visible, title symbols render correctly,"
            f" no $\\pm$ with dollar signs in body text,"
            f" all composite figure panels legible.",
            _EVIDENCE_MANDATE,
        ],
        model_provider=model_provider,
        model_name=model_name,
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

    return _make_agent(
        name=f"JFC Arbiter (Phase {phase})",
        sections=[
            f"# ARBITER ROLE\n\n{role_def}" if role_def else "",
            f"# REVIEW METHODOLOGY (§6)\n\n{review_sec[:5000]}" if review_sec else "",
            f"# COMMITMENTS.md\n\n{commitments}" if commitments else "",
            _graph_section(analysis_root, phase, for_arbiter=True),
            "# REVIEWER OUTPUTS\n\n" + "\n\n---\n\n".join(review_files) if review_files else "",
            f"# OUTPUT\n\nWrite your adjudication to: `{review_dir}/ADJUDICATION.md`\n\n"
            f"Produce a structured adjudication table then end with one of:\n"
            f"  PASS\n"
            f"  ITERATE — list Category A items that must be fixed in this phase\n"
            f"  ESCALATE — human intervention required\n"
            f"  REGRESS(M) — root cause lies in an earlier Phase M; re-execution of Phase M\n"
            f"   is required before this phase can pass. M must be the exact phase identifier\n"
            f"    (e.g. REGRESS(3) or REGRESS(4a)). Use this only when the fix cannot be made\n"
            f"    within the current phase.\n\n"
            f"Render PASS only if the phase's subgraph is internally consistent: every\n"
            f"    claim traceable to a node with lineage, every referenced figure present,\n"
            f"    every commitment resolved or downscoped with evidence, and every number\n"
            f"    matching the machine-readable results. Use ITERATE when the graph is\n"
            f"    incomplete but repairable within this phase, and REGRESS(M) when a gap\n"
            f"    traces back to an earlier phase.\n\n"
            f"At Phase 4a, additionally: before rendering verdict, verify all commitments "
            f"in COMMITMENTS.md are either resolved or formally downscoped. "
            f"Any pending commitment is automatically Category A.",
            _EVIDENCE_MANDATE,
        ],
        model_provider=model_provider,
        model_name=model_name,
    )
