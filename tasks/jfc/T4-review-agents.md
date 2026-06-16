# T4: Review Agent Definitions

**Priority:** P2
**Depends on:** T1
**Blocks:** T5

## Goal

Define the HepAgent role agents for JFC's 7 reviewer roles and the arbiter.
These are instantiated in parallel per review gate, each receiving the phase
artifact(s) and relevant methodology sections.

## JFC Review Tier Summary

| Reviewer | Phase 1 | Phase 2 | Phase 3 | Phase 4a | Phase 4b | Phase 4c | Phase 5 |
|----------|---------|---------|---------|---------|---------|---------|---------|
| physics_reviewer | ✓ | | | ✓ | ✓ | | ✓ |
| critical_reviewer | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| constructive_reviewer | ✓ | | | ✓ | ✓ | | ✓ |
| plot_validator | | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| bibtex_validator | | | | ✓ | ✓ | | ✓ |
| rendering_reviewer | | | | | | | ✓ |
| arbiter | ✓ | | | ✓ | ✓ | | ✓ |

## Deliverables

### `src/hepagent/agents/jfc/reviewers.py`

One factory function per reviewer role:

```python
def create_physics_reviewer(phase: int, analysis_root: Path, model: str) -> Agent
def create_critical_reviewer(phase: int, analysis_root: Path, model: str) -> Agent
def create_constructive_reviewer(phase: int, analysis_root: Path, model: str) -> Agent
def create_plot_validator(phase: int, analysis_root: Path, model: str) -> Agent
def create_bibtex_validator(phase: int, analysis_root: Path, model: str) -> Agent
def create_rendering_reviewer(analysis_root: Path, model: str) -> Agent
def create_arbiter(phase: int, analysis_root: Path, model: str) -> Agent
```

Each factory:
1. Reads the corresponding `testarea/jfc/src/agents/{role}.md` for the system
   prompt body
2. Injects the target artifact path(s) so the reviewer reads from disk
3. Injects the relevant methodology sections (e.g., `06-review.md`,
   phase-specific sections from `03-phases.md`)
4. Instructs the reviewer to produce structured output:
   ```
   ## Findings

   ### Category A (Must Resolve)
   - [Finding text with evidence citation]

   ### Category B (Should Address)
   - ...

   ### Category C (Suggestions)
   - ...

   ## Verdict
   PASS | ITERATE | ESCALATE
   ```

**Reviewer tool grants:**
- `read_resource` — access conventions and methodology
- `execute_bash_command_with_confirmation` — **plot_validator only** (runs
  `lint_plots.py`, checks figure file sizes, counts figures)
- **No bash** for physics/critical/constructive reviewers (read-only)

**Evidence-based review mandate** (from `06-review.md`):
Every finding must cite specific evidence: a number, a file path, a line in
the artifact. Inject this as an explicit instruction into all reviewer system
prompts.

### `src/hepagent/agents/jfc/arbiter.py` (or extend reviewers.py)

```python
def create_arbiter(
    phase: int,
    analysis_root: Path,
    model: str,
) -> Agent:
    """
    Return an agent that adjudicates multiple reviewer outputs.

    Input: all reviewer finding documents (written to review/ directory)
    Output: ADJUDICATION.md in review/ with a structured table and final
            verdict: PASS | ITERATE | ESCALATE.
    """
```

Arbiter logic per `06-review.md`:
- If reviewers agree on severity → accept higher category
- If reviewers disagree → arbiter evaluates independently
- Plot validator RED FLAGS → automatically Category A
- Output: structured adjudication table with per-finding rationale

### `src/hepagent/agents/jfc/review_gate.py`

```python
async def run_review_gate(
    phase: int,
    analysis_root: Path,
    model: str = "cborg:claude-sonnet-4-5",
) -> ReviewGateResult:
    """
    Run all reviewers for the given phase concurrently, then run arbiter.

    Returns ReviewGateResult with:
    - verdict: Literal["PASS", "ITERATE", "ESCALATE"]
    - category_a_findings: list[str]
    - category_b_findings: list[str]
    - adjudication_path: Path  (path to ADJUDICATION.md)
    """
```

**Concurrency:** Use `asyncio.gather` to run independent reviewers in
parallel. Arbiter runs after all reviewer outputs are written to disk.

**Iteration protocol:**
- If verdict is ITERATE: return findings to orchestrator (T5 handles the loop)
- If verdict is ESCALATE: raise `PhaseEscalationError` (human needed)
- If verdict is PASS: return normally

**Output layout:**
```
{analysis_root}/phase{N}_{name}/review/
├── physics_review.md
├── critical_review.md
├── constructive_review.md
├── plot_validation.md
├── bibtex_validation.md   (phases 4a, 4b, 5)
├── rendering_review.md    (phase 5)
└── ADJUDICATION.md
```

### `src/hepagent/agents/jfc/__init__.py` (update)

Export `ReviewGateResult`, `run_review_gate`.

## Acceptance Criteria

- [x] `create_physics_reviewer(phase=1, ...)` returns an `Agent` with the
  correct system prompt (contains physics_reviewer.md body)
- [x] `run_review_gate(phase=1, ...)` calls all 4 phase-1 reviewers
- [x] Reviewer outputs are written to `review/` subdirectory
- [x] `ADJUDICATION.md` is produced by arbiter
- [x] Concurrent execution: Phase 1 runs 3 reviewers in parallel (verified
  by timing test or mock concurrency check)
- [x] `ReviewGateResult.verdict` is one of PASS/ITERATE/ESCALATE
- [x] Unit tests in `tests/agents/jfc/test_review_gate.py`
