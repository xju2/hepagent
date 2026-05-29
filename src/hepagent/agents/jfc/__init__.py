"""JFC agent factories and orchestration."""

from hepagent.agents.jfc._data import get_jfc_data_dir
from hepagent.agents.jfc.codesign import run_codesign_gate
from hepagent.agents.jfc.commitment_checker import (
    CommitmentCheckResult,
    CommitmentsNotResolved,
    CommitmentStatus,
    check_phase1_commitments,
)
from hepagent.agents.jfc.executor import (
    create_note_writer,
    create_phase_executor,
    create_typesetter,
)
from hepagent.agents.jfc.orchestrator import (
    JFCOrchestrationState,
    MaxIterationsExceeded,
    load_state,
    run_jfc_analysis,
    save_state,
)
from hepagent.agents.jfc.review_gate import (
    PhaseEscalationError,
    ReviewGateResult,
    run_review_gate,
)

__all__ = [
    "run_codesign_gate",
    "create_phase_executor",
    "create_note_writer",
    "create_typesetter",
    "run_review_gate",
    "ReviewGateResult",
    "PhaseEscalationError",
    "JFCOrchestrationState",
    "MaxIterationsExceeded",
    "run_jfc_analysis",
    "save_state",
    "load_state",
    "check_phase1_commitments",
    "CommitmentCheckResult",
    "CommitmentStatus",
    "CommitmentsNotResolved",
]
