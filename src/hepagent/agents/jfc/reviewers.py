"""Review agent factories.

Every factory takes the `PlanNode` under review rather than a phase key: which
reviewers run, whether an arbiter adjudicates, and where the findings are written
all come from the plan.
"""

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
from hepagent.plan.schema import AnalysisPlan, PlanNode
from hepagent.plan.store import resolve_plan
from hepagent.tools.common import read_file, write_review

_JFC_SRC = get_jfc_data_dir()

_EVIDENCE_MANDATE_HEAD = """
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
"""


def evidence_mandate(plan: AnalysisPlan | None = None) -> str:
    """The shared output contract every reviewer is held to.

    `REGRESS(M)` names an upstream node, so the valid values of M are the plan's
    node ids. Listing them keeps a reviewer from inventing one the orchestrator
    cannot resolve.
    """
    if plan is not None and plan.nodes:
        valid = ", ".join(plan.node_ids())
        example = plan.node_ids()[0]
    else:
        valid = "any node id declared in the analysis plan"
        example = "selection"

    return (
        _EVIDENCE_MANDATE_HEAD
        + f"\nUse REGRESS(M) only when a finding reveals that the root cause lives in an\n"
        f"earlier node M that requires re-execution (e.g. a wrong selection cut causing\n"
        f"bad yields downstream).\n"
        f"M must be one of: {valid}.\n"
        f"Write the verdict as exactly: REGRESS(M) — e.g. REGRESS({example}).\n"
    )


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


def _read_artifact(analysis_root: Path, node: PlanNode) -> str:
    content = read_md(analysis_root / node.artifact_path)
    return content[:6000] if len(content) > 6000 else content


def _review_dir(analysis_root: Path, node: PlanNode) -> Path:
    return analysis_root / node.directory / "review"


def _bibliography(analysis_root: Path, plan: AnalysisPlan) -> Path:
    """Where citations are collected — the last node that writes an analysis note."""
    note_nodes = [n for n in plan.nodes if n.produces_note]
    if not note_nodes:
        return analysis_root / "references.bib"
    return analysis_root / note_nodes[-1].outputs_dir / "references.bib"


def _physics_prompt(analysis_root: Path) -> str:
    return read_md(analysis_root / "prompt.md")


def _graph_section(analysis_root: Path, node: PlanNode, for_arbiter: bool = False) -> str:
    """Render the graph's view of this node for a reviewer or arbiter prompt.

    Gives the reviewer the four things the graph can settle that prose cannot:
    what has provenance, which figures actually exist, which commitments are
    still open, and the machine-readable numbers the note must match.
    """
    try:
        graph = AnalysisGraph.load(analysis_root)
        if len(graph) == 0:
            return ""
        report = validate(graph)
        brief = phase_brief(graph, node.id)
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
    node: PlanNode,
    analysis_root: Path,
    plan: AnalysisPlan | None = None,
    model_provider: str = "cborg",
    model_name: str | None = None,
) -> Agent[AgentContext]:
    """
    Return a physics reviewer agent for the given node.

    Physics reviewer receives ONLY the physics prompt + artifact.
    No methodology spec, no conventions. Reviews physics on merits only.
    """
    plan = resolve_plan(analysis_root, plan)
    role_def = _read_agent_def("physics_reviewer")
    artifact = _read_artifact(analysis_root, node)
    prompt = _physics_prompt(analysis_root)
    review_dir = _review_dir(analysis_root, node)

    return _make_agent(
        name=f"JFC Physics Reviewer ({node.label})",
        sections=[
            f"# PHYSICS REVIEWER ROLE\n\n{role_def}" if role_def else "",
            f"# PHYSICS PROMPT\n\n{prompt}" if prompt else "",
            f"# ARTIFACT UNDER REVIEW\n\n{artifact}" if artifact else "",
            f"# OUTPUT\n\nWrite your review to: `{review_dir}/physics_review.md`",
            evidence_mandate(plan),
        ],
        model_provider=model_provider,
        model_name=model_name,
    )


def create_critical_reviewer(
    node: PlanNode,
    analysis_root: Path,
    plan: AnalysisPlan | None = None,
    model_provider: str = "cborg",
    model_name: str | None = None,
) -> Agent[AgentContext]:
    """
    Return a critical reviewer agent ("bad cop") for the given node.

    Has full context: methodology §6 + applicable §3 + artifact + conventions.
    """
    plan = resolve_plan(analysis_root, plan)
    role_def = _read_agent_def("critical_reviewer")
    review_sec = _read_methodology("06-review.md")
    phase_sec = _read_methodology("03-phases.md")
    artifact = _read_artifact(analysis_root, node)
    prompt = _physics_prompt(analysis_root)
    commitments = read_md(analysis_root / "COMMITMENTS.md")
    review_dir = _review_dir(analysis_root, node)

    return _make_agent(
        name=f"JFC Critical Reviewer ({node.label})",
        sections=[
            f"# CRITICAL REVIEWER ROLE\n\n{role_def}" if role_def else "",
            f"# PHYSICS PROMPT\n\n{prompt}" if prompt else "",
            f"# REVIEW METHODOLOGY (§6)\n\n{review_sec[:4000]}" if review_sec else "",
            f"# PHASE SPECIFICATION (§3)\n\n{phase_sec[:3000]}" if phase_sec else "",
            f"# COMMITMENTS.md\n\n{commitments}" if commitments else "",
            _graph_section(analysis_root, node),
            f"# ARTIFACT UNDER REVIEW\n\n{artifact}" if artifact else "",
            f"# OUTPUT\n\nWrite your review to: `{review_dir}/critical_review.md`",
            evidence_mandate(plan),
        ],
        model_provider=model_provider,
        model_name=model_name,
    )


def create_constructive_reviewer(
    node: PlanNode,
    analysis_root: Path,
    plan: AnalysisPlan | None = None,
    model_provider: str = "cborg",
    model_name: str | None = None,
) -> Agent[AgentContext]:
    """
    Return a constructive reviewer agent ("good cop") for the given node.

    Same context as critical; focuses on strengthening clarity, validation, presentation.
    """
    plan = resolve_plan(analysis_root, plan)
    role_def = _read_agent_def("constructive_reviewer")
    review_sec = _read_methodology("06-review.md")
    artifact = _read_artifact(analysis_root, node)
    prompt = _physics_prompt(analysis_root)
    review_dir = _review_dir(analysis_root, node)

    return _make_agent(
        name=f"JFC Constructive Reviewer ({node.label})",
        sections=[
            f"# CONSTRUCTIVE REVIEWER ROLE\n\n{role_def}" if role_def else "",
            f"# PHYSICS PROMPT\n\n{prompt}" if prompt else "",
            f"# REVIEW METHODOLOGY (§6)\n\n{review_sec[:4000]}" if review_sec else "",
            f"# ARTIFACT UNDER REVIEW\n\n{artifact}" if artifact else "",
            f"# OUTPUT\n\nWrite your review to: `{review_dir}/constructive_review.md`",
            evidence_mandate(plan),
        ],
        model_provider=model_provider,
        model_name=model_name,
    )


def create_plot_validator(
    node: PlanNode,
    analysis_root: Path,
    plan: AnalysisPlan | None = None,
    model_provider: str = "cborg",
    model_name: str | None = None,
) -> Agent[AgentContext]:
    """
    Return a plot validator agent for the given node.

    Has read_file to inspect figure-related files. RED FLAG findings are auto-Category A.
    """
    plan = resolve_plan(analysis_root, plan)
    role_def = _read_agent_def("plot_validator")
    figures_dir = analysis_root / node.outputs_dir / "figures"
    review_dir = _review_dir(analysis_root, node)
    lint_script = _JFC_SRC / "conventions" / "lint_plots.py"

    return _make_agent(
        name=f"JFC Plot Validator ({node.label})",
        sections=[
            f"# PLOT VALIDATOR ROLE\n\n{role_def}" if role_def else "",
            f"# FIGURES DIRECTORY\n\n`{figures_dir}`",
            (
                f"# LINT SCRIPT\n\nRun: `python {lint_script} {figures_dir}`\n"
                f"Any RED FLAG line in the output is automatically Category A."
                if lint_script.exists()
                else ""
            ),
            _graph_section(analysis_root, node),
            f"# OUTPUT\n\nWrite your validation to: `{review_dir}/plot_validation.md`\n"
            f"List each figure with its status. 'Figures look fine' is not acceptable.\n"
            f"Every figure the graph lists must be accounted for, and every figure an\n"
            f"analysis note references must appear in that list.",
            evidence_mandate(plan),
        ],
        model_provider=model_provider,
        model_name=model_name,
    )


def create_bibtex_validator(
    node: PlanNode,
    analysis_root: Path,
    plan: AnalysisPlan | None = None,
    model_provider: str = "cborg",
    model_name: str | None = None,
) -> Agent[AgentContext]:
    """
    Return a bibtex validator agent for the given node.

    Validates citations in the AN against the references.bib file.
    """
    plan = resolve_plan(analysis_root, plan)
    role_def = _read_agent_def("bibtex_validator")
    bib_path = _bibliography(analysis_root, plan)
    review_dir = _review_dir(analysis_root, node)

    return _make_agent(
        name=f"JFC BibTeX Validator ({node.label})",
        sections=[
            f"# BIBTEX VALIDATOR ROLE\n\n{role_def}" if role_def else "",
            f"# REFERENCES FILE\n\n`{bib_path}`",
            f"# OUTPUT\n\nWrite your validation to: `{review_dir}/bibtex_validation.md`",
            evidence_mandate(plan),
        ],
        model_provider=model_provider,
        model_name=model_name,
    )


def create_rendering_reviewer(
    node: PlanNode,
    analysis_root: Path,
    plan: AnalysisPlan | None = None,
    model_provider: str = "cborg",
    model_name: str | None = None,
) -> Agent[AgentContext]:
    """
    Return a rendering reviewer agent.

    Compiles and inspects the PDF for rendering issues, so it only earns its keep
    on a node that writes an analysis note.
    """
    plan = resolve_plan(analysis_root, plan)
    role_def = _read_agent_def("rendering_reviewer")
    review_sec_excerpt = _read_methodology("06-review.md")
    review_dir = _review_dir(analysis_root, node)

    return _make_agent(
        name=f"JFC Rendering Reviewer ({node.label})",
        sections=[
            f"# RENDERING REVIEWER ROLE\n\n{role_def}" if role_def else "",
            (
                f"# RENDERING CHECKLIST (from §6.4.3)\n\n{review_sec_excerpt[2000:4000]}"
                if review_sec_excerpt
                else ""
            ),
            f"# ANALYSIS NOTE\n\nCompiled PDF: `{analysis_root / node.note_pdf_path}`",
            f"# OUTPUT\n\nWrite your review to: `{review_dir}/rendering_review.md`\n\n"
            f"Check: zero unresolved cross-references, TOC pages correct,"
            f" no raw LaTeX visible, title symbols render correctly,"
            f" no $\\pm$ with dollar signs in body text,"
            f" all composite figure panels legible.",
            evidence_mandate(plan),
        ],
        model_provider=model_provider,
        model_name=model_name,
    )


def create_arbiter(
    node: PlanNode,
    analysis_root: Path,
    plan: AnalysisPlan | None = None,
    model_provider: str = "cborg",
    model_name: str | None = None,
) -> Agent[AgentContext]:
    """
    Return an arbiter agent that adjudicates multiple reviewer outputs.

    Input: all reviewer finding documents (written to review/ directory).
    Output: ADJUDICATION.md with structured table and final verdict.
    """
    plan = resolve_plan(analysis_root, plan)
    role_def = _read_agent_def("arbiter")
    review_sec = _read_methodology("06-review.md")
    review_dir = _review_dir(analysis_root, node)
    commitments = read_md(analysis_root / "COMMITMENTS.md")
    gates_before = [g.name for g in node.gates_at("before")]

    # Load all review outputs written so far
    review_files = []
    if review_dir.exists():
        for review_file in sorted(review_dir.glob("*.md")):
            content = read_md(review_file)
            if content:
                review_files.append(f"### {review_file.name}\n\n{content[:2000]}")

    upstream = ", ".join(plan.prerequisites(node.id)) or "none — this is a starting node"

    return _make_agent(
        name=f"JFC Arbiter ({node.label})",
        sections=[
            f"# ARBITER ROLE\n\n{role_def}" if role_def else "",
            f"# REVIEW METHODOLOGY (§6)\n\n{review_sec[:5000]}" if review_sec else "",
            f"# NODE UNDER ADJUDICATION\n\n"
            f"Node id: `{node.id}` — {node.label}\n"
            f"Artifact: `{node.artifact_path}`\n"
            f"Upstream nodes: {upstream}",
            f"# COMMITMENTS.md\n\n{commitments}" if commitments else "",
            _graph_section(analysis_root, node, for_arbiter=True),
            "# REVIEWER OUTPUTS\n\n" + "\n\n---\n\n".join(review_files) if review_files else "",
            f"# OUTPUT\n\nWrite your adjudication to: `{review_dir}/ADJUDICATION.md`\n\n"
            f"Produce a structured adjudication table then end with one of:\n"
            f"  PASS\n"
            f"  ITERATE — list Category A items that must be fixed in this node\n"
            f"  ESCALATE — human intervention required\n"
            f"  REGRESS(M) — root cause lies in an upstream node M; re-execution of M is\n"
            f"    required before this node can pass. M must be an exact node id from the\n"
            f"    analysis plan. Use this only when the fix cannot be made in this node.\n\n"
            f"Render PASS only if this node's subgraph is internally consistent: every\n"
            f"    claim traceable to a node with lineage, every referenced figure present,\n"
            f"    every commitment resolved or downscoped with evidence, and every number\n"
            f"    matching the machine-readable results. Use ITERATE when the graph is\n"
            f"    incomplete but repairable here, and REGRESS(M) when a gap traces back\n"
            f"    upstream.\n\n"
            + (
                "This node is gated on commitments: before rendering a verdict, verify "
                "all commitments in COMMITMENTS.md are either resolved or formally "
                "downscoped. Any pending commitment is automatically Category A."
                if "commitments" in gates_before
                else ""
            ),
            evidence_mandate(plan),
        ],
        model_provider=model_provider,
        model_name=model_name,
    )


#: Reviewer name -> factory. `review_gate` dispatches through this, and
#: `validate_plan` checks a plan's reviewer names against its keys, so a plan
#: naming a reviewer that cannot be built is caught before the run starts.
REVIEWER_FACTORIES = {
    "physics": create_physics_reviewer,
    "critical": create_critical_reviewer,
    "constructive": create_constructive_reviewer,
    "plot": create_plot_validator,
    "bibtex": create_bibtex_validator,
    "rendering": create_rendering_reviewer,
}

REVIEWER_NAMES: frozenset[str] = frozenset(REVIEWER_FACTORIES)
