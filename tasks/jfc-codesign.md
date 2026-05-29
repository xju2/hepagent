# Task: JFC Codesign of Physics Analysis Strategy
Add an option `--codesign` to the CLI command: `hepagent jfc run` so that after the arbiter generates the analysis stretegy and verdits a **PASS** statement but before executing the Phase 2, the `hepagent` will use an agent to write down a human-readable summary of the analysis strategy highlighting the physics insights and key technical choices. The summary should be concise and clear. The summary should also include any assumptions or limitations of the analysis strategy, as well as any potential areas for improvement or further investigation. The agent will invite users to read the summary, ask any questions about the analysis strategy, or provide any feedback before proceeding to execute the Phase 2. The agent will also be responsible for addressing any questions or feedback from the users solely based on the analysis strategy NOT the summary and incorporating open questions into a human-feedback artifact.
The human-feedback artifact together with the analysis strategy will be reviewed again by the arbiter. The arbiter will then provide a final verdict on whether to proceed with executing Phase 2 or to go back to the drawing board and revise the analysis strategy.

## Progress report

**Status: Complete**

### What was implemented

1. **`src/hepagent/agents/jfc/codesign.py`** (new file):
   - `create_codesign_agent()` — reads STRATEGY.md, writes CODESIGN_SUMMARY.md, interacts with the user via `ask_user_for_info`, answers questions solely from STRATEGY.md, and records all open questions in HUMAN_FEEDBACK.md
   - `create_codesign_arbiter()` — reviews STRATEGY.md + HUMAN_FEEDBACK.md and writes CODESIGN_ADJUDICATION.md with verdict PASS or ITERATE
   - `_parse_codesign_verdict()` — parses adjudication file, returns "PROCEED" or "REVISE"
   - `run_codesign_gate()` — orchestrates the two-step flow, returns "PROCEED" or "REVISE"

2. **`src/hepagent/agents/jfc/orchestrator.py`** (modified):
   - Added `codesign: bool = False` parameter to `run_jfc_analysis()`
   - After Phase 1 PASS, if `codesign=True`: calls `run_codesign_gate()`; if verdict is REVISE, removes Phase 1 from completed phases and re-runs it via `run_phase_with_review()`; the codesign gate does not repeat after the re-run

3. **`src/hepagent/main.py`** (modified):
   - Added `--codesign` flag to `hepagent jfc run` subcommand
   - Passes `codesign=codesign` to `run_jfc_analysis()`

4. **`src/hepagent/agents/jfc/__init__.py`** (modified):
   - Exports `run_codesign_gate`

5. **`tests/agents/jfc/test_codesign.py`** (new file):
   - 17 tests covering agent creation, verdict parsing, gate orchestration, and CLI flag

### Artifacts produced per analysis
- `phase1_strategy/codesign/CODESIGN_SUMMARY.md` — human-readable strategy summary
- `phase1_strategy/codesign/HUMAN_FEEDBACK.md` — Q&A log with open/resolved items
- `phase1_strategy/codesign/CODESIGN_ADJUDICATION.md` — arbiter verdict
