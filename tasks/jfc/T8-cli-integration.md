# T8: CLI and TUI Integration

**Priority:** P3
**Depends on:** T5
**Blocks:** —

## Goal

Expose JFC analysis orchestration through HepAgent's existing CLI (`hepagent`)
and Textual TUI so users can launch, monitor, and resume analyses without
writing Python.

## Deliverables

### `hepagent jfc` subcommand (`src/hepagent/main.py`)

```
hepagent jfc run --name z_boson_xsec --type measurement \
    --prompt "Measure the Z→bb cross-section at √s=91 GeV"

hepagent jfc resume --name z_boson_xsec --from-phase 3

hepagent jfc status --name z_boson_xsec

hepagent jfc list
```

**`hepagent jfc run`:**
- Calls `scaffold_jfc_analysis` then `run_jfc_analysis`
- Streams executor/reviewer progress to terminal
- Pauses at human gate and prompts interactively
- Saves state to `.orchestration_state.json` on exit/crash

**`hepagent jfc resume`:**
- Loads state from `.orchestration_state.json`
- Calls `run_jfc_analysis(start_from_phase=N)`
- Skips scaffold step

**`hepagent jfc status`:**
- Reads `.orchestration_state.json` + git log
- Prints phase completion table:
  ```
  Phase 1: Strategy      ✓ PASS  (3 review iterations)
  Phase 2: Exploration   ✓ PASS
  Phase 3: Processing    → IN PROGRESS
  Phase 4a: Expected     ○ pending
  ...
  ```

**`hepagent jfc list`:**
- Lists analyses in `analyses/` directory with current phase and status.

### Textual TUI integration

In `agents/textual.py`, add a JFC-specific step renderer:
- Display current phase name and executor progress
- Show review gate status (which reviewers running, verdicts received)
- Highlight Category A findings in red, Category B in yellow
- Show commitment tracking table when relevant

This is a visual enhancement only — the core orchestration runs the same.
If TUI changes are complex, defer this sub-item and run JFC analyses via CLI only.

### Progress reporting via `run_jfc_analysis`

`run_jfc_analysis` should accept an optional `progress_callback`:
```python
async def run_jfc_analysis(
    ...,
    progress_callback: Callable[[str, str], None] | None = None,
    # (phase_name, status_message)
) -> Path:
```

CLI and TUI pass different callbacks; this keeps the engine decoupled.

### Error handling and user communication

- `MaxIterationsExceeded`: Print full finding list, suggest `hepagent jfc resume`
- `PhaseEscalationError`: Print escalation findings, wait for user to resolve
  manually, then resume
- `CommitmentsNotResolved`: Print pending commitments table with instructions
- Keyboard interrupt (Ctrl-C): Save state cleanly before exiting

## Acceptance Criteria

- [ ] `hepagent jfc --help` shows the subcommand group
- [ ] `hepagent jfc run --name test --type measurement --prompt "..."` starts
  the analysis (even if it fails on the first real phase due to missing data)
- [ ] `hepagent jfc status --name test` prints the phase table
- [ ] `hepagent jfc resume --name test --from-phase 2` skips phase 1
- [ ] Ctrl-C during `hepagent jfc run` saves state and exits cleanly
- [ ] `hepagent jfc list` lists analyses in `analyses/`
- [ ] Integration test: `tests/cli/test_jfc_cli.py` (mocked orchestrator)
