# T1: JFC Skill Definition

**Priority:** P1 (Foundation — everything else builds on this)
**Depends on:** nothing
**Blocks:** T3, T4

## Goal

Create a HepAgent skill that gives the skilled agent (`--agent scientist`)
the knowledge and vocabulary to orchestrate a JFC physics analysis.

## Why This First

HepAgent's skilled agent is the user-facing entry point. Before any code is
written, the agent needs a skill manifest that:
- Defines what a JFC analysis is
- Lists what the agent can and cannot do at each phase
- Points to JFC-specific tools the agent may call
- Sets the correctness-first philosophy from `methodology/01-principles.md`

## Deliverables

### `.agents/skills/jfc/SKILL.md`

YAML frontmatter fields:
```yaml
name: jfc
description: "Orchestrate a full autonomous HEP physics analysis using the JFC framework: from a physics prompt through strategy, exploration, processing, statistical inference, and publication-quality analysis note."
tools:
  - scaffold_jfc_analysis
  - run_phase
  - run_review_gate
  - advance_phase
  - get_phase_status
  - read_artifact
  - append_experiment_log
```

Body must cover:
1. **What JFC is** (1 paragraph): 5-phase autonomous HEP analysis pipeline.
2. **When to use this skill**: User asks to run a physics analysis, produce a
   measurement/search result, write an analysis note.
3. **Correctness-first mandate**: Quote the key principle from
   `methodology/01-principles.md` — wrong results are failures regardless of
   polish.
4. **Phase overview table**: Phase name, artifact produced, review tier.
5. **Binding commitments rule**: Phase 1 commitments [D1]-[DN] must be
   resolved or explicitly downscoped at every downstream phase.
6. **Anti-fabrication rules** (non-negotiable list from methodology):
   - No parameter tuned to improve visual agreement
   - No systematic dropped because it was large
   - Every formula cites a source or derives step-by-step
   - Closure test failures are method failures, not rationalizations
7. **Agent call sequence** (abbreviated orchestration loop):
   ```
   scaffold → phase1_executor → review_gate(phase=1) → phase2_executor → ...
   ```
8. **When to call `load_skill_details("jfc")`**: Before starting any JFC
   analysis orchestration.

### `.agents/skills/jfc/LOGBOOK.md`

Empty initial logbook (will be populated as analyses are run).

### `.agents/skills/jfc/resources/methodology-summary.md`

A condensed (~5-page) summary of `testarea/jfc/src/methodology/` covering:
- Phase entry/exit criteria
- Review tier per phase (which reviewers, which artifacts)
- Artifact format requirements (experiment_log, STRATEGY.md structure, etc.)
- Commitment tracking format ([D1]-[DN] convention)
- Downscoping rules (attempt first, document failures, downscope last)

This is what an executor agent reads via `read_resource("jfc/methodology-summary")`.

### `.agents/skills/jfc/resources/agent-roles.md`

A reference card mapping each JFC agent role to its HepAgent instantiation:
- executor → `PhaseExecutorAgent` (role agent, phase-specific system prompt)
- physics_reviewer, critical_reviewer, constructive_reviewer → reviewer role agents
- arbiter → `ArbiterAgent`
- plot_validator, bibtex_validator, rendering_reviewer → specialized role agents
- note_writer → `NoteWriterAgent`
- typesetter → `TypesetterAgent`

## Acceptance Criteria

- [ ] `load_skill_details("jfc")` returns the skill manifest without error
- [ ] `read_resource("jfc/methodology-summary")` returns condensed methodology
- [ ] `read_resource("jfc/agent-roles")` returns the role reference card
- [ ] The skill manifest passes lint (valid YAML frontmatter, non-empty body)
- [ ] The body correctly reflects anti-fabrication rules from
  `testarea/jfc/src/methodology/01-principles.md` (verify by diff)
