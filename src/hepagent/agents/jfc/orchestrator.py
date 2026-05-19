"""JFC phase orchestration engine."""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Literal

from agents import Runner
from hepagent.agents.common import AgentContext
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
from hepagent.agents.jfc.review_gate import PhaseEscalationError, ReviewGateResult, run_review_gate
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


# Ordered phase sequence
PHASE_ORDER: list[int | str] = [1, 2, 3, "4a", "4b", "4c", 5]

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
) -> None:
    """Run the executor agent for a phase."""
    if progress_callback:
        progress_callback(str(phase), "executor starting")

    executor = create_phase_executor(
        phase,
        state.root,
        model_provider=state.model_provider,
        model_name=state.model_name,
    )
    context = AgentContext(agent_name="jfc_executor", active_skill="jfc")
    task = (
        f"Execute Phase {phase} of the JFC analysis at {state.root}. "
        f"Read your system prompt for full instructions. "
        f"Produce the primary artifact to the outputs/ directory."
    )
    await Runner.run(executor, task, context=context, max_turns=50)

    if progress_callback:
        progress_callback(str(phase), "executor complete")


async def _run_note_writer_and_typesetter(
    state: JFCOrchestrationState,
    phase: str,
    progress_callback: Callable[[str, str], None] | None,
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
    await Runner.run(note_writer, task, context=context, max_turns=30)

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
    await Runner.run(typesetter, type_task, context=type_context, max_turns=20)

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


async def run_phase_with_review(
    state: JFCOrchestrationState,
    phase: int | str,
    progress_callback: Callable[[str, str], None] | None = None,
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

        # Executor
        await _run_executor(state, phase, progress_callback)

        # Note writer + typesetter for AN phases
        if str(phase) in AN_PHASES:
            await _run_note_writer_and_typesetter(state, str(phase), progress_callback)

        if progress_callback:
            progress_callback(str(phase), f"review gate (iteration {iteration + 1})")

        # Review gate
        try:
            result: ReviewGateResult = await run_review_gate(
                phase,
                state.root,
                model_provider=state.model_provider,
                model_name=state.model_name,
            )
        except PhaseEscalationError:
            save_state(state)
            raise

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
            await run_fixer(
                phase,
                state.root,
                all_findings,
                model_provider=state.model_provider,
                model_name=state.model_name,
            )
            # If it's an AN phase, re-typeset after fixing
            if str(phase) in AN_PHASES:
                await _run_note_writer_and_typesetter(state, str(phase), progress_callback)

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
    progress_callback: Callable[[str, str], None] | None = None,
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

    # Determine which phases to run
    start_idx = PHASE_ORDER.index(start_from_phase) if start_from_phase in PHASE_ORDER else 0
    phases_to_run = PHASE_ORDER[start_idx:]

    for phase in phases_to_run:
        phase_key = str(phase)
        if phase_key in state.completed_phases:
            if progress_callback:
                progress_callback(str(phase), "already complete, skipping")
            continue

        state.current_phase = phase if isinstance(phase, int) else 0
        state.current_subphase = str(phase)
        save_state(state)

        if progress_callback:
            progress_callback(str(phase), "starting")

        # Commitment gate before Phase 4a
        if phase == "4a":
            commitment_result = check_phase1_commitments(analysis_root)
            if not commitment_result.all_resolved:
                raise CommitmentsNotResolved(commitment_result)

        await run_phase_with_review(state, phase, progress_callback)

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
