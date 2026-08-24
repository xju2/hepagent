"""Orchestration engine: run the nodes of an analysis plan, with review.

The plan says what the nodes are; the graph says which of them can run now. This
module walks the second, re-asking after every node so a regression that
un-completes work simply makes that work runnable again.

Gates are node attributes rather than branches. `_run_gates` dispatches on
`PlanGate.name` at the position the node declares, which is why there is no
`if node.id == "inference_expected"` anywhere below.
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable, Iterable, Iterator
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Literal

from agents import Runner
from hepagent.agents.common import AgentContext
from hepagent.agents.jfc.capabilities import plan_vocabulary
from hepagent.agents.jfc.codesign import run_codesign_gate
from hepagent.agents.jfc.commitment_checker import (
    CommitmentsNotResolved,
    check_phase1_commitments,
)
from hepagent.agents.jfc.condition import (
    ConditionOutcome,
    evaluate_condition,
    exhaust,
    record_evaluation,
)
from hepagent.agents.jfc.executor import (
    create_note_writer,
    create_phase_executor,
    create_typesetter,
)
from hepagent.agents.jfc.fixer import run_fixer
from hepagent.agents.jfc.graph_builder import (
    bootstrap_graph,
    ingest_condition,
    ingest_node,
    ingest_review,
)
from hepagent.agents.jfc.investigator import RegressionTicket, run_investigator
from hepagent.agents.jfc.planner import next_phase
from hepagent.agents.jfc.review_gate import (
    PhaseEscalationError,
    PhaseRegressionError,
    ReviewGateResult,
    run_review_gate,
)
from hepagent.graph.store import AnalysisGraph
from hepagent.graph.validation import validate_commitments
from hepagent.interaction import current_backend
from hepagent.plan.schema import AnalysisPlan, PlanNode
from hepagent.plan.service import APPROVAL_GATE, PlanApprovalGate
from hepagent.plan.store import has_plan, load_plan, save_plan
from hepagent.plan.templates import DEFAULT_TEMPLATE, instantiate
from hepagent.plan.validate import validate_plan
from hepagent.tools.jfc.scaffold import _scaffold_impl as scaffold_jfc_analysis


class MaxIterationsExceeded(Exception):
    """Raised when a node fails review more than max_iterations_per_phase times."""

    def __init__(self, phase: str, iterations: int):
        self.phase = phase
        self.iterations = iterations
        super().__init__(
            f"Node {phase} exceeded {iterations} review iterations without PASS. "
            f"Human intervention required. Resume with start_from_phase={phase}."
        )


class NodeBudgetSpent(MaxIterationsExceeded):
    """Raised when a node's review budget was spent *before* this run started.

    `run_phase_with_review` iterates `range(spent, limit)`, so a node whose
    counter already sits at or above its limit gets an empty loop: nothing runs
    and the plain `MaxIterationsExceeded` at the bottom of the function would
    blame a review that never happened. This says what actually happened
    instead. It subclasses `MaxIterationsExceeded` so every existing handler
    still catches it.
    """

    def __init__(self, phase: str, spent: int, limit: int):
        self.phase = phase
        self.iterations = limit
        self.spent = spent
        Exception.__init__(
            self,
            f"Node {phase} has already spent {spent} review iteration(s) and its "
            f"plan allows {limit}, so nothing would run. Raise the node's "
            f"max_iterations (or the run's), or run the node from scratch - the "
            f"plan page's per-node Run button clears its counter.",
        )


@dataclass
class JFCOrchestrationState:
    """Durable run state. Node ids throughout — the plan owns the structure."""

    analysis_root: str
    analysis_name: str
    analysis_type: Literal["measurement", "search"]
    current_node: str = ""
    max_iterations_per_phase: int = 3
    model_provider: str = "cborg"
    model_name: str | None = None
    completed_nodes: list[str] = field(default_factory=list)
    phase_iterations: dict[str, int] = field(default_factory=dict)
    #: Nodes a condition routed past. Durable, unlike a node merely not run yet:
    #: the branch was decided, and downstream work must stop waiting on it.
    skipped_nodes: list[str] = field(default_factory=list)
    #: Evaluations spent per condition node — the loop budget, tracked across
    #: resumes so a restart cannot silently refill it.
    condition_iterations: dict[str, int] = field(default_factory=dict)
    #: Metric values each condition has read, oldest first, for `improvement_*`.
    condition_history: dict[str, list[float]] = field(default_factory=dict)

    def model_dump_json(self) -> str:
        return json.dumps(asdict(self), indent=2)

    @classmethod
    def model_validate_json(cls, data: str, root: Path | None = None) -> JFCOrchestrationState:
        """Read state, dropping keys this version no longer knows about.

        `root` supplies `analysis_root` when the file omits it, which is what
        lets a hand-written or migrated state file load.
        """
        known = set(cls.__dataclass_fields__)
        fields = {k: v for k, v in json.loads(data).items() if k in known}
        if root is not None:
            fields.setdefault("analysis_root", str(root))
            fields.setdefault("analysis_name", Path(root).name)
        return cls(**fields)

    @property
    def root(self) -> Path:
        return Path(self.analysis_root)


def save_state(state: JFCOrchestrationState) -> None:
    path = state.root / ".orchestration_state.json"
    path.write_text(state.model_dump_json(), encoding="utf-8")


def load_state(analysis_root: Path) -> JFCOrchestrationState:
    path = analysis_root / ".orchestration_state.json"
    return JFCOrchestrationState.model_validate_json(
        path.read_text(encoding="utf-8"), root=analysis_root
    )


def update_graph(
    analysis_root: Path,
    node: PlanNode,
    stage: Literal["phase", "review", "condition"],
    plan: AnalysisPlan | None = None,
    progress_callback: Callable[[str, str], None] | None = None,
) -> None:
    """Fold a node's outputs, its review round or its branch decision into the graph.

    Graph ingestion is bookkeeping, not analysis: a failure here must never take
    down a run that is otherwise progressing, so this swallows exceptions the
    same way `git_commit_phase` does and reports them through the callback.
    """
    ingesters = {
        "phase": ingest_node,
        "review": ingest_review,
        "condition": ingest_condition,
    }
    try:
        report = ingesters[stage](analysis_root, node.id, plan)
    except Exception as exc:  # noqa: BLE001 - bookkeeping must not abort a run
        if progress_callback:
            progress_callback(node.id, f"graph {stage} ingestion failed: {exc}")
        return
    if progress_callback:
        progress_callback(node.id, f"graph updated ({stage}): {report.summary()}")


def _nodes_before(plan: AnalysisPlan, start_from: str | None) -> set[str]:
    """Node ids the plan declares before an explicit resume point.

    Resuming at a mid-plan node is an assertion that everything upstream of it is
    done; the planner needs that stated or it reports the node as blocked.
    """
    if not start_from:
        return set()
    ids = list(plan.node_ids())
    if start_from not in ids:
        return set()
    return set(ids[: ids.index(start_from)])


def _phase_sequence(
    analysis_root: Path,
    state: JFCOrchestrationState,
    plan: AnalysisPlan,
    assumed: set[str],
    progress_callback: Callable[[str, str], None] | None = None,
    attempted: set[str] | None = None,
) -> Iterator[PlanNode]:
    """Yield nodes to run, re-reading the graph frontier after each one.

    The order is derived from `requires` edges rather than declared, so a node
    becomes runnable exactly when its prerequisites are complete — including
    after a regression cycle has un-completed part of the analysis.

    Args:
        attempted: Nodes already offered, owned by the caller. A loop rewinds by
            removing its body from this set as well as from `completed_nodes`;
            keeping the set private here would mean a node could be offered only
            once per run and no loop could ever take a second pass.
    """
    if attempted is None:
        attempted = set()

    while True:
        satisfied = set(state.completed_nodes) | assumed
        try:
            graph = AnalysisGraph.load(analysis_root)
            node_id = next_phase(
                graph, satisfied, skip=attempted, plan=plan, skipped=state.skipped_nodes
            )
        except Exception as exc:  # noqa: BLE001 - fall back rather than abort
            if progress_callback:
                progress_callback("planner", f"graph planning failed ({exc}); using plan order")
            node_id = _fallback_next(plan, satisfied, attempted | set(state.skipped_nodes))

        if node_id is None:
            return

        attempted.add(node_id)
        node = plan.node(node_id)
        if node is None:
            if progress_callback:
                progress_callback(
                    "planner", f"graph names node '{node_id}', which the plan does not"
                )
            continue
        yield node


def _fallback_next(plan: AnalysisPlan, done: set[str], attempted: set[str]) -> str | None:
    """Next node by the plan's declaration order, when the graph cannot be read."""
    for node_id in plan.node_ids():
        if node_id not in done and node_id not in attempted:
            return node_id
    return None


def _ensure_graph(
    analysis_root: Path,
    plan: AnalysisPlan,
    progress_callback: Callable[[str, str], None] | None = None,
) -> None:
    """Seed the analysis graph if this directory does not have one yet."""
    if (analysis_root / "graph" / "nodes.jsonl").exists():
        return
    try:
        bootstrap_graph(analysis_root, plan)
    except Exception as exc:  # noqa: BLE001 - bookkeeping must not abort a run
        if progress_callback:
            progress_callback("graph", f"bootstrap failed: {exc}")
        return
    if progress_callback:
        progress_callback("graph", "seeded analysis graph")


def _graph_commitment_findings(analysis_root: Path) -> list[str]:
    """Return graph-level commitment problems blocking a commitment-gated node.

    This is a second, independent check alongside `check_phase1_commitments`:
    the markdown table says what a commitment's status *is*, the graph says
    which evidence actually closed it. A commitment marked resolved with no
    `resolves` edge is caught here and nowhere else.

    Returns an empty list when the graph is unreadable — the markdown check
    remains the blocking authority.
    """
    try:
        report = validate_commitments(AnalysisGraph.load(analysis_root))
    except Exception:  # noqa: BLE001 - never let bookkeeping block on its own failure
        return []
    return [f.message for f in report.errors]


def git_commit_phase(analysis_root: Path, phase: str, message: str) -> None:
    try:
        subprocess.run(["git", "add", "-A"], cwd=analysis_root, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", f"phase/{phase}: {message}"],
            cwd=analysis_root,
            capture_output=True,
        )
    except Exception:
        pass  # git is optional


async def _run_executor(
    state: JFCOrchestrationState,
    node: PlanNode,
    plan: AnalysisPlan,
    progress_callback: Callable[[str, str], None] | None,
    max_turns: int = 50,
    codesign_feedback: str | None = None,
) -> None:
    """Run the executor agent for one plan node."""
    if progress_callback:
        progress_callback(node.id, "executor starting")

    executor = create_phase_executor(
        node,
        state.root,
        plan,
        model_provider=state.model_provider,
        model_name=state.model_name,
        codesign_feedback=codesign_feedback,
    )
    context = AgentContext(agent_name="jfc_executor", active_skill="jfc")
    task = (
        f"Execute the '{node.id}' node of the analysis at {state.root}. "
        f"Read your system prompt for full instructions. "
        f"Produce the primary artifact to the outputs/ directory."
    )
    await Runner.run(executor, task, context=context, max_turns=max_turns)

    if progress_callback:
        progress_callback(node.id, "executor complete")


async def _run_note_writer_and_typesetter(
    state: JFCOrchestrationState,
    node: PlanNode,
    plan: AnalysisPlan,
    progress_callback: Callable[[str, str], None] | None,
    max_turns: int = 30,
) -> Path | None:
    """Write and typeset the analysis note for a node that declares one."""
    if progress_callback:
        progress_callback(node.id, "writing analysis note")

    note_writer = create_note_writer(
        node,
        state.root,
        plan,
        model_provider=state.model_provider,
        model_name=state.model_name,
    )
    context = AgentContext(agent_name="jfc_note_writer", active_skill="jfc")
    an_path = state.root / node.note_path
    task = (
        f"Write the analysis note for '{node.id}' to {an_path}. "
        f"Read all available artifacts from {state.root}."
    )
    await Runner.run(note_writer, task, context=context, max_turns=max_turns)

    if progress_callback:
        progress_callback(node.id, "typesetting analysis note")

    typesetter = create_typesetter(
        state.root,
        model_provider=state.model_provider,
        model_name=state.model_name,
    )
    pdf_path = state.root / node.note_pdf_path
    type_context = AgentContext(agent_name="jfc_typesetter", active_skill="jfc")
    type_task = (
        f"Compile the analysis note at {an_path} to PDF at {pdf_path}. "
        f"Run pandoc → postprocess_tex.py → tectonic. Read and verify the PDF output."
    )
    await Runner.run(typesetter, type_task, context=type_context, max_turns=max_turns)

    return pdf_path if pdf_path.exists() else None


async def _human_gate(node: PlanNode, pdf_path: Path | None) -> bool:
    """Ask a person to approve what a node produced. True when they approve."""
    pdf_str = str(pdf_path) if pdf_path else "(PDF compilation failed — check logs)"

    # A run launched from the plan page has no terminal: the gate is asked there
    # instead, as two buttons. See `hepagent.interaction`.
    backend = current_backend()
    if backend is not None:
        answer = backend.ask(
            f"{node.label} is complete. Draft analysis note PDF: {pdf_str}\n\n"
            "Review the PDF, then APPROVE to continue or ITERATE to send the node back.",
            thought=f"Human gate after node '{node.id}'.",
            choices=("APPROVE", "ITERATE"),
        )
        return answer.strip().upper().startswith("APPROVE")

    print(
        f"\n{'=' * 60}\n"
        f"HUMAN GATE: {node.label} complete\n"
        f"{'=' * 60}\n"
        f"Draft analysis note PDF: {pdf_str}\n\n"
        f"Review the PDF and respond:\n"
        f"  APPROVE  — continue to the next node\n"
        f"  ITERATE  — return to '{node.id}' for fixes\n"
    )
    try:
        response = input("Your response: ").strip().upper()
    except (EOFError, OSError):
        response = ""

    return response.startswith("APPROVE")


def _commitment_gate(analysis_root: Path) -> None:
    """Block until every commitment is closed. Raises `CommitmentsNotResolved`.

    Two independent authorities have to agree: the markdown table says what a
    commitment's status *is*, the graph says which evidence actually closed it.
    """
    result = check_phase1_commitments(analysis_root)
    graph_findings = _graph_commitment_findings(analysis_root)
    if result.all_resolved and not graph_findings:
        return
    if graph_findings:
        result.blocking_message = "\n".join(
            filter(
                None,
                [
                    result.blocking_message or "Blocked by the analysis graph:",
                    "",
                    "Graph commitment findings:",
                    *(f"  - {f}" for f in graph_findings),
                ],
            )
        )
    raise CommitmentsNotResolved(result)


def _turns_for(node: PlanNode, run_max_turns: int | None, role_default: int) -> int:
    """The turn cap for one agent call on `node`.

    Precedence is narrowest-first: the node's own cap, then the cap the run was
    launched with, then the role's default. A node that needs a long leash — a
    selection step sweeping cuts, say — says so in the plan without raising the
    ceiling for every other node, and a run-wide `--max-turns` still overrides
    the role defaults for every node that declares nothing.
    """
    if node.max_turns is not None:
        return node.max_turns
    if run_max_turns is not None:
        return run_max_turns
    return role_default


async def _run_gates(
    state: JFCOrchestrationState,
    node: PlanNode,
    plan: AnalysisPlan,
    when: Literal["before", "after"],
    progress_callback: Callable[[str, str], None] | None = None,
    max_turns: int | None = None,
) -> Literal["continue", "rerun"]:
    """Run the gates a node declares at this position.

    Returns "rerun" when a gate concluded the node has to be redone, which is how
    the codesign gate's REVISE verdict gets back into the loop without the
    orchestrator knowing what codesign is.

    Raises:
        CommitmentsNotResolved: the commitment gate found open commitments.
        PhaseEscalationError: a human declined to approve the node's output.
    """
    for gate in node.gates_at(when):
        if progress_callback:
            progress_callback(node.id, f"gate {gate.name} ({when})")

        if gate.name == "commitments":
            _commitment_gate(state.root)

        elif gate.name == "human":
            pdf_path = state.root / node.note_pdf_path if node.produces_note else None
            if not await _human_gate(node, pdf_path if pdf_path and pdf_path.exists() else None):
                raise PhaseEscalationError(
                    node.id,
                    ReviewGateResult(
                        verdict="ESCALATE",
                        category_a_findings=[f"Human did not approve the '{node.id}' output"],
                    ),
                )

        elif gate.name == "codesign":
            verdict = await run_codesign_gate(
                state.root,
                model_provider=state.model_provider,
                model_name=state.model_name,
                max_turns=_turns_for(node, max_turns, 20),
                progress_callback=progress_callback,
            )
            if verdict == "REVISE":
                if progress_callback:
                    progress_callback(node.id, "codesign REVISE — re-running with human feedback")
                return "rerun"

    return "continue"


def _codesign_feedback(analysis_root: Path) -> str | None:
    """The human feedback the codesign gate recorded, if any."""
    path = analysis_root / "phase1_strategy" / "codesign" / "HUMAN_FEEDBACK.md"
    return path.read_text(encoding="utf-8") if path.exists() else None


async def _run_regression_cycle(
    state: JFCOrchestrationState,
    plan: AnalysisPlan,
    err: PhaseRegressionError,
    progress_callback: Callable[[str, str], None] | None,
    max_turns: int | None = None,
    attempted: set[str] | None = None,
) -> None:
    """
    Handle a regression verdict:
    1. Run the investigator to produce REGRESSION_TICKET.md.
    2. Determine which nodes (origin + affected downstream) must be re-run.
    3. Remove those nodes from `completed_nodes` and re-run them in plan order.

    The fallback when the ticket names no nodes is the plan's *descendants* of
    the origin, intersected with the ancestors of the detected node. A linear
    plan makes that the old `origin..detected` slice; a branching plan makes it
    the correct answer rather than an arbitrary one.
    """
    detected = str(err.detected_phase)
    origin = str(err.origin_phase) if err.origin_phase else ""

    if progress_callback:
        progress_callback(detected, f"regression → investigating (origin node {origin})")

    detected_node = plan.node(detected)
    investigator_turns = (
        _turns_for(detected_node, max_turns, 20)
        if detected_node is not None
        else (max_turns if max_turns is not None else 20)
    )
    ticket: RegressionTicket = await run_investigator(
        detected_phase=detected,
        origin_phase=origin,
        symptom=err.symptom,
        analysis_root=state.root,
        plan=plan,
        model_provider=state.model_provider,
        model_name=state.model_name,
        max_turns=investigator_turns,
    )

    known = set(plan.node_ids())
    affected = {p for p in ticket.affected_phases if p in known}
    if not affected:
        origin_id = str(ticket.origin_phase or origin)
        affected = _between(plan, origin_id, detected)
    if detected in known:
        affected.add(detected)

    order = {node_id: index for index, node_id in enumerate(plan.node_ids())}
    nodes_to_rerun = sorted(affected, key=lambda n: order.get(n, len(order)))

    if progress_callback:
        progress_callback(detected, f"regression cycle: re-running {nodes_to_rerun}")

    for node_id in nodes_to_rerun:
        if node_id in state.completed_nodes:
            state.completed_nodes.remove(node_id)
        if attempted is not None:
            attempted.discard(node_id)
    save_state(state)

    git_commit_phase(
        state.root,
        detected,
        f"regression detected (origin node {origin}) — rewinding",
    )

    for node_id in nodes_to_rerun:
        node = plan.node(node_id)
        # A condition is evaluated, not executed. Un-completing it above is
        # enough — the sequence offers it again once its body has re-run.
        if node is not None and node.kind != "condition":
            await run_phase_with_review(state, node, plan, progress_callback, max_turns=max_turns)


def _between(plan: AnalysisPlan, origin: str, detected: str) -> set[str]:
    """Nodes downstream of `origin` and upstream of `detected`, inclusive.

    Replaces the `PHASE_ORDER[origin:detected+1]` slice, which assumed the plan
    was a total order. On a branching plan a slice would sweep in siblings that
    the regression never touched.
    """
    if origin not in set(plan.node_ids()):
        return {detected} if detected in set(plan.node_ids()) else set()

    downstream = _reach(plan, origin, forward=True)
    upstream = _reach(plan, detected, forward=False)
    return (downstream & upstream) | {origin}


def _reach(plan: AnalysisPlan, start: str, *, forward: bool) -> set[str]:
    """Everything reachable from `start` along blocking edges, including itself."""
    seen = {start}
    frontier = [start]
    while frontier:
        current = frontier.pop()
        edges = (
            plan.downstream_edges(current, kind="requires")
            if forward
            else plan.upstream_edges(current, kind="requires")
        )
        for edge in edges:
            nxt = edge.downstream if forward else edge.upstream
            if nxt not in seen:
                seen.add(nxt)
                frontier.append(nxt)
    return seen


async def run_condition_node(
    state: JFCOrchestrationState,
    node: PlanNode,
    plan: AnalysisPlan,
    attempted: set[str],
    progress_callback: Callable[[str, str], None] | None = None,
    max_turns: int | None = None,
) -> None:
    """Evaluate a condition node and route the analysis along one of its branches.

    A condition is evaluated, not executed: no executor, no note writer, and no
    review gate. That bypass has to be explicit — `reviewers_for` deliberately
    hands a node declaring no reviewers the critical reviewer, so an empty panel
    would not have been enough.

    Taking a **back** branch rewinds: the loop body is dropped from
    `completed_nodes` *and* from `attempted`, which is what lets the planner
    offer it again. Taking a **forward** branch skips whatever the other branch
    exclusively leads to, so a node that re-joins downstream stops waiting on
    work the analysis decided not to do.

    Raises:
        PhaseEscalationError: the budget ran out and the plan asked for a human.
    """
    limit = node.condition.max_iterations if node.condition else 1
    history = list(state.condition_history.get(node.id, []))
    iteration = state.condition_iterations.get(node.id, 0) + 1

    if progress_callback:
        progress_callback(node.id, f"evaluating condition (iteration {iteration}/{limit})")

    outcome = await evaluate_condition(
        node,
        state.root,
        plan,
        iteration=iteration,
        history=history,
        model_provider=state.model_provider,
        model_name=state.model_name,
        max_turns=_turns_for(node, max_turns, 10),
    )
    state.condition_iterations[node.id] = iteration
    if outcome.metric_value is not None:
        history.append(outcome.metric_value)
        state.condition_history[node.id] = history

    outcome, target = _apply_budget(plan, node, outcome, iteration, limit, progress_callback)
    record_evaluation(state.root, node, iteration, outcome, target)

    if node.id not in state.completed_nodes:
        state.completed_nodes.append(node.id)
    save_state(state)

    update_graph(state.root, node, "condition", plan, progress_callback)

    if progress_callback:
        progress_callback(
            node.id,
            f"condition {outcome.branch} via {outcome.source} → "
            f"{target or 'nowhere'}: {_one_line(outcome.rationale)}",
        )

    if outcome.escalate:
        raise PhaseEscalationError(
            node.id,
            ReviewGateResult(verdict="ESCALATE", category_a_findings=[outcome.rationale]),
        )

    if target is None:
        return

    if _is_back_branch(plan, node.id, target, outcome.branch):
        _rewind_loop(state, plan, node, target, attempted, progress_callback)
    else:
        _skip_untaken_branch(state, plan, node, target, progress_callback)
    save_state(state)


def _apply_budget(
    plan: AnalysisPlan,
    node: PlanNode,
    outcome: ConditionOutcome,
    iteration: int,
    limit: int,
    progress_callback: Callable[[str, str], None] | None,
) -> tuple[ConditionOutcome, str | None]:
    """Refuse a rewind that would exceed the loop's budget.

    `max_iterations` bounds how many times the **body runs**, which is the cost a
    plan author is choosing. The condition is still evaluated on the final pass —
    the reading is cheap and the decision record should say what the metric
    actually showed when the budget, rather than convergence, ended the loop.
    """
    target = _branch_target(plan, node, outcome.branch)
    if target is None or not _is_back_branch(plan, node.id, target, outcome.branch):
        return outcome, target
    if iteration < limit:
        return outcome, target

    exhausted = exhaust(node)
    fallback = _branch_target(plan, node, exhausted.branch)
    if fallback is not None and _is_back_branch(plan, node.id, fallback, exhausted.branch):
        # The plan's own exhaustion branch loops as well. Honouring it would run
        # forever, which is the one outcome the budget exists to rule out.
        if progress_callback:
            progress_callback(
                node.id,
                f"on_exhaustion '{exhausted.branch}' also loops back; stopping here instead",
            )
        return exhausted, None
    return exhausted, fallback


def _branch_target(plan: AnalysisPlan, node: PlanNode, branch: str) -> str | None:
    """The node a condition routes to for this outcome, or None if it declares none."""
    kind = "on_true" if branch == "true" else "on_false"
    for edge in plan.branch_edges(node.id):
        if edge.kind == kind:
            return edge.downstream
    return None


def _is_back_branch(plan: AnalysisPlan, condition_id: str, target: str, branch: str) -> bool:
    kind = "on_true" if branch == "true" else "on_false"
    return (condition_id, target, kind) in plan.back_branch_keys()


def _rewind_loop(
    state: JFCOrchestrationState,
    plan: AnalysisPlan,
    node: PlanNode,
    head: str,
    attempted: set[str],
    progress_callback: Callable[[str, str], None] | None,
) -> None:
    """Un-complete the loop body so the planner offers it again.

    The body is everything downstream of the loop head and upstream of the
    condition — the same `_between` a regression cycle rewinds, which on a
    branching plan is the correct set rather than a slice.

    **The condition itself is part of the body it rewinds.** Leaving it complete
    would let the loop run its body a second time and then walk straight past the
    test that sent it round, so the loop would take exactly one extra pass
    whatever the metric said. The budget lives in `condition_iterations`, which
    is not rewound, so re-offering the condition cannot refill it.
    """
    body = _between(plan, head, node.id)
    for node_id in sorted(body):
        if node_id in state.completed_nodes:
            state.completed_nodes.remove(node_id)
        attempted.discard(node_id)
        # A node re-entering the loop starts its review budget over; the fixer
        # is answering a fresh execution, not continuing the previous round.
        state.phase_iterations.pop(node_id, None)

    if progress_callback:
        progress_callback(node.id, f"looping back to {head}: re-running {sorted(body)}")

    git_commit_phase(state.root, node.id, f"loop iteration via {head}")


def _skip_untaken_branch(
    state: JFCOrchestrationState,
    plan: AnalysisPlan,
    node: PlanNode,
    taken: str,
    progress_callback: Callable[[str, str], None] | None,
) -> None:
    """Mark what the branch not taken exclusively leads to as skipped.

    Only what is reachable *solely* through the untaken branch is skipped: a node
    downstream of both branches is reachable from the taken one and still runs,
    which is what makes a fork that re-joins work.
    """
    others = [
        edge.downstream
        for edge in plan.branch_edges(node.id)
        if edge.downstream != taken and edge.key not in plan.back_branch_keys()
    ]
    if not others:
        return

    kept = _reach(plan, taken, forward=True)
    abandoned: set[str] = set()
    for other in others:
        abandoned |= _reach(plan, other, forward=True)
    abandoned -= kept
    abandoned -= set(state.completed_nodes)
    if not abandoned:
        return

    for node_id in sorted(abandoned):
        if node_id not in state.skipped_nodes:
            state.skipped_nodes.append(node_id)

    if progress_callback:
        progress_callback(node.id, f"branch not taken: skipping {sorted(abandoned)}")


def _one_line(text: str, limit: int = 160) -> str:
    """Flatten a rationale for a single-line progress message."""
    flat = " ".join((text or "").split())
    return flat[:limit] + ("…" if len(flat) > limit else "")


async def run_phase_with_review(
    state: JFCOrchestrationState,
    node: PlanNode,
    plan: AnalysisPlan,
    progress_callback: Callable[[str, str], None] | None = None,
    max_turns: int | None = None,
    codesign_feedback: str | None = None,
) -> None:
    """
    Execute one plan node: executor, note writer if it declares one, then the
    review gate with its iteration loop.

    The node's own `max_iterations` bounds the loop, so a cheap node can be
    allowed more attempts than an expensive one.
    """
    phase_key = node.id
    iterations = state.phase_iterations.get(phase_key, 0)
    limit = max(node.max_iterations, state.max_iterations_per_phase)

    if iterations >= limit:
        # The loop below would not execute once. Reported here rather than by
        # falling through to the raise at the bottom, which describes a review
        # that failed - this one never ran.
        raise NodeBudgetSpent(phase_key, iterations, limit)

    for iteration in range(iterations, limit):
        state.phase_iterations[phase_key] = iteration + 1
        save_state(state)

        # Executor (codesign_feedback only injected on first iteration; fixer handles later ones)
        executor_turns = _turns_for(node, max_turns, 50)
        feedback = codesign_feedback if iteration == iterations else None
        await _run_executor(
            state,
            node,
            plan,
            progress_callback,
            max_turns=executor_turns,
            codesign_feedback=feedback,
        )

        writer_turns = _turns_for(node, max_turns, 30)
        if node.produces_note:
            await _run_note_writer_and_typesetter(
                state, node, plan, progress_callback, max_turns=writer_turns
            )

        # Record what the executor produced before anyone reviews it.
        update_graph(state.root, node, "phase", plan, progress_callback)

        if progress_callback:
            progress_callback(phase_key, f"review gate (iteration {iteration + 1})")

        # Review gate
        reviewer_turns = _turns_for(node, max_turns, 20)
        try:
            result: ReviewGateResult = await run_review_gate(
                node,
                state.root,
                plan,
                model_provider=state.model_provider,
                model_name=state.model_name,
                max_turns=reviewer_turns,
            )
        except (PhaseEscalationError, PhaseRegressionError):
            # Capture the findings that stopped the run — that is exactly when
            # the provenance record matters most.
            update_graph(state.root, node, "review", plan, progress_callback)
            save_state(state)
            raise

        update_graph(state.root, node, "review", plan, progress_callback)

        if result.verdict == "PASS":
            if progress_callback:
                progress_callback(phase_key, "PASS")
            if phase_key not in state.completed_nodes:
                state.completed_nodes.append(phase_key)
            save_state(state)
            git_commit_phase(state.root, phase_key, "executor + review PASS")
            return

        if result.verdict == "ITERATE":
            if progress_callback:
                progress_callback(phase_key, f"ITERATE (iteration {iteration + 1})")
            all_findings = result.category_a_findings + result.category_b_findings
            fixer_turns = _turns_for(node, max_turns, 30)
            await run_fixer(
                node,
                state.root,
                all_findings,
                model_provider=state.model_provider,
                model_name=state.model_name,
                max_turns=fixer_turns,
            )
            if node.produces_note:
                await _run_note_writer_and_typesetter(
                    state, node, plan, progress_callback, max_turns=writer_turns
                )

    raise MaxIterationsExceeded(phase_key, limit)


class PlanNotRunnableError(ValueError):
    """Raised when a plan has findings that make it unsafe or impossible to run."""


def require_runnable_plan(plan: AnalysisPlan) -> AnalysisPlan:
    """Return `plan`, or raise if a blocking finding means it must not run.

    Called *before* anything touches the filesystem. A plan is executable data —
    `node.directory` becomes a real `mkdir` and a real `CLAUDE.md` write — so an
    authored plan that has never been through the editor, which refuses to
    approve a blocking plan, has to be checked here instead. Without this a
    `--plan` file declaring `directory: "../somewhere-else"` scaffolded itself
    outside the analysis root: P2 catches it, but nothing was asking P2.
    """
    report = validate_plan(plan, vocabulary=plan_vocabulary())
    if report.blocking:
        detail = "\n".join(f"  - [{f.rule}] {f.message}" for f in report.blocking)
        raise PlanNotRunnableError(
            f"Plan '{plan.name}' cannot be run — {len(report.blocking)} blocking "
            f"finding(s):\n{detail}\n\n"
            f"Fix the plan and try again, or open it with "
            f"`hepagent jfc plan edit --name {plan.name}`."
        )
    return plan


def _prepare_plan(
    analysis_root: Path,
    analysis_name: str,
    analysis_type: str,
    physics_prompt: str,
    template: str,
    plan: AnalysisPlan | None,
) -> AnalysisPlan:
    """Settle which plan this run executes.

    An explicit plan wins; otherwise the analysis's own `plan.json` is used; a
    directory that predates the plan layer gets one instantiated from `template`
    and written, so a legacy analysis becomes plan-driven on first resume.

    Whatever the source, the result is checked before it is returned — a
    hand-edited `plan.json` on disk is as unchecked as one passed on the command
    line.
    """
    if plan is not None:
        require_runnable_plan(plan)
        save_plan(analysis_root, plan)
        return plan
    if has_plan(analysis_root):
        return require_runnable_plan(load_plan(analysis_root))
    built = instantiate(
        template,
        analysis_name=analysis_name,
        analysis_type=analysis_type,
        physics_prompt=physics_prompt,
    )
    save_plan(analysis_root, built)
    return built


def _apply_codesign_flag(plan: AnalysisPlan, codesign: bool) -> AnalysisPlan:
    """Turn every codesign gate on or off. `--codesign` flips data, not control flow."""
    import dataclasses

    nodes = tuple(
        dataclasses.replace(
            node,
            gates=tuple(
                dataclasses.replace(gate, enabled=codesign) if gate.name == "codesign" else gate
                for gate in node.gates
            ),
        )
        for node in plan.nodes
    )
    return dataclasses.replace(plan, nodes=nodes)


async def run_jfc_analysis(
    analysis_name: str,
    physics_prompt: str,
    analysis_type: Literal["measurement", "search"],
    base_dir: str = "analyses",
    model_provider: str = "cborg",
    model_name: str | None = None,
    start_from_phase: str | None = None,
    only_node: str | None = None,
    max_iterations_per_phase: int = 3,
    max_turns: int | None = None,
    progress_callback: Callable[[str, str], None] | None = None,
    codesign: bool = False,
    template: str = DEFAULT_TEMPLATE,
    plan: AnalysisPlan | None = None,
    require_approval: bool = False,
    approval_gate: PlanApprovalGate | None = None,
) -> Path:
    """
    Orchestrate a complete analysis from scaffold to published note.

    Returns the path to the final analysis note PDF.

    Args:
        analysis_name: Short name for the analysis.
        physics_prompt: The physics question to answer.
        analysis_type: "measurement" or "search".
        base_dir: Parent directory for analyses.
        model_provider: Model provider for all agents.
        model_name: Specific model name.
        start_from_phase: Node id to resume from. None starts from the plan's
            entry node; naming a node asserts everything upstream of it is done.
        only_node: Run exactly this one node and stop — the plan page's per-node
            "Run" button. Nothing upstream is run and nothing downstream follows,
            so the node reads whatever its dependencies last left on disk. The
            return value is that node's primary artifact, not the final PDF.
        max_iterations_per_phase: Floor on review iterations per node; a node
            asking for more in its plan entry gets more.
        max_turns: Per-agent turn cap.
        progress_callback: Optional callback(node_id, status_message).
        codesign: Enable every codesign gate the plan declares.
        template: Plan template for a fresh analysis. Ignored once a plan exists.
        plan: An explicit plan to run — what the plan editor and `--plan` supply.
            Written to the analysis directory, superseding what is there.
        require_approval: Wait for the plan to be approved before running the
            first node. The editor releases the latch.
        approval_gate: The latch to wait on. Defaults to the process-wide one.
    """
    analysis_root = Path(base_dir).resolve() / analysis_name
    fresh = start_from_phase is None and only_node is None

    # Before the scaffolder turns node directories into real ones. `_prepare_plan`
    # checks again for the plans that do not come in this way.
    if plan is not None:
        require_runnable_plan(plan)

    if fresh and not analysis_root.exists():
        if progress_callback:
            progress_callback("scaffold", "creating analysis directory")
        await scaffold_jfc_analysis(
            analysis_name, physics_prompt, analysis_type, base_dir, template, plan
        )

    active_plan = _apply_codesign_flag(
        _prepare_plan(analysis_root, analysis_name, analysis_type, physics_prompt, template, plan),
        codesign,
    )

    state_path = analysis_root / ".orchestration_state.json"
    if state_path.exists() and not fresh:
        state = load_state(analysis_root)
        state.max_iterations_per_phase = max_iterations_per_phase
    else:
        state = JFCOrchestrationState(
            analysis_root=str(analysis_root),
            analysis_name=analysis_name,
            analysis_type=analysis_type,
            model_provider=model_provider,
            model_name=model_name,
            max_iterations_per_phase=max_iterations_per_phase,
        )
        save_state(state)

    # Analyses scaffolded before the graph existed, or resumed from a directory
    # that lost it, get one seeded here. bootstrap_graph is idempotent.
    _ensure_graph(analysis_root, active_plan, progress_callback)

    if require_approval:
        gate = approval_gate or APPROVAL_GATE
        if not gate.is_approved(analysis_root):
            if progress_callback:
                progress_callback("plan", "waiting for the plan to be approved")
            await gate.wait(analysis_root)
            active_plan = _apply_codesign_flag(load_plan(analysis_root), codesign)
        if progress_callback:
            progress_callback("plan", "approved — starting")

    # Nodes whose prerequisites an explicit resume point declares satisfied.
    assumed = _nodes_before(active_plan, start_from_phase)

    # Owned here rather than by the generator: a loop rewinds by taking its body
    # back out, and a node offered only once per run could never take a second pass.
    attempted: set[str] = set()

    # A single-node run skips the planner rather than asking it for a frontier of
    # one: the point of the button is to re-run *this* node whatever the graph
    # thinks is ready, and `next_phase` would refuse it while something upstream
    # is incomplete.
    sequence: Iterable[PlanNode]
    if only_node is None:
        sequence = _phase_sequence(
            analysis_root, state, active_plan, assumed, progress_callback, attempted
        )
    else:
        single = active_plan.node(only_node)
        if single is None:
            raise ValueError(f"Plan '{active_plan.name}' has no node '{only_node}'.")
        _reset_node_progress(state, single)
        sequence = [single]

    for node in sequence:
        state.current_node = node.id
        save_state(state)

        if progress_callback:
            progress_callback(node.id, "starting")

        if node.kind == "condition":
            await run_condition_node(
                state, node, active_plan, attempted, progress_callback, max_turns=max_turns
            )
            continue

        await _run_gates(state, node, active_plan, "before", progress_callback, max_turns=max_turns)

        try:
            await run_phase_with_review(
                state, node, active_plan, progress_callback, max_turns=max_turns
            )
        except PhaseRegressionError as reg_err:
            await _run_regression_cycle(
                state,
                active_plan,
                reg_err,
                progress_callback,
                max_turns=max_turns,
                attempted=attempted,
            )
            # The detected node has been re-run and marked complete; move on.
            continue

        outcome = await _run_gates(
            state, node, active_plan, "after", progress_callback, max_turns=max_turns
        )
        if outcome == "rerun":
            if node.id in state.completed_nodes:
                state.completed_nodes.remove(node.id)
            save_state(state)
            await run_phase_with_review(
                state,
                node,
                active_plan,
                progress_callback,
                max_turns=max_turns,
                codesign_feedback=_codesign_feedback(analysis_root),
            )

    if only_node is not None:
        node = active_plan.node(only_node)
        artifact = analysis_root / node.artifact_path if node else analysis_root
        if progress_callback:
            progress_callback("complete", f"Node '{only_node}' complete: {artifact}")
        return artifact

    final_pdf = _final_pdf(analysis_root, active_plan)
    if progress_callback:
        progress_callback("complete", f"Analysis complete: {final_pdf}")
    return final_pdf


def _reset_node_progress(state: JFCOrchestrationState, node: PlanNode) -> None:
    """Forget what earlier runs recorded about `node`, so it can run again.

    A whole-run launch is `fresh`, so it builds a new state and its counters
    start at zero. A single-node run loads the state on disk instead, and would
    inherit the review iterations the node already spent: with the counter at or
    above the node's `max_iterations` - which happens after any completed run,
    and immediately if the plan has since *lowered* the limit -
    `run_phase_with_review` has nothing left to iterate, so pressing Run raised
    at once and looked to the physicist like a button that does nothing.

    Pressing Run on a node says "do this node again", so this clears that node's
    own bookkeeping and nothing else: what other nodes completed still stands,
    and so does everything the node left on disk, which the executor is expected
    to read and supersede.
    """
    state.phase_iterations.pop(node.id, None)
    if node.id in state.completed_nodes:
        state.completed_nodes.remove(node.id)
    # A node a condition routed past is not "done", it is "decided against";
    # asking for it by name overrides that decision for this run.
    if node.id in state.skipped_nodes:
        state.skipped_nodes.remove(node.id)
    if node.kind == "condition":
        # The loop budget too - re-evaluating a condition whose budget is spent
        # would otherwise be refused by `_apply_budget` before it read anything.
        state.condition_iterations.pop(node.id, None)
        state.condition_history.pop(node.id, None)
    save_state(state)


def _final_pdf(analysis_root: Path, plan: AnalysisPlan) -> Path:
    """The PDF the run delivers: the last node that writes an analysis note."""
    note_nodes = [n for n in plan.nodes if n.produces_note]
    if not note_nodes:
        return analysis_root
    return analysis_root / note_nodes[-1].note_pdf_path
