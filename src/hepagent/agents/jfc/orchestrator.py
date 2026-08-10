"""JFC phase orchestration engine."""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable, Iterator
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Literal

from agents import Runner
from hepagent.agents.common import AgentContext
from hepagent.agents.jfc.codesign import run_codesign_gate
from hepagent.agents.jfc.commitment_checker import (
    CommitmentsNotResolved,
    check_phase1_commitments,
)
from hepagent.agents.jfc.executor import (
    create_note_writer,
    create_phase_executor,
    create_typesetter,
)
from hepagent.agents.jfc.fixer import run_fixer
from hepagent.agents.jfc.graph_builder import bootstrap_graph, ingest_phase, ingest_review
from hepagent.agents.jfc.investigator import RegressionTicket, run_investigator
from hepagent.agents.jfc.planner import PHASE_ORDER as PLANNER_PHASE_ORDER, next_phase
from hepagent.agents.jfc.review_gate import (
    PhaseEscalationError,
    PhaseRegressionError,
    ReviewGateResult,
    run_review_gate,
)
from hepagent.graph.store import AnalysisGraph
from hepagent.graph.validation import validate_commitments
from hepagent.tools.jfc.scaffold import _scaffold_impl as scaffold_jfc_analysis


class MaxIterationsExceeded(Exception):
    """Raised when a phase fails review more than max_iterations_per_phase times."""

    def __init__(self, phase: int | str, iterations: int):
        self.phase = phase
        self.iterations = iterations
        super().__init__(
            f"Phase {phase} exceeded {iterations} review iterations without PASS. "
            f"Human intervention required. Resume with start_from_phase={phase}."
        )


@dataclass
class JFCOrchestrationState:
    analysis_root: str
    analysis_name: str
    analysis_type: Literal["measurement", "search"]
    current_phase: int = 1
    current_subphase: str = "1"
    max_iterations_per_phase: int = 3
    model_provider: str = "cborg"
    model_name: str | None = None
    completed_phases: list[str] = field(default_factory=list)
    phase_iterations: dict[str, int] = field(default_factory=dict)

    def model_dump_json(self) -> str:
        return json.dumps(asdict(self), indent=2)

    @classmethod
    def model_validate_json(cls, data: str) -> JFCOrchestrationState:
        d = json.loads(data)
        return cls(**d)

    @property
    def root(self) -> Path:
        return Path(self.analysis_root)


# Canonical phase sequence. Execution order is derived from the graph's
# `requires` edges (see `planner`); this remains the tiebreak between equally
# ready phases and the fallback when the graph cannot be read.
PHASE_ORDER = PLANNER_PHASE_ORDER

# Phases that produce an analysis note requiring note_writer + typesetter
AN_PHASES: set[str] = {"4a", "4b", "4c", "5"}

_AN_OUTPUT_MAP = {
    "4a": "phase4a_inference_expected/outputs/ANALYSIS_NOTE_4a_v1.md",
    "4b": "phase4b_inference_partial/outputs/ANALYSIS_NOTE_4b_v1.md",
    "4c": "phase4c_inference_observed/outputs/ANALYSIS_NOTE_4c_v1.md",
    "5": "phase5_documentation/outputs/ANALYSIS_NOTE_5_v1.md",
}

_AN_PDF_MAP = {
    "4a": "phase4a_inference_expected/outputs/ANALYSIS_NOTE_4a_v1.pdf",
    "4b": "phase4b_inference_partial/outputs/ANALYSIS_NOTE_4b_v1.pdf",
    "4c": "phase4c_inference_observed/outputs/ANALYSIS_NOTE_4c_v1.pdf",
    "5": "phase5_documentation/outputs/ANALYSIS_NOTE_5_v1.pdf",
}


def save_state(state: JFCOrchestrationState) -> None:
    path = state.root / ".orchestration_state.json"
    path.write_text(state.model_dump_json(), encoding="utf-8")


def load_state(analysis_root: Path) -> JFCOrchestrationState:
    path = analysis_root / ".orchestration_state.json"
    return JFCOrchestrationState.model_validate_json(path.read_text(encoding="utf-8"))


def update_graph(
    analysis_root: Path,
    phase: int | str,
    stage: Literal["phase", "review"],
    progress_callback: Callable[[str, str], None] | None = None,
) -> None:
    """Fold a phase's outputs (or its review round) into the analysis graph.

    Graph ingestion is bookkeeping, not analysis: a failure here must never take
    down a run that is otherwise progressing, so this swallows exceptions the
    same way `git_commit_phase` does and reports them through the callback.
    """
    try:
        report = (
            ingest_phase(analysis_root, phase)
            if stage == "phase"
            else ingest_review(analysis_root, phase)
        )
    except Exception as exc:  # noqa: BLE001 - bookkeeping must not abort a run
        if progress_callback:
            progress_callback(str(phase), f"graph {stage} ingestion failed: {exc}")
        return
    if progress_callback:
        progress_callback(str(phase), f"graph updated ({stage}): {report.summary()}")


def _phases_before(start_from_phase: int | str) -> set[str]:
    """Phase keys strictly before an explicit resume point.

    Resuming at 4a is an assertion that 1-3 are done; the planner needs that
    stated or it will report 4a as blocked on unsatisfied prerequisites.
    """
    key = str(start_from_phase)
    if key not in {str(p) for p in PHASE_ORDER}:
        return set()
    index = [str(p) for p in PHASE_ORDER].index(key)
    return {str(p) for p in PHASE_ORDER[:index]}


def _phase_sequence(
    analysis_root: Path,
    state: JFCOrchestrationState,
    assumed: set[str],
    progress_callback: Callable[[str, str], None] | None = None,
) -> Iterator[int | str]:
    """Yield phases to run, re-reading the graph frontier after each one.

    The order is derived from `requires` edges rather than declared, so a phase
    becomes runnable exactly when its prerequisites are complete — including
    after a regression cycle has un-completed part of the analysis.
    """
    attempted: set[str] = set()

    while True:
        try:
            graph = AnalysisGraph.load(analysis_root)
            satisfied = set(state.completed_phases) | assumed
            phase_key = next_phase(graph, satisfied, skip=attempted)
        except Exception as exc:  # noqa: BLE001 - fall back rather than abort
            if progress_callback:
                progress_callback("planner", f"graph planning failed ({exc}); using phase order")
            phase_key = _fallback_next(state, assumed, attempted)

        if phase_key is None:
            return

        attempted.add(phase_key)
        yield _canonical_phase(phase_key)


def _fallback_next(
    state: JFCOrchestrationState,
    assumed: set[str],
    attempted: set[str],
) -> str | None:
    """Next phase by canonical order, used when the graph cannot be planned from."""
    done = set(state.completed_phases) | assumed
    for phase in PHASE_ORDER:
        key = str(phase)
        if key not in done and key not in attempted:
            return key
    return None


def _canonical_phase(phase_key: str) -> int | str:
    """Return the phase in the form the rest of the pipeline keys off ("4a" or 3)."""
    for phase in PHASE_ORDER:
        if str(phase) == phase_key:
            return phase
    return phase_key


def _ensure_graph(
    analysis_root: Path,
    analysis_name: str,
    analysis_type: str,
    physics_prompt: str,
    progress_callback: Callable[[str, str], None] | None = None,
) -> None:
    """Seed the analysis graph if this directory does not have one yet."""
    if (analysis_root / "graph" / "nodes.jsonl").exists():
        return
    try:
        bootstrap_graph(analysis_root, analysis_name, analysis_type, physics_prompt)
    except Exception as exc:  # noqa: BLE001 - bookkeeping must not abort a run
        if progress_callback:
            progress_callback("graph", f"bootstrap failed: {exc}")
        return
    if progress_callback:
        progress_callback("graph", "seeded analysis graph")


def _graph_commitment_findings(analysis_root: Path) -> list[str]:
    """Return graph-level commitment problems blocking Phase 4a.

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


def git_commit_phase(analysis_root: Path, phase: str | int, message: str) -> None:
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
    phase: int | str,
    progress_callback: Callable[[str, str], None] | None,
    max_turns: int = 50,
    codesign_feedback: str | None = None,
) -> None:
    """Run the executor agent for a phase."""
    if progress_callback:
        progress_callback(str(phase), "executor starting")

    executor = create_phase_executor(
        phase,
        state.root,
        model_provider=state.model_provider,
        model_name=state.model_name,
        codesign_feedback=codesign_feedback,
    )
    context = AgentContext(agent_name="jfc_executor", active_skill="jfc")
    task = (
        f"Execute Phase {phase} of the JFC analysis at {state.root}. "
        f"Read your system prompt for full instructions. "
        f"Produce the primary artifact to the outputs/ directory."
    )
    await Runner.run(executor, task, context=context, max_turns=max_turns)

    if progress_callback:
        progress_callback(str(phase), "executor complete")


async def _run_note_writer_and_typesetter(
    state: JFCOrchestrationState,
    phase: str,
    progress_callback: Callable[[str, str], None] | None,
    max_turns: int = 30,
) -> Path | None:
    """Run note writer and typesetter for AN phases."""
    if progress_callback:
        progress_callback(str(phase), "writing analysis note")

    note_writer = create_note_writer(
        phase,  # type: ignore[arg-type]
        state.root,
        model_provider=state.model_provider,
        model_name=state.model_name,
    )
    context = AgentContext(agent_name="jfc_note_writer", active_skill="jfc")
    an_path = state.root / _AN_OUTPUT_MAP[phase]
    task = (
        f"Write the analysis note for Phase {phase} to {an_path}. "
        f"Read all available phase artifacts from {state.root}."
    )
    await Runner.run(note_writer, task, context=context, max_turns=max_turns)

    if progress_callback:
        progress_callback(str(phase), "typesetting analysis note")

    typesetter = create_typesetter(
        state.root,
        model_provider=state.model_provider,
        model_name=state.model_name,
    )
    pdf_path = state.root / _AN_PDF_MAP[phase]
    type_context = AgentContext(agent_name="jfc_typesetter", active_skill="jfc")
    type_task = (
        f"Compile the analysis note at {an_path} to PDF at {pdf_path}. "
        f"Run pandoc → postprocess_tex.py → tectonic. Read and verify the PDF output."
    )
    await Runner.run(typesetter, type_task, context=type_context, max_turns=max_turns)

    return pdf_path if pdf_path.exists() else None


async def _human_gate(state: JFCOrchestrationState, pdf_path: Path | None) -> bool:
    """Present Phase 4b results to the human for approval. Returns True if approved."""
    pdf_str = str(pdf_path) if pdf_path else "(PDF compilation failed — check logs)"

    # In CLI/TUI context we use ask_user_for_info directly
    # Here we use print + input as a fallback
    print(
        f"\n{'=' * 60}\n"
        f"HUMAN GATE: Phase 4b (10% Validation) Complete\n"
        f"{'=' * 60}\n"
        f"Draft analysis note PDF: {pdf_str}\n\n"
        f"Review the PDF and respond:\n"
        f"  APPROVE  — proceed to full data (Phase 4c)\n"
        f"  ITERATE  — return to Phase 4b for fixes\n"
        f"  REGRESS(N) — regress to Phase N\n"
    )
    try:
        response = input("Your response: ").strip().upper()
    except (EOFError, OSError):
        response = ""

    if response.startswith("APPROVE"):
        return True
    return False


async def _run_regression_cycle(
    state: JFCOrchestrationState,
    err: PhaseRegressionError,
    progress_callback: Callable[[str, str], None] | None,
    max_turns: int | None = None,
) -> None:
    """
    Handle a regression verdict:
    1. Run the investigator to produce REGRESSION_TICKET.md.
    2. Determine which phases (origin + affected downstream) must be re-run.
    3. Remove those phases from completed_phases and re-run them in order.
    """
    if progress_callback:
        progress_callback(
            str(err.detected_phase),
            f"regression → investigating (origin Phase {err.origin_phase})",
        )

    investigator_turns = max_turns if max_turns is not None else 20
    ticket: RegressionTicket = await run_investigator(
        detected_phase=err.detected_phase,
        origin_phase=err.origin_phase,
        symptom=err.symptom,
        analysis_root=state.root,
        model_provider=state.model_provider,
        model_name=state.model_name,
        max_turns=investigator_turns,
    )

    # Normalize a raw phase value to the type used in PHASE_ORDER (int or str).
    def _canonical(p: int | str) -> int | str:
        try:
            ip = int(p)
            return ip if ip in PHASE_ORDER else str(p)
        except (ValueError, TypeError):
            return str(p)

    phase_rank = {p: i for i, p in enumerate(PHASE_ORDER)}

    # Build the ordered list of phases to re-run.
    # Use the ticket's affected_phases if populated; fall back to origin..detected.
    if ticket.affected_phases:
        phases_to_rerun: list[int | str] = [_canonical(p) for p in ticket.affected_phases]
    else:
        origin_c = _canonical(ticket.origin_phase)
        detected_c = _canonical(err.detected_phase)
        origin_idx = phase_rank.get(origin_c, 0)
        detected_idx = phase_rank.get(detected_c, len(PHASE_ORDER) - 1)
        phases_to_rerun = list(PHASE_ORDER[origin_idx : detected_idx + 1])

    # Ensure detected phase is included
    detected_c = _canonical(err.detected_phase)
    if detected_c not in phases_to_rerun:
        phases_to_rerun.append(detected_c)

    # Deduplicate and sort by canonical phase order
    phases_to_rerun = sorted(
        {_canonical(p) for p in phases_to_rerun}, key=lambda p: phase_rank.get(p, 999)
    )

    if progress_callback:
        progress_callback(
            str(err.detected_phase),
            f"regression cycle: re-running phases {phases_to_rerun}",
        )

    # Remove affected phases from completed so they are re-run
    for phase in phases_to_rerun:
        key = str(phase)
        if key in state.completed_phases:
            state.completed_phases.remove(key)
    save_state(state)

    git_commit_phase(
        state.root,
        err.detected_phase,
        f"regression detected (origin Phase {err.origin_phase}) — rewinding",
    )

    # Re-run each affected phase in order
    for phase in phases_to_rerun:
        await run_phase_with_review(state, phase, progress_callback, max_turns=max_turns)


async def run_phase_with_review(
    state: JFCOrchestrationState,
    phase: int | str,
    progress_callback: Callable[[str, str], None] | None = None,
    max_turns: int | None = None,
    codesign_feedback: str | None = None,
) -> None:
    """
    Execute a single phase including executor, note writer (if AN phase),
    and review gate with iteration loop.
    """
    phase_key = str(phase)
    iterations = state.phase_iterations.get(phase_key, 0)

    for iteration in range(iterations, state.max_iterations_per_phase):
        state.phase_iterations[phase_key] = iteration + 1
        save_state(state)

        # Executor (codesign_feedback only injected on first iteration; fixer handles later ones)
        executor_turns = max_turns if max_turns is not None else 50
        feedback = codesign_feedback if iteration == iterations else None
        await _run_executor(
            state, phase, progress_callback, max_turns=executor_turns, codesign_feedback=feedback
        )

        # Note writer + typesetter for AN phases
        writer_turns = max_turns if max_turns is not None else 30
        if str(phase) in AN_PHASES:
            await _run_note_writer_and_typesetter(
                state, str(phase), progress_callback, max_turns=writer_turns
            )

        # Record what the executor produced before anyone reviews it.
        update_graph(state.root, phase, "phase", progress_callback)

        if progress_callback:
            progress_callback(str(phase), f"review gate (iteration {iteration + 1})")

        # Review gate
        reviewer_turns = max_turns if max_turns is not None else 20
        try:
            result: ReviewGateResult = await run_review_gate(
                phase,
                state.root,
                model_provider=state.model_provider,
                model_name=state.model_name,
                max_turns=reviewer_turns,
            )
        except (PhaseEscalationError, PhaseRegressionError):
            # Capture the findings that stopped the run — that is exactly when
            # the provenance record matters most.
            update_graph(state.root, phase, "review", progress_callback)
            save_state(state)
            raise

        update_graph(state.root, phase, "review", progress_callback)

        if result.verdict == "PASS":
            if progress_callback:
                progress_callback(str(phase), "PASS")
            state.completed_phases.append(phase_key)
            save_state(state)
            git_commit_phase(state.root, phase, "executor + review PASS")
            return

        if result.verdict == "ITERATE":
            if progress_callback:
                progress_callback(str(phase), f"ITERATE (iteration {iteration + 1})")
            all_findings = result.category_a_findings + result.category_b_findings
            fixer_turns = max_turns if max_turns is not None else 30
            await run_fixer(
                phase,
                state.root,
                all_findings,
                model_provider=state.model_provider,
                model_name=state.model_name,
                max_turns=fixer_turns,
            )
            # If it's an AN phase, re-typeset after fixing
            if str(phase) in AN_PHASES:
                await _run_note_writer_and_typesetter(
                    state, str(phase), progress_callback, max_turns=writer_turns
                )

    raise MaxIterationsExceeded(phase, state.max_iterations_per_phase)


async def run_jfc_analysis(
    analysis_name: str,
    physics_prompt: str,
    analysis_type: Literal["measurement", "search"],
    base_dir: str = "analyses",
    model_provider: str = "cborg",
    model_name: str | None = None,
    start_from_phase: int | str = 1,
    max_iterations_per_phase: int = 3,
    max_turns: int | None = None,
    progress_callback: Callable[[str, str], None] | None = None,
    codesign: bool = False,
) -> Path:
    """
    Orchestrate a complete JFC analysis from scaffold to published note.

    Returns path to the final analysis note PDF.

    Args:
        analysis_name: Short name for the analysis.
        physics_prompt: The physics question to answer.
        analysis_type: "measurement" or "search".
        base_dir: Parent directory for analyses.
        model_provider: Model provider for all agents.
        model_name: Specific model name.
        start_from_phase: Phase to start from (for resuming). 1 = start fresh.
        max_iterations_per_phase: Maximum review iterations before raising MaxIterationsExceeded.
        progress_callback: Optional callback(phase_name, status_message) for progress reporting.
        codesign: If True, run the codesign gate after Phase 1 PASS: generate a human-readable
            strategy summary, facilitate interactive human review, then re-adjudicate with the
            arbiter. If verdict is REVISE, Phase 1 is re-run before proceeding to Phase 2.
    """
    analysis_root = Path(base_dir).resolve() / analysis_name

    # Scaffold or load state
    if start_from_phase == 1 and not analysis_root.exists():
        if progress_callback:
            progress_callback("scaffold", "creating analysis directory")
        await scaffold_jfc_analysis(analysis_name, physics_prompt, analysis_type, base_dir)

    state_path = analysis_root / ".orchestration_state.json"
    if state_path.exists() and start_from_phase != 1:
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
    _ensure_graph(analysis_root, analysis_name, analysis_type, physics_prompt, progress_callback)

    # Phases whose prerequisites an explicit resume point declares satisfied.
    # Without this, starting at 4a would look blocked on phases 1-3.
    assumed = _phases_before(start_from_phase)

    for phase in _phase_sequence(analysis_root, state, assumed, progress_callback):
        state.current_phase = phase if isinstance(phase, int) else 0
        state.current_subphase = str(phase)
        save_state(state)

        if progress_callback:
            progress_callback(str(phase), "starting")

        # Commitment gate before Phase 4a
        if phase == "4a":
            commitment_result = check_phase1_commitments(analysis_root)
            graph_findings = _graph_commitment_findings(analysis_root)
            if not commitment_result.all_resolved or graph_findings:
                if graph_findings:
                    commitment_result.blocking_message = "\n".join(
                        filter(
                            None,
                            [
                                commitment_result.blocking_message
                                or "Phase 4a blocked by the analysis graph:",
                                "",
                                "Graph commitment findings:",
                                *(f"  - {f}" for f in graph_findings),
                            ],
                        )
                    )
                raise CommitmentsNotResolved(commitment_result)

        try:
            await run_phase_with_review(state, phase, progress_callback, max_turns=max_turns)
        except PhaseRegressionError as reg_err:
            await _run_regression_cycle(state, reg_err, progress_callback, max_turns=max_turns)
            # After the regression cycle the detected phase has been re-run and
            # marked complete; skip to the next phase in the outer loop.
            continue

        # Codesign gate after Phase 1 (one-time human review of the strategy)
        if phase == 1 and codesign:
            codesign_verdict = await run_codesign_gate(
                analysis_root,
                model_provider=state.model_provider,
                model_name=state.model_name,
                max_turns=max_turns,
                progress_callback=progress_callback,
            )
            if codesign_verdict == "REVISE":
                if progress_callback:
                    progress_callback(
                        "1", "codesign REVISE — re-running Phase 1 with human feedback"
                    )
                if "1" in state.completed_phases:
                    state.completed_phases.remove("1")
                save_state(state)
                feedback_path = analysis_root / "phase1_strategy" / "codesign" / "HUMAN_FEEDBACK.md"
                feedback_content = (
                    feedback_path.read_text(encoding="utf-8") if feedback_path.exists() else None
                )
                await run_phase_with_review(
                    state,
                    1,
                    progress_callback,
                    max_turns=max_turns,
                    codesign_feedback=feedback_content,
                )

        # Human gate after Phase 4b
        if phase == "4b":
            pdf_path_str = _AN_PDF_MAP.get("4b", "")
            pdf_path = analysis_root / pdf_path_str if pdf_path_str else None
            approved = await _human_gate(state, pdf_path)
            if not approved:
                # Treat as ITERATE — human wants changes
                raise PhaseEscalationError(
                    "4b",
                    type(
                        "ReviewGateResult",
                        (),
                        {
                            "verdict": "ESCALATE",
                            "category_a_findings": ["Human did not approve Phase 4b results"],
                            "category_b_findings": [],
                            "adjudication_path": None,
                        },
                    )(),
                )

    # Return path to final PDF
    final_pdf = analysis_root / _AN_PDF_MAP.get(
        "5", "phase5_documentation/outputs/ANALYSIS_NOTE_5_v1.pdf"
    )
    if progress_callback:
        progress_callback("complete", f"Analysis complete: {final_pdf}")
    return final_pdf
