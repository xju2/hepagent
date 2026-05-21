# JFC Phase 1 — Strategy Workflow

```mermaid
flowchart TD
    User([User / CLI]) --> Orch

    Orch["Orchestrator\nrun_phase_with_review(phase=1)"]
    Orch --> Executor

    Executor["Executor Agent\n(phase1_claude.md template)\n─────────────────────\n• physics motivation\n• ≥3 corpus queries\n• selection approaches\n• systematic plan\n• reference table\n─────────────────────\ntools: bash, read_file,\nread_resource, update_logbook,\nask_user_for_info, JFC tools"]
    Executor -->|writes| Strategy

    Strategy[("phase1_strategy/outputs/\nSTRATEGY.md")]
    Strategy --> Gate

    subgraph Gate["Review Gate"]
        subgraph Parallel["Parallel Reviewers"]
            PR["Physics Reviewer\n──────────────\nphysics prompt + artifact\n(no extra tools)"]
            CR["Critical Reviewer (bad cop)\n──────────────\nmethodology §6, phase spec §3,\ncommitments, artifact\ntools: read_file"]
            GR["Constructive Reviewer (good cop)\n──────────────\nmethodology §6, artifact\ntools: read_file"]
        end

        PR -->|physics_review.md| Arbiter
        CR -->|critical_review.md| Arbiter
        GR -->|constructive_review.md| Arbiter

        Arbiter["Arbiter\n──────────────\nadjudicates all reviewer outputs\ntools: read_file, read_resource"]
        Arbiter -->|writes| Adj
        Adj[("ADJUDICATION.md")]
    end

    Adj --> Verdict{Verdict}

    Verdict -->|PASS| Done
    Verdict -->|ESCALATE| Escalate

    Verdict -->|ITERATE\nCat-A/B findings| Fixer
    Fixer["Fixer Agent\n──────────────\nminimal targeted fixes\nfor Cat-A/B findings"]
    Fixer -->|updates STRATEGY.md\nmax 3 iterations| Gate

    Escalate(["PhaseEscalationError\n(human review)"])

    Done["Phase 1 complete\n.orchestration_state.json updated\ngit commit: phase/1: executor + review PASS"]
    Done --> Phase2([Phase 2 unlocked])
```

## Artifacts

| File | Written by |
|---|---|
| `phase1_strategy/outputs/STRATEGY.md` | Executor |
| `phase1_strategy/review/physics_review.md` | Physics Reviewer |
| `phase1_strategy/review/critical_review.md` | Critical Reviewer |
| `phase1_strategy/review/constructive_review.md` | Constructive Reviewer |
| `phase1_strategy/review/ADJUDICATION.md` | Arbiter |
