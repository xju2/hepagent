"""Executor, note-writer and typesetter agent factories.

Every factory here is driven by a `PlanNode`: the working directory, the primary
artifact, the prompt the executor runs and the graph write-back contract all come
from the plan rather than from a table in this module. What used to be
`PHASE_SPECS` and `UPSTREAM_ARTIFACTS` is now authored in `plan.json` and read
back through `plan.upstream_edges`, which is also what the graph's dependency
edges are compiled from — so the two still cannot drift.
"""

from __future__ import annotations

import logging
from pathlib import Path

from agents import Agent
from hepagent.agent_helpers import update_logbook
from hepagent.agents.bash import execute_bash_command_with_confirmation
from hepagent.agents.common import AgentContext
from hepagent.agents.jfc._data import get_jfc_data_dir
from hepagent.helpers import read_md
from hepagent.model_providers import get_model_provider
from hepagent.plan.schema import AnalysisPlan, PlanNode
from hepagent.plan.store import resolve_plan
from hepagent.tools.common import ask_user_for_info, read_resource, render_skill, web_search
from hepagent.tools.jfc import get_jfc_tools

logger = logging.getLogger(__name__)

_JFC_SRC = get_jfc_data_dir()

#: How much of an upstream artifact an `inject="summary"` edge contributes.
SUMMARY_CHARS = 1500


def executor_tools() -> list:
    """Every function tool a plan node's executor may be given.

    The full set is the default; `PlanNode.tools` narrows it by name. This is
    also the catalog the plan editor offers, via
    `hepagent.agents.jfc.capabilities`, so the names a user can pick and the
    tools that actually exist cannot drift apart.
    """
    return [
        execute_bash_command_with_confirmation,
        read_resource,
        update_logbook,
        ask_user_for_info,
        web_search,
        *get_jfc_tools(),
    ]


def _tools_for(node: PlanNode) -> list:
    """Apply a node's tool allowlist.

    An unknown name is logged and skipped rather than raised: `validate_plan` is
    where a user hears about it, and a run must not die because a plan outlived
    a tool rename. An empty allowlist really does mean no tools — that is a
    different statement from `tools=None`, which means the default set.
    """
    available = executor_tools()
    if node.tools is None:
        return available
    wanted = list(node.tools)
    by_name = {tool.name: tool for tool in available}
    unknown = [name for name in wanted if name not in by_name]
    if unknown:
        logger.warning(
            "Plan node '%s' allowlists unknown tool(s): %s. Available: %s",
            node.id,
            ", ".join(unknown),
            ", ".join(sorted(by_name)),
        )
    return [by_name[name] for name in wanted if name in by_name]


def _skills_section(node: PlanNode) -> str:
    """Render the skills a node declares, straight into its system prompt.

    A plan node names its skills up front, so unlike the interactive agent it
    has no reason to discover and activate one mid-run — the instructions are
    simply present from the first turn.
    """
    blocks = []
    for name in node.skills:
        body = render_skill(name)
        if body is None:
            logger.warning("Plan node '%s' declares unknown skill '%s'.", node.id, name)
            continue
        blocks.append(body)
    if not blocks:
        return ""
    return "# ACTIVE SKILLS\n\nFollow these standard operating procedures.\n\n" + "\n\n".join(
        blocks
    )


def _read_jfc_file(relative: str) -> str:
    path = _JFC_SRC / relative
    return read_md(path)


def _read_upstream_artifacts(plan: AnalysisPlan, node: PlanNode, analysis_root: Path) -> str:
    """Render the upstream context for a node's prompt.

    Two kinds of input arrive here. Upstream *artifacts* come from the plan's
    edges and honour each edge's `inject` mode, so a plan can hand a node a full
    document, a summary, or nothing but the dependency. Ambient *context paths*
    are read whole: they belong to the node, not to one of its dependencies, and
    are optional by construction — a file that is not there yet is simply absent.
    """
    blocks: list[str] = []
    seen: set[str] = set()

    for edge in plan.upstream_edges(node.id):
        if edge.inject == "none":
            continue
        upstream = plan.node(edge.upstream)
        if upstream is None or upstream.artifact_path in seen:
            continue
        content = read_md(analysis_root / upstream.artifact_path)
        if not content:
            continue
        seen.add(upstream.artifact_path)
        if edge.inject == "summary" and len(content) > SUMMARY_CHARS:
            content = (
                content[:SUMMARY_CHARS]
                + f"\n\n[summary: first {SUMMARY_CHARS} characters of "
                + f"{len(content)}; read the file for the rest]"
            )
        blocks.append(f"### {upstream.artifact_path}\n\n{content}")

    for rel in node.context_paths:
        if rel in seen:
            continue
        content = read_md(analysis_root / rel)
        if content:
            seen.add(rel)
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


def _graph_contract_section(node: PlanNode, analysis_root: Path) -> str:
    """Render the node's graph write-back contract for the executor prompt."""
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
        f"Your node id is `{node.id}` — pass it as the `node_id` argument.\n\n"
        f"{contract_summary(node)}\n\n"
        f"Calls outside this contract are refused. Use `graph_query` to inspect "
        f"existing nodes before linking to them — edges need real node ids on "
        f"both ends. Every major claim in your artifact should be reachable from "
        f"a `supports` or `derives_from` edge."
    )


def _process_inventory_section(node: PlanNode, plan: AnalysisPlan, analysis_root: Path) -> str:
    """Tell a node either to record the process inventory, or what it says.

    Which of the two a node gets is decided by its write-back contract, not by
    its id: the node allowed to create `process` and `dataset` graph nodes is the
    node that owns the inventory. Every other node is handed the inventory that
    exists, so a downstream executor never re-derives the background list from
    prose — that is the whole point of writing it down once.
    """
    from hepagent.agents.jfc.processes import (
        BACKGROUND_CATEGORIES,
        find_inventory,
        inventory_owner,
    )

    try:
        existing, owner = find_inventory(analysis_root, plan)
    except Exception:  # noqa: BLE001 - a broken inventory must not block the run
        existing, owner = None, None

    if inventory_owner(plan) is node:
        return (
            "# PROCESS INVENTORY (REQUIRED)\n\n"
            "Before you finish, record the processes this analysis models with\n"
            '`record_process_inventory`, passing `node_id="' + node.id + '"`. This is '
            "the machine-readable\ncounterpart of the sample inventory in your artifact, "
            "and every later node reads\nit instead of re-reading your prose.\n\n"
            "It must contain:\n"
            "- the signal process(es);\n"
            "- every background, each classified as "
            + ", ".join(BACKGROUND_CATEGORIES)
            + ",\n  with a one-line rationale for the classification and how you will "
            "estimate it;\n"
            "- the observed data as a `data` process;\n"
            "- for each of them, the dataset(s) that carry it. Take the datasets from the\n"
            '  physics prompt where it names them, recording `source: "prompt"` and the '
            "path\n  exactly as given. Where the prompt does not name one, say so with\n"
            '  `source: "inferred"` rather than inventing a path that looks official.\n\n'
            "The tool validates the document and refuses an invalid one with the reasons;\n"
            "fix them and call it again. Recording it is not optional — the progress panel\n"
            "and the downstream nodes read this file, not the artifact."
        )

    if existing is None:
        return ""
    return (
        "# PROCESS INVENTORY\n\n"
        f"The strategy (node `{owner.id if owner else '?'}`) recorded these processes and "
        "datasets.\nUse these ids, labels and paths as given rather than inventing your own; "
        "call\n`read_process_inventory` for the full detail.\n\n" + existing.summary()
    )


def _assemble_executor_prompt(
    node: PlanNode,
    plan: AnalysisPlan,
    analysis_root: Path,
    codesign_feedback: str | None = None,
) -> str:
    parts = []

    # 1. Role definition
    executor_role = _read_jfc_file("agents/executor.md")
    if executor_role:
        parts.append(f"# EXECUTOR ROLE DEFINITION\n\n{executor_role}")

    # 2. The node's own specification, straight from the plan
    if node.prompt:
        parts.append(f"# PHASE SPECIFICATION\n\n{node.prompt}")

    # 3. Physics prompt
    prompt_path = analysis_root / "prompt.md"
    physics_prompt = read_md(prompt_path)
    if physics_prompt:
        parts.append(f"# PHYSICS PROMPT\n\n{physics_prompt}")

    # 4. Upstream artifacts
    upstream = _read_upstream_artifacts(plan, node, analysis_root)
    if upstream:
        parts.append(upstream)

    # 4b. Skills the node declares
    skills = _skills_section(node)
    if skills:
        parts.append(skills)

    # 5. Working directory instruction
    outputs_dir = analysis_root / node.outputs_dir
    src_dir = analysis_root / node.directory / "src"
    parts.append(
        f"# WORKING DIRECTORY\n\n"
        f"Write all outputs to: `{outputs_dir}/`\n"
        f"Primary artifact: `{analysis_root / node.artifact_path}`\n"
        f"Write analysis code to: `{src_dir}/`\n"
        f"Write figures to: `{outputs_dir}/figures/`\n"
        f"Append to experiment log: `{analysis_root}/experiment_log.md`\n"
        f"Analysis root: `{analysis_root}/`"
    )

    # 6. Graph write-back contract
    parts.append(_graph_contract_section(node, analysis_root))

    # 6b. The process inventory: recorded by the node that owns it, injected
    # into every other node that has to work per-process.
    inventory = _process_inventory_section(node, plan, analysis_root)
    if inventory:
        parts.append(inventory)

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
    node: PlanNode,
    analysis_root: Path,
    plan: AnalysisPlan | None = None,
    model_provider: str = "cborg",
    model_name: str | None = None,
    codesign_feedback: str | None = None,
) -> Agent[AgentContext]:
    """
    Return a role agent configured to execute one node of the analysis plan.

    Assembles the system prompt from executor.md, the node's own prompt, the
    physics prompt, the upstream artifacts the plan's edges declare, and the
    skills it names. The tool set is `executor_tools()` narrowed by the node's
    allowlist. `node.mcp_servers` is *not* read here: nothing in hepagent
    connects to an MCP server yet, so the plan records the selection and the
    runtime ignores it.

    Args:
        node: The plan node to execute.
        analysis_root: Path to the analysis root directory.
        plan: The analysis plan. Read from `plan.json` when omitted.
        model_provider: Model provider name (default "cborg").
        model_name: Specific model name (default: provider's default).
        codesign_feedback: Human feedback from the codesign gate. When provided,
            appended as a mandatory revision directive to the executor prompt.
    """
    plan = resolve_plan(analysis_root, plan)
    instructions = _assemble_executor_prompt(
        node,
        plan,
        analysis_root,
        codesign_feedback=codesign_feedback,
    )
    provider, name = _model_for(node, model_provider, model_name)

    return Agent[AgentContext](
        name=f"JFC Executor ({node.label})",
        instructions=instructions,
        model=get_model_provider(model_provider=provider, model_name=name),
        tools=_tools_for(node),
    )


def _model_for(
    node: PlanNode,
    model_provider: str,
    model_name: str | None,
) -> tuple[str, str | None]:
    """Apply a node's `"provider:model"` override, falling back to the run's model.

    Three shapes, and the colon is what tells them apart — the same reading the
    CLI's `parse_model_spec` uses, so a spec typed at a prompt and one picked in
    the plan editor mean the same thing:

    * ``"model"`` — that model on whatever platform the run chose.
    * ``"provider:model"`` — both overridden.
    * ``"provider:"`` — that platform, at its own default model. This is what the
      editor writes when a user picks a platform and leaves the model alone, so
      it must not be read as a model literally named ``"provider"``.
    """
    if not node.model:
        return model_provider, model_name
    provider, sep, name = node.model.partition(":")
    if not sep:
        return model_provider, node.model
    return provider, name or None


def create_note_writer(
    node: PlanNode,
    analysis_root: Path,
    plan: AnalysisPlan | None = None,
    model_provider: str = "cborg",
    model_name: str | None = None,
) -> Agent[AgentContext]:
    """
    Return a role agent that writes the analysis note for one plan node.

    The note writer reads every artifact the analysis has produced so far and
    emits the markdown AN. No bash execution tools — pure prose generation.

    Args:
        node: The plan node whose note is being written. Its `note_path` is the
            output, which is the node's own artifact unless it names a separate
            `note_artifact`.
        analysis_root: Path to the analysis root directory.
        plan: The analysis plan. Read from `plan.json` when omitted.
        model_provider: Model provider name.
        model_name: Specific model name.
    """
    plan = resolve_plan(analysis_root, plan)
    role_def = _read_jfc_file("agents/note_writer.md")
    an_target = node.note_path

    # Every artifact the analysis has produced, in plan order, plus the ambient
    # context files this node reads. The note that is about to be written is
    # excluded — it is the output, not an input.
    artifact_paths = [n.artifact_path for n in plan.nodes if n.artifact_path != an_target]
    artifact_paths += [p for p in node.context_paths if p not in artifact_paths]

    artifact_blocks = []
    for rel in artifact_paths:
        content = read_md(analysis_root / rel)
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
        f"Write the complete analysis note for {node.label}.\n"
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
    provider, name = _model_for(node, model_provider, model_name)

    from hepagent.tools.jfc.graph import graph_query

    return Agent[AgentContext](
        name=f"JFC Note Writer ({node.label})",
        instructions=instructions,
        model=get_model_provider(model_provider=provider, model_name=name),
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
