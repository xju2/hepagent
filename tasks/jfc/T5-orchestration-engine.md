# T5: Phase Orchestration Engine

**Priority:** P2 (ties everything together)
**Depends on:** T2, T3, T4
**Blocks:** T7, T8

## Goal

Implement the programmable orchestration loop that drives a JFC analysis from
scaffold through all 5 phases. This replaces JFC's CLAUDE.md-based orchestrator
with Python code using the OpenAI Agent SDK.

## Core Loop

Each phase follows the same pattern from `methodology/03a-orchestration.md`:

```
EXECUTE → REVIEW → CHECK → COMMIT → ADVANCE
```

With iteration:
```
while not PASS:
    EXECUTE (fix findings or re-run full phase)
    REVIEW
    CHECK verdict
```

And human gate after Phase 4b:
```
present PDF + findings → wait for human approval → ADVANCE to 4c
```

## Deliverables

### `src/hepagent/agents/jfc/orchestrator.py`

```python
@dataclass
class JFCOrchestrationState:
    analysis_root: Path
    analysis_name: str
    analysis_type: Literal["measurement", "search"]
    current_phase: int          # 1–5
    current_subphase: str       # "4a", "4b", "4c" for phase 4
    max_iterations_per_phase: int = 3
    model: str = "cborg:claude-sonnet-4-5"


async def run_jfc_analysis(
    analysis_name: str,
    physics_prompt: str,
    analysis_type: Literal["measurement", "search"],
    base_dir: str = "analyses",
    model: str = "cborg:claude-sonnet-4-5",
    start_from_phase: int = 1,  # for resuming interrupted runs
) -> Path:
    """
    Orchestrate a complete JFC analysis from scaffold to published note.

    Returns path to the final analysis note PDF.
    """
```

**Implementation steps:**

1. **Scaffold** (calls `scaffold_jfc_analysis` if `start_from_phase == 1`)

2. **Phase loop** (phases 1, 2, 3, 4a, 4b, 4c, 5):
   ```python
   for phase in PHASE_ORDER:
       await run_phase_with_review(state, phase)
       git_commit(analysis_root, f"phase {phase}: executor + review complete")
   ```

3. **`run_phase_with_review(state, phase)`:**
   ```python
   async def run_phase_with_review(state, phase):
       for iteration in range(state.max_iterations_per_phase):
           executor = create_phase_executor(phase, state.analysis_root, state.model)
           await executor.run(context)   # writes artifact to disk
           result = await run_review_gate(phase, state.analysis_root, state.model)
           if result.verdict == "PASS":
               return
           if result.verdict == "ESCALATE":
               raise PhaseEscalationError(phase, result)
           # ITERATE: spawn fixer with findings, then re-review
           await run_fixer(state, phase, result.category_a_findings)
       raise MaxIterationsExceeded(phase)
   ```

4. **Human gate** (after phase 4b):
   ```python
   # Present PDF path + summary to user via ask_user_for_info
   approval = await ask_user_for_info(
       "Phase 4b (10% validation) complete. Review the draft analysis note "
       f"at {pdf_path}. Type APPROVE to proceed to full data, or describe issues."
   )
   if "APPROVE" not in approval.upper():
       # Treat user feedback as Category A findings and iterate
       ...
   ```

5. **Phase 4 sub-phases** (4a, 4b, 4c run in sequence, not as loop iterations)

### `src/hepagent/agents/jfc/fixer.py`

```python
async def run_fixer(
    state: JFCOrchestrationState,
    phase: int,
    findings: list[str],
    model: str,
) -> None:
    """
    Spawn a fixer agent to address Category A/B findings in-place.

    Fixer reads the current phase artifact + findings list, produces
    corrected artifact (in place), does NOT re-run the full executor.
    """
```

System prompt from `testarea/jfc/src/agents/fixer.md`. Fixer has bash
execution tools to modify scripts and re-run targeted analyses.

### `src/hepagent/agents/jfc/investigator.py`

```python
async def run_investigator(
    state: JFCOrchestrationState,
    regression_phase: int,
    symptom: str,
) -> RegressionTicket:
    """
    Investigate a regression finding traceable to an earlier phase.

    Returns a RegressionTicket describing which earlier phase to re-run
    and what specifically to fix.
    """
```

System prompt from `testarea/jfc/src/agents/investigator.md`.

### `src/hepagent/agents/jfc/commitment_checker.py` (lightweight)

```python
def check_phase1_commitments(analysis_root: Path) -> CommitmentCheckResult:
    """
    Parse COMMITMENTS.md and verify each [D1]-[DN] is marked resolved or
    formally downscoped in the current phase artifact.

    Used as a pre-advancement gate at Phase 4a.
    """
```

### State Persistence

Between phase runs (which may crash), serialize `JFCOrchestrationState` to
`{analysis_root}/.orchestration_state.json`. On startup with
`start_from_phase > 1`, load this state to resume.

```python
def save_state(state: JFCOrchestrationState) -> None:
    path = state.analysis_root / ".orchestration_state.json"
    path.write_text(state.model_dump_json())

def load_state(analysis_root: Path) -> JFCOrchestrationState:
    path = analysis_root / ".orchestration_state.json"
    return JFCOrchestrationState.model_validate_json(path.read_text())
```

### Git Commit Integration

After each phase completes review:
```python
def git_commit_phase(analysis_root: Path, phase: str, message: str) -> None:
    subprocess.run(["git", "add", "-A"], cwd=analysis_root)
    subprocess.run(["git", "commit", "-m", f"phase/{phase}: {message}"], cwd=analysis_root)
```

## Acceptance Criteria

- [ ] `run_jfc_analysis(...)` with a mocked executor+reviewer completes phases
  1, 2, 3 without error
- [ ] State is saved to `.orchestration_state.json` after each phase
- [ ] `start_from_phase=3` skips phases 1 and 2 and resumes from phase 3
- [ ] `MaxIterationsExceeded` is raised after 3 failed review iterations
- [ ] Human gate pauses execution and prompts the user via `ask_user_for_info`
- [ ] Git commits are created after each phase (verified by `git log`)
- [ ] Integration test in `tests/agents/jfc/test_orchestrator.py` (mocked
  executor and reviewers)
