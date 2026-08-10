"""JFC agent factories and orchestration.

Exports resolve lazily. `hepagent.tools.jfc` imports `hepagent.agents.jfc._data`,
which executes this package's ``__init__``; eagerly importing the agent modules
here would re-enter `hepagent.tools.jfc` while it is still initialising and raise
ImportError. Deferring the imports to attribute access keeps the public API
unchanged while breaking that cycle.
"""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - import-time only, for type checkers
    from hepagent.agents.jfc._data import get_jfc_data_dir
    from hepagent.agents.jfc.architect import (
        ArchitectProposal,
        ProposalResult,
        propose_plan,
    )
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
    from hepagent.agents.jfc.graph_builder import (
        bootstrap_graph,
        ingest_node,
        ingest_review,
        rebuild,
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

# Public name -> submodule that defines it.
_EXPORTS: dict[str, str] = {
    "get_jfc_data_dir": "_data",
    "ArchitectProposal": "architect",
    "ProposalResult": "architect",
    "propose_plan": "architect",
    "run_codesign_gate": "codesign",
    "CommitmentCheckResult": "commitment_checker",
    "CommitmentStatus": "commitment_checker",
    "CommitmentsNotResolved": "commitment_checker",
    "check_phase1_commitments": "commitment_checker",
    "create_note_writer": "executor",
    "create_phase_executor": "executor",
    "create_typesetter": "executor",
    "bootstrap_graph": "graph_builder",
    "ingest_node": "graph_builder",
    "ingest_review": "graph_builder",
    "rebuild": "graph_builder",
    "JFCOrchestrationState": "orchestrator",
    "MaxIterationsExceeded": "orchestrator",
    "load_state": "orchestrator",
    "run_jfc_analysis": "orchestrator",
    "save_state": "orchestrator",
    "PhaseEscalationError": "review_gate",
    "ReviewGateResult": "review_gate",
    "run_review_gate": "review_gate",
}

__all__ = sorted(_EXPORTS)


def __getattr__(name: str) -> Any:
    """Resolve a public export by importing its submodule on first access."""
    module_name = _EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module = importlib.import_module(f"{__name__}.{module_name}")
    value = getattr(module, name)
    globals()[name] = value  # cache so later lookups skip __getattr__
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(_EXPORTS))
