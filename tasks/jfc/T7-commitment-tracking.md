# T7: Commitment Tracking System

**Priority:** P3
**Depends on:** T5, T6 (`update_commitments` tool from T6)
**Blocks:** —

## Goal

Implement the binding commitment tracking system that enforces Phase 1's
[D1]-[DN] commitments are resolved or explicitly downscoped before Phase 4a
can advance.

JFC's specification is strict: every commitment from Phase 1 must be tracked
and verified. Silent dropping is not permitted.

## Background

In Phase 1, the executor produces `STRATEGY.md` with explicit commitments:
```markdown
[D1] Will compare two selection approaches: cut-based and MVA-based.
[D2] Will use unfolding for the cross-section measurement.
[D3] Will quote systematic from jet energy scale.
```

By Phase 4a, each must be:
- **Resolved:** Evidence provided (e.g., "chi2/ndf = 1.3, file: results/closure.json")
- **Downscoped:** Formally documented attempt + reason (e.g., "Attempted MVA, insufficient MC for training")
- NOT silently absent.

## Deliverables

### `COMMITMENTS.md` format (written by Phase 1 executor)

```markdown
# Phase 1 Commitments

| ID | Commitment | Status | Evidence | Phase Resolved |
|----|-----------|--------|----------|---------------|
| D1 | Compare cut-based and MVA-based | pending | — | — |
| D2 | Use unfolding for cross-section | pending | — | — |
| D3 | Quote jet energy scale systematic | pending | — | — |
```

The Phase 1 executor should populate the commitment rows from its STRATEGY.md.
The `update_commitments` tool (T6) updates a row's Status/Evidence columns.

### `src/hepagent/agents/jfc/commitment_checker.py`

```python
@dataclass
class CommitmentStatus:
    id: str             # "D1", "D2", ...
    text: str
    status: Literal["pending", "resolved", "downscoped"]
    evidence: str
    phase_resolved: str  # "3", "4a", etc.

@dataclass
class CommitmentCheckResult:
    all_resolved: bool
    pending: list[CommitmentStatus]
    resolved: list[CommitmentStatus]
    downscoped: list[CommitmentStatus]
    blocking_message: str  # human-readable summary if not all_resolved


def check_phase1_commitments(analysis_root: Path) -> CommitmentCheckResult:
    """
    Parse COMMITMENTS.md and return status of all commitments.

    Called by the orchestrator (T5) as a pre-advancement gate before
    Phase 4a executor runs.
    """
```

### Integration in orchestrator (`T5`)

In `run_jfc_analysis`, before running Phase 4a executor:
```python
commitment_result = check_phase1_commitments(state.analysis_root)
if not commitment_result.all_resolved:
    # Inject pending commitments as Category A findings into Phase 3 review
    # so they are resolved before Phase 4a starts
    raise CommitmentsNotResolved(commitment_result.blocking_message)
```

### Executor instruction injection

In `create_phase_executor` (T3), for phases 3, 4a: include the current
COMMITMENTS.md content in the executor's context block so it knows what
it must address.

### Arbiter commitment check

In the Phase 4a arbiter's system prompt, include:
> "Before rendering your verdict, verify that all commitments in
> COMMITMENTS.md are either `resolved` or `downscoped`. Any `pending`
> commitment is automatically Category A."

## Acceptance Criteria

- [x] Phase 1 executor produces a `COMMITMENTS.md` with [D1]-[DN] rows
- [x] `check_phase1_commitments` returns pending items correctly
- [x] Orchestrator raises `CommitmentsNotResolved` if any pending at Phase 4a
- [x] `update_commitments("D1", "resolved", "evidence")` updates the table row
- [x] `update_commitments("D2", "downscoped", "reason")` updates correctly
- [x] Arbiter system prompt at Phase 4a references COMMITMENTS.md
- [x] Unit test: `tests/agents/jfc/test_commitment_checker.py`
