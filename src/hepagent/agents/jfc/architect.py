"""The architect: propose an analysis plan from the physics prompt.

Runs once, before anything else. It reads the physics prompt and the template
that would otherwise run, and proposes structural edits — split this node per
channel, add a calibration, drop the staged unblinding for a reinterpretation.

The proposal is never trusted. It is applied edit by edit (an edit that cannot be
applied is skipped, not fatal), then validated. Blocking findings go back to the
model for one repair round. A proposal that still does not validate is discarded
and the plain template runs. The worst case is therefore the behaviour we had
before the architect existed, which is what makes it safe to run by default.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from pydantic import BaseModel, Field

from agents import Agent, Runner
from hepagent.agents.common import AgentContext
from hepagent.agents.jfc._data import get_jfc_data_dir
from hepagent.helpers import read_md
from hepagent.model_providers import get_model_provider
from hepagent.plan.edits import OPS, EditReport, PlanEdit, apply_edits
from hepagent.plan.report import to_table
from hepagent.plan.schema import AnalysisPlan
from hepagent.plan.templates import DEFAULT_TEMPLATE, instantiate
from hepagent.plan.validate import validate_plan

_JFC_SRC = get_jfc_data_dir()

#: How much of a node's prompt the architect is shown. Full prompts run to
#: thousands of words each; the architect decides structure, and a first
#: paragraph is enough to know what a node is for. `set_prompt` replaces a whole
#: prompt, so an architect that wants to rewrite one writes it from scratch.
PROMPT_EXCERPT_CHARS = 400


class ProposedEdit(BaseModel):
    """One structural edit, as the model returns it.

    A flat object rather than a discriminated union: unused fields are left null,
    which models emit far more reliably than a nested variant type.
    """

    op: str = Field(description=f"One of: {', '.join(OPS)}")
    node_id: str | None = Field(
        default=None, description="Node being added, removed, split or re-prompted"
    )
    label: str | None = Field(default=None, description="Display name, for add_node")
    directory: str | None = Field(
        default=None, description="Working directory, for add_node. Defaults to node_id"
    )
    artifact: str | None = Field(default=None, description="Primary output filename, for add_node")
    prompt: str | None = Field(
        default=None, description="Whole executor prompt, for add_node and set_prompt"
    )
    like: str | None = Field(
        default=None,
        description="Existing node whose reviewers, contract and gates a new node inherits",
    )
    into: list[str] = Field(
        default_factory=list, description="New node ids, for split_node (at least two)"
    )
    upstream: str | None = Field(default=None, description="Producing node, for edge ops")
    downstream: str | None = Field(default=None, description="Consuming node, for edge ops")
    kind: str | None = Field(default=None, description="requires (blocking) or informs")
    inject: str | None = Field(default=None, description="full, summary or none")


class ArchitectProposal(BaseModel):
    """What the architect returns."""

    rationale: str = Field(
        description="Why the template does or does not fit, citing the physics prompt"
    )
    edits: list[ProposedEdit] = Field(
        default_factory=list, description="Ordered edits. Empty means the template fits."
    )


@dataclass
class ProposalResult:
    """A proposed plan and the record of how it was arrived at.

    Args:
        plan: The plan to run. The plain template when the proposal was discarded.
        rationale: The architect's stated reasoning, empty when it never ran.
        notes: Human-readable trail: edits applied, edits skipped, repair rounds,
            and whether the proposal was accepted.
        accepted: False when the plan is the untouched template.
    """

    plan: AnalysisPlan
    rationale: str = ""
    notes: list[str] = field(default_factory=list)
    accepted: bool = True


def _to_edit(proposed: ProposedEdit) -> PlanEdit:
    return PlanEdit(
        op=(proposed.op or "").strip().lower(),
        node_id=proposed.node_id,
        label=proposed.label,
        directory=proposed.directory,
        artifact=proposed.artifact,
        prompt=proposed.prompt,
        like=proposed.like,
        into=tuple(proposed.into or ()),
        upstream=proposed.upstream,
        downstream=proposed.downstream,
        kind=proposed.kind,
        inject=proposed.inject,
    )


def describe_plan(plan: AnalysisPlan) -> str:
    """Render a plan for the architect: structure in full, prompts in excerpt."""
    lines = [to_table(plan), "", "## Node prompts (excerpt)", ""]
    for node in plan.nodes:
        excerpt = " ".join(node.prompt.split())[:PROMPT_EXCERPT_CHARS]
        gates = ", ".join(f"{g.name}/{g.when}" for g in node.gates if g.enabled) or "none"
        lines += [
            f"### {node.id} — {node.label}",
            f"gates: {gates} | produces_note: {node.produces_note} | arbiter: {node.arbiter}",
            f"{excerpt}…",
            "",
        ]
    return "\n".join(lines)


def _instructions(plan: AnalysisPlan, physics_prompt: str, repair: str = "") -> str:
    role = read_md(_JFC_SRC / "agents" / "architect.md") or (
        "# ARCHITECT ROLE\n\n"
        "You decide the shape of a physics analysis. You are given a template plan "
        "and a physics prompt, and you propose structural edits to the template. "
        "Returning no edits is correct when the template already fits."
    )
    methodology = read_md(_JFC_SRC / "methodology" / "09-multichannel.md")

    sections = [
        f"# ARCHITECT ROLE\n\n{role}",
        f"# PHYSICS PROMPT\n\n{physics_prompt}",
        f"# TEMPLATE PLAN\n\n{describe_plan(plan)}",
    ]
    if methodology:
        sections.append(f"# MULTI-CHANNEL METHODOLOGY\n\n{methodology}")
    if repair:
        sections.append(
            "# YOUR PREVIOUS PROPOSAL DID NOT VALIDATE\n\n"
            "These findings block it. Return a corrected edit list — the full list, "
            "not a patch to it, applied against the same template shown above.\n\n"
            f"{repair}"
        )
    return "\n\n".join(sections)


def _apply_and_validate(
    plan: AnalysisPlan, proposal: ArchitectProposal
) -> tuple[AnalysisPlan, EditReport, list[str]]:
    """Apply a proposal and report what still blocks the result."""
    report = apply_edits(plan, [_to_edit(edit) for edit in proposal.edits])
    findings = validate_plan(report.plan).blocking
    return report.plan, report, [_render(f) for f in findings]


def _render(finding) -> str:
    """One validation finding as a line the architect can act on."""
    where = f" [{finding.node_id}]" if finding.node_id else ""
    return f"{finding.rule}{where}: {finding.message}"


async def propose_plan(
    physics_prompt: str,
    analysis_name: str,
    analysis_type: str = "measurement",
    template: str = DEFAULT_TEMPLATE,
    model_provider: str = "cborg",
    model_name: str | None = None,
    max_turns: int = 20,
) -> ProposalResult:
    """Propose a plan for a physics prompt, falling back to the template.

    Args:
        physics_prompt: The physics question the analysis answers.
        analysis_name: Short name; becomes the plan's name.
        analysis_type: "measurement" or "search".
        template: Built-in template to start from.
        model_provider: Model provider for the architect.
        model_name: Specific model name.
        max_turns: Turn cap for the architect call.

    Returns:
        A `ProposalResult`. Its `plan` is always runnable: on any failure — a
        model error, an unusable proposal, a proposal that will not validate —
        it is the plain template, and `accepted` is False.
    """
    base = instantiate(
        template,
        analysis_name=analysis_name,
        analysis_type=analysis_type,
        physics_prompt=physics_prompt,
    )
    notes: list[str] = []

    agent = Agent[AgentContext](
        name="JFC Architect",
        instructions=_instructions(base, physics_prompt),
        model=get_model_provider(model_provider=model_provider, model_name=model_name),
        output_type=ArchitectProposal,
    )
    context = AgentContext(agent_name="jfc_architect", active_skill="jfc")
    task = (
        "Decide whether the template fits this analysis. Return an empty edit list "
        "if it does, or the smallest set of edits that makes it fit if it does not."
    )

    try:
        result = await Runner.run(agent, task, context=context, max_turns=max_turns)
        proposal = result.final_output
    except Exception as exc:  # noqa: BLE001 - the template is always a usable answer
        notes.append(f"architect failed ({exc}); running the template unchanged")
        return ProposalResult(plan=base, notes=notes, accepted=False)

    if not isinstance(proposal, ArchitectProposal):
        notes.append("architect returned no structured proposal; running the template unchanged")
        return ProposalResult(plan=base, notes=notes, accepted=False)

    if not proposal.edits:
        notes.append("architect proposed no edits: the template fits")
        return ProposalResult(plan=base, rationale=proposal.rationale, notes=notes)

    edited, report, blocking = _apply_and_validate(base, proposal)
    notes.extend(report.applied)
    notes.extend(f"skipped: {note}" for note in report.skipped)

    if blocking:
        notes.append(f"proposal did not validate ({len(blocking)} blocking); asking for a repair")
        repaired = await _repair(
            agent, base, physics_prompt, blocking, context=context, max_turns=max_turns
        )
        if repaired is None:
            notes.append("repair failed; running the template unchanged")
            return ProposalResult(
                plan=base, rationale=proposal.rationale, notes=notes, accepted=False
            )
        edited, report, blocking = _apply_and_validate(base, repaired)
        notes.extend(f"repair: {note}" for note in report.applied)
        notes.extend(f"repair skipped: {note}" for note in report.skipped)
        proposal = repaired

    if blocking:
        notes.append(
            f"repaired proposal still does not validate ({blocking[0]}); "
            f"running the template unchanged"
        )
        return ProposalResult(plan=base, rationale=proposal.rationale, notes=notes, accepted=False)

    notes.append(f"proposal accepted: {len(edited.nodes)} nodes, {len(edited.edges)} edges")
    return ProposalResult(plan=edited, rationale=proposal.rationale, notes=notes)


async def _repair(
    agent: Agent[AgentContext],
    base: AnalysisPlan,
    physics_prompt: str,
    blocking: list[str],
    context: AgentContext,
    max_turns: int,
) -> ArchitectProposal | None:
    """Give the architect its validation findings and one more attempt."""
    findings = "\n".join(f"- {line}" for line in blocking)
    repairer = agent.clone(
        instructions=_instructions(base, physics_prompt, repair=findings),
    )
    try:
        result = await Runner.run(
            repairer,
            "Correct your proposal so it validates. Return the full edit list.",
            context=context,
            max_turns=max_turns,
        )
    except Exception:  # noqa: BLE001 - the caller falls back to the template
        return None
    output = result.final_output
    return output if isinstance(output, ArchitectProposal) else None
