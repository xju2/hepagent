# JFC → HepAgent Implementation Tasks

## Context

JFC (Just Furnish Context) is a framework for autonomous HEP physics analysis
orchestration. It uses Claude Code sessions with CLAUDE.md harnesses to drive a
5-phase pipeline from physics prompt to publication-quality analysis note.

The goal here is to re-implement the JFC workflow in **HepAgent** using the
OpenAI Agent SDK — replacing the CLAUDE.md-harness approach with programmable
Python agents that are launched, composed, and reviewed via HepAgent's
agent infrastructure.

## JFC Workflow at a Glance

```
User physics prompt
        ↓
Phase 1: Strategy        → STRATEGY.md        [4-bot review]
Phase 2: Exploration     → EXPLORATION.md     [self + plot-validator]
Phase 3: Processing      → SELECTION.md       [1-bot review]
Phase 4a: Expected       → INFERENCE_EXPECTED + AN PDF   [4-bot+bib review]
Phase 4b: 10% Validation → INFERENCE_PARTIAL  + AN PDF   [HUMAN GATE]
Phase 4c: Full Data      → INFERENCE_OBSERVED + AN PDF   [1-bot review]
Phase 5: Documentation   → ANALYSIS_NOTE PDF  [5-bot review]
        ↓
Published Analysis Note (50-100 pages, reproducible via pixi run all)
```

Each phase: **Executor agent** produces artifact → **Reviewer agents** evaluate
→ **Arbiter** adjudicates → advance or iterate.

## Key Design Decisions

1. **No CLAUDE.md harnesses.** Orchestration logic lives in Python using the
   OpenAI Agent SDK. Phase transitions are code, not prompt instructions.

2. **Agents as Python objects.** JFC's 13 agent roles become HepAgent role
   agents (or specialized skilled agents) instantiated on demand.

3. **Artifacts on disk.** Each phase writes to a structured directory. Agents
   read prior artifacts explicitly; no conversation-history leakage.

4. **Review gates are async subagent calls.** Multiple reviewers run in
   parallel; arbiter synthesizes findings; orchestrator decides to advance or
   iterate.

5. **Skill-first entry.** HepAgent's skilled agent gains a `jfc` skill so users
   can trigger JFC workflows naturally ("Run a JFC analysis of Z→bb cross-section").

## Task Priority Order

| # | Task File | Priority | Depends On |
|---|-----------|----------|-----------|
| T1 | [jfc-skill.md](T1-jfc-skill.md) | P1 | — |
| T2 | [scaffold-tool.md](T2-scaffold-tool.md) | P1 | — |
| T3 | [executor-agents.md](T3-executor-agents.md) | P1 | T1 |
| T4 | [review-agents.md](T4-review-agents.md) | P2 | T1 |
| T5 | [orchestration-engine.md](T5-orchestration-engine.md) | P2 | T2, T3, T4 |
| T6 | [jfc-tools.md](T6-jfc-tools.md) | P2 | T2 |
| T7 | [commitment-tracking.md](T7-commitment-tracking.md) | P3 | T5 |
| T8 | [cli-integration.md](T8-cli-integration.md) | P3 | T5 |
