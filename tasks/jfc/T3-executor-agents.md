# T3: Phase Executor Agent Definitions

**Priority:** P1 (core execution path)
**Depends on:** T1 (skill must exist to know what agents need)
**Blocks:** T5

## Goal

Define the HepAgent role agents for JFC's executor, note_writer, and
typesetter roles. Each is a stateless role agent instantiated on demand by
the orchestrator, given a phase-specific system prompt assembled from JFC's
template files.

## Design Principle

JFC's phase templates (`testarea/jfc/src/templates/phase*_claude.md`) are the
authoritative specifications for what each executor must do. Rather than
hard-coding those specs in Python, the role agent system prompt is assembled
at runtime from:
1. The agent role definition (`agents/executor.md`)
2. The phase-specific template (`templates/phase{N}_claude.md`)
3. The analysis physics prompt (`prompt.md`)
4. The upstream artifacts from prior phases

This keeps HepAgent's Python layer thin; domain knowledge stays in the JFC
spec files.

## Deliverables

### `src/hepagent/agents/jfc/` package

New sub-package for JFC-specific agent factories.

### `src/hepagent/agents/jfc/executor.py`

```python
def create_phase_executor(
    phase: int,
    analysis_root: Path,
    model: str = "cborg:claude-sonnet-4-5",
) -> Agent:
    """
    Return a role agent configured to execute a specific JFC phase.

    Assembles the system prompt from:
    - testarea/jfc/src/agents/executor.md  (role definition)
    - testarea/jfc/src/templates/phase{phase}_claude.md  (phase spec)
    - {analysis_root}/prompt.md  (physics prompt)
    - Upstream artifacts from prior phases (read from disk)
    """
```

**System prompt assembly (in order):**
1. Agent role definition (executor.md): identity, approach, general rules
2. Phase template: what to deliver, required checklist, anti-patterns
3. Physics prompt block: "PHYSICS PROMPT:\n{contents of prompt.md}"
4. Upstream artifacts block: "PRIOR PHASE ARTIFACTS:\n..." (STRATEGY.md,
   EXPLORATION.md, SELECTION.md, etc. depending on phase)
5. Working directory instruction: "Write all outputs to
   `{analysis_root}/phase{N}_{name}/outputs/`. Write analysis code to
   `{analysis_root}/phase{N}_{name}/src/`."

**Tools for phase executor agents:**
- `execute_bash_command_with_confirmation` (run pixi tasks, python scripts)
- `read_resource` (read conventions and methodology sections)
- `update_logbook` (append to experiment_log.md)
- `ask_user_for_info` (for data location, credentials, physics choices)

### `src/hepagent/agents/jfc/note_writer.py`

```python
def create_note_writer(
    phase: Literal["4a", "4b", "4c", "5"],
    analysis_root: Path,
    model: str = "cborg:claude-sonnet-4-5",
) -> Agent:
    """Return a role agent that writes the analysis note for a given phase."""
```

System prompt from `agents/note_writer.md` + phase-specific instructions.
The note writer reads all phase artifacts and produces the markdown AN file.
No bash execution tools; note writer is a prose-generation agent.

### `src/hepagent/agents/jfc/typesetter.py`

```python
def create_typesetter(
    analysis_root: Path,
    model: str = "cborg:claude-sonnet-4-5",
) -> Agent:
    """Return a role agent that compiles the analysis note to PDF."""
```

System prompt from `agents/typesetter.md`. Has bash execution tools.
Runs: `pandoc → postprocess_tex.py → tectonic`.

### `src/hepagent/agents/jfc/__init__.py`

Exports `create_phase_executor`, `create_note_writer`, `create_typesetter`.

## Phase → Artifact Mapping

| Phase | Executor artifact | Note writer artifact |
|-------|------------------|---------------------|
| 1 | `phase1_strategy/outputs/STRATEGY.md` | — |
| 2 | `phase2_exploration/outputs/EXPLORATION.md` | — |
| 3 | `phase3_selection/outputs/SELECTION.md` | — |
| 4a | `phase4a_inference_expected/outputs/INFERENCE_EXPECTED.md` | `ANALYSIS_NOTE_4a_v1.md` |
| 4b | `phase4b_inference_partial/outputs/INFERENCE_PARTIAL.md` | `ANALYSIS_NOTE_4b_v1.md` |
| 4c | `phase4c_inference_observed/outputs/INFERENCE_OBSERVED.md` | `ANALYSIS_NOTE_4c_v1.md` |
| 5 | (figure polish) | `ANALYSIS_NOTE_5_v1.md` |

## Acceptance Criteria

- [x] `create_phase_executor(phase=1, analysis_root=path)` returns an `Agent`
  without error
- [x] The assembled system prompt contains the executor.md role definition
- [x] The assembled system prompt contains the phase1_claude.md template body
- [x] Running the agent (mocked) writes a file to `phase1_strategy/outputs/`
- [x] `create_note_writer("4a", path)` returns an `Agent`
- [x] `create_typesetter(path)` returns an `Agent`
- [x] Unit tests in `tests/agents/jfc/test_executor.py` verify prompt assembly
