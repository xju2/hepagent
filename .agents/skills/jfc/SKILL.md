---
name: jfc
description: "Orchestrate a full autonomous HEP physics analysis using the JFC framework: from a physics prompt through strategy, exploration, processing, statistical inference, and publication-quality analysis note."
tools:
  - scaffold_jfc_analysis
  - run_pixi_task
  - list_pixi_tasks
  - read_phase_artifact
  - write_phase_artifact
  - append_experiment_log
  - read_commitments
  - update_commitments
  - validate_figures
  - list_phase_figures
  - compile_analysis_note
---

# JFC: Just Furnish Context — HEP Analysis Orchestration

## What JFC Is

JFC is a 5-phase autonomous HEP physics analysis pipeline that drives a
physics prompt to a publication-quality analysis note (50–100 pages).
Each phase produces a self-contained artifact reviewed by independent
subagents before the next phase begins. The pipeline spans strategy
definition, data exploration, event selection and correction, statistical
inference on expected, 10% validation, and full-data results, culminating
in a typeset PDF analysis note.

## When to Use This Skill

Activate this skill when the user asks to:
- Run a physics analysis (measurement or search) on HEP data
- Produce a measurement result with uncertainties (cross-section, coupling, etc.)
- Write an analysis note for a physics result
- Orchestrate a multi-phase analysis pipeline
- Resume or check the status of a running JFC analysis

## Correctness-First Mandate

> **Quality bar: publication-ready. Correctness above all else.**
> The single most important property of an analysis is that it produces
> the right answer with honest uncertainties. No amount of completeness,
> polish, or thoroughness compensates for a wrong result. A slow, correct
> analysis is infinitely more valuable than a fast, wrong one.
> — JFC Methodology §1

This is non-negotiable. Agents must spend as much time as needed — hours,
iterations, multiple approaches — to be confident the result is correct.

## Phase Overview

| Phase | Name | Primary Artifact | Review Tier |
|-------|------|-----------------|-------------|
| 1 | Strategy | `STRATEGY.md` | 4-bot (physics + critical + constructive + arbiter) |
| 2 | Exploration | `EXPLORATION.md` | Self-review + plot validator |
| 3 | Processing | `SELECTION.md` | 1-bot (critical + plot validator) |
| 4a | Expected Results | `INFERENCE_EXPECTED.md` + AN PDF | 4-bot + bibtex |
| 4b | 10% Validation | `INFERENCE_PARTIAL.md` + AN PDF | 4-bot + bibtex → human gate |
| 4c | Full Data | `INFERENCE_OBSERVED.md` + AN PDF | 1-bot (or 4-bot if surprises) |
| 5 | Documentation | `ANALYSIS_NOTE_5_v1.md` + PDF | 5-bot |

Every phase boundary is a hard gate: Phase N+1 cannot begin until Phase N
has produced its artifact AND passed review. Skipping review is a process
failure.

## Binding Commitments Rule

Phase 1 produces commitments labelled [D1], [D2], ..., [DN] in
`STRATEGY.md` and tracked in `COMMITMENTS.md`. These commitments are:
- **Systematic sources** that will be implemented
- **Cross-checks** that will be performed
- **Comparison targets** (published measurements, theory predictions)
- **Method choices** (extraction method, correction strategy)

Every commitment must be either:
- **Resolved** ([x]) with evidence at the phase where it was addressed
- **Downscoped** ([D]) with documented attempt + specific failure reason

Silent absence of a commitment at Phase 4a or 5 is Category A at review.
The orchestrator maintains `COMMITMENTS.md` and checks it before each phase.

## Anti-Fabrication Rules (Non-Negotiable)

These rules apply to every executor, and violation is Category A at review:

1. **No parameter tuned to improve visual agreement.** Every parameter
   must have a PRIOR justification (measurement, convention, optimization
   criterion). "This value makes the plot match" is fabrication.

2. **No systematic dropped because it was large.** If a variation produces
   a large shift, that IS the systematic. Dropping it is fabrication.

3. **Every formula cites a source or derives step-by-step.** "It can be
   shown that" is banned. Show the steps or cite the source.

4. **Closure test failures are method failures, not rationalizations.**
   chi2/ndf > 3 or any pull > 5-sigma → method does not work. Do not
   rationalize or paper over. Fix the method or formally abandon.

5. **No uncertainty band smoothed or adjusted for visual appearance.**
   The uncertainty is what the calculation gives.

## Agent Call Sequence

```
scaffold_jfc_analysis(analysis_name, physics_prompt, analysis_type)
    → Phase 1 executor → run_review_gate(phase=1)
    → Phase 2 executor → run_review_gate(phase=2)
    → Phase 3 executor → run_review_gate(phase=3)
    → check_phase1_commitments() [gate before Phase 4a]
    → Phase 4a executor + note_writer → compile_analysis_note() → run_review_gate(phase="4a")
    → Phase 4b executor + note_writer → compile_analysis_note() → run_review_gate(phase="4b") → human gate
    → Phase 4c executor + note_writer → compile_analysis_note() → run_review_gate(phase="4c")
    → Phase 5 executor + note_writer + typesetter → run_review_gate(phase=5)
    → published analysis note PDF
```

Each phase runs via `run_jfc_analysis()` in `hepagent.agents.jfc.orchestrator`.
The orchestrator handles iteration (up to 3 ITERATE cycles before escalating),
human gates, state persistence, and git commits after each phase.

## Loading This Skill

Call `load_skill_details("jfc")` before starting any JFC analysis orchestration.
Then read resources as needed:
- `read_resource("methodology-summary")` — condensed methodology (entry/exit criteria, review tiers)
- `read_resource("agent-roles")` — reference card mapping JFC roles to HepAgent agents

## Orchestration via Python

JFC analyses are orchestrated entirely in Python using the OpenAI Agent SDK:
```python
from hepagent.agents.jfc.orchestrator import run_jfc_analysis
path = await run_jfc_analysis(
    analysis_name="z_boson_xsec",
    physics_prompt="Measure the Z→bb cross-section at √s=91 GeV",
    analysis_type="measurement",
)
```

Or via CLI: `hepagent jfc run --name z_boson_xsec --type measurement --prompt "..."`
