# JFC Agent Roles — HepAgent Reference Card

Maps each JFC agent role to its HepAgent Python instantiation.

---

## Executor Roles

### PhaseExecutorAgent
**JFC role:** executor
**HepAgent factory:** `create_phase_executor(phase, analysis_root, model)`
**Module:** `hepagent.agents.jfc.executor`
**System prompt:** executor.md + phase{N}_claude.md template + physics prompt + upstream artifacts
**Tools:** `execute_bash_command_with_confirmation`, `read_resource`, `update_logbook`, `ask_user_for_info`, JFC tools
**Use:** Implements each analysis phase. Writes code to `src/`, figures to `outputs/figures/`, artifact to `outputs/`.

### NoteWriterAgent
**JFC role:** note_writer
**HepAgent factory:** `create_note_writer(phase, analysis_root, model)`
**Module:** `hepagent.agents.jfc.note_writer`
**System prompt:** note_writer.md + phase-specific instructions + all prior artifacts
**Tools:** `read_resource` only — no bash execution
**Use:** Writes the analysis note markdown from all phase artifacts. Pure prose generation.

### TypesetterAgent
**JFC role:** typesetter
**HepAgent factory:** `create_typesetter(analysis_root, model)`
**Module:** `hepagent.agents.jfc.typesetter`
**System prompt:** typesetter.md + typesetting workflow instructions
**Tools:** `execute_bash_command_with_confirmation`
**Use:** Converts markdown AN to PDF via pandoc → postprocess_tex.py → tectonic.

---

## Reviewer Roles

All reviewer factories are in `hepagent.agents.jfc.reviewers`.

### PhysicsReviewerAgent
**JFC role:** physics_reviewer
**HepAgent factory:** `create_physics_reviewer(phase, analysis_root, model)`
**Context:** Physics prompt + artifact ONLY. No methodology spec, no conventions.
**Phases:** 1, 4a, 4b, 5
**Outputs to:** `phase{N}/review/physics_review.md`
**Verdict format:** Findings by category (A/B/C) + PASS/ITERATE/ESCALATE

### CriticalReviewerAgent
**JFC role:** critical_reviewer
**HepAgent factory:** `create_critical_reviewer(phase, analysis_root, model)`
**Context:** Full context: methodology §6 + applicable §3 phase + artifact + conventions
**Phases:** 1, 2, 3, 4a, 4b, 4c, 5 (all phases)
**Outputs to:** `phase{N}/review/critical_review.md`

### ConstructiveReviewerAgent
**JFC role:** constructive_reviewer
**HepAgent factory:** `create_constructive_reviewer(phase, analysis_root, model)`
**Context:** Same as critical reviewer
**Phases:** 1, 4a, 4b, 5
**Outputs to:** `phase{N}/review/constructive_review.md`

### PlotValidatorAgent
**JFC role:** plot_validator
**HepAgent factory:** `create_plot_validator(phase, analysis_root, model)`
**Context:** Artifact + figures directory + appendix-plotting.md conventions
**Phases:** 2, 3, 4a, 4b, 4c, 5
**Tools:** `execute_bash_command_with_confirmation` (runs `lint_plots.py`, checks figure files)
**Outputs to:** `phase{N}/review/plot_validation.md`
**Note:** RED FLAG findings are auto-Category A. Arbiter cannot downgrade.

### BibtexValidatorAgent
**JFC role:** bibtex_validator
**HepAgent factory:** `create_bibtex_validator(phase, analysis_root, model)`
**Context:** AN markdown file + references.bib
**Phases:** 4a, 4b, 5
**Outputs to:** `phase{N}/review/bibtex_validation.md`

### RenderingReviewerAgent
**JFC role:** rendering_reviewer
**HepAgent factory:** `create_rendering_reviewer(analysis_root, model)`
**Context:** Compiled PDF + rendering checklist from §6.4.3
**Phases:** 5 only
**Outputs to:** `phase5_documentation/review/rendering_review.md`

---

## Arbiter Role

**JFC role:** arbiter
**HepAgent factory:** `create_arbiter(phase, analysis_root, model)`
**Module:** `hepagent.agents.jfc.reviewers`
**Context:** All reviewer findings + artifact + conventions + §6 methodology
**Phases:** 1, 4a, 4b, 5 (4-bot review phases)
**Outputs to:** `phase{N}/review/ADJUDICATION.md`
**Verdict:** PASS | ITERATE (list Category A items) | ESCALATE

### Arbiter Logic (from §6.5.1)
- Reviewers agree on severity → accept at higher category
- Reviewers disagree → evaluate independently and document rationale
- Only one reviewer raised finding → assess independently (not less important)
- All reviewers missed something → arbiter raises it
- Plot validator RED FLAGS → automatically Category A (cannot be downgraded)
- Every dismissal must include: cost estimate (agent-hours), physics justification, future phase commitment

---

## Fixer Role

**JFC role:** fixer
**HepAgent factory:** `run_fixer(state, phase, findings, model)` (async function, not Agent factory)
**Module:** `hepagent.agents.jfc.fixer`
**Context:** Arbiter verdict + existing artifact + existing code + experiment_log
**Tools:** `execute_bash_command_with_confirmation`, `read_resource`
**Use:** Spawned during ITERATE cycles to address Category A/B findings with minimum effective changes.

---

## Investigator Role

**JFC role:** investigator
**HepAgent factory:** `run_investigator(state, regression_phase, symptom)` (async function)
**Module:** `hepagent.agents.jfc.investigator`
**Context:** Regression trigger + all artifacts from trigger phase forward
**Outputs:** `REGRESSION_TICKET.md` in `phase{N}/review/`
**Use:** Spawned when regression is triggered to scope the fix and identify affected downstream phases.

---

## Review Gate

**HepAgent function:** `run_review_gate(phase, analysis_root, model)` → `ReviewGateResult`
**Module:** `hepagent.agents.jfc.review_gate`
**Concurrency:** Independent reviewers run in parallel via `asyncio.gather()`; arbiter runs after all outputs are written
**Returns:** `ReviewGateResult(verdict, category_a_findings, category_b_findings, adjudication_path)`

### Review Gate Composition by Phase

| Phase | Reviewers (parallel) | Arbiter |
|-------|---------------------|---------|
| 1 | physics + critical + constructive | Yes |
| 2 | plot_validator only | No |
| 3 | critical + plot_validator | No |
| 4a | physics + critical + constructive + plot_validator + bibtex | Yes |
| 4b | physics + critical + constructive + plot_validator + bibtex | Yes → human gate |
| 4c | critical + plot_validator | No (escalate to 4-bot if surprises) |
| 5 | physics + critical + constructive + plot_validator + bibtex + rendering | Yes |

---

## Model Recommendations

- Executor agents: `cborg:claude-sonnet-4-5` (default)
- Reviewer agents: `cborg:claude-sonnet-4-5`
- Arbiter: `cborg:claude-sonnet-4-5` (consider opus for complex adjudications)
- Note writer: `cborg:claude-sonnet-4-5`
- Typesetter: `cborg:claude-sonnet-4-5`
