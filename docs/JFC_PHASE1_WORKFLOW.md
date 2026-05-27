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

    subgraph Gate["Review Gate (max_turns=20)"]
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

    Verdict -->|ESCALATE| Escalate
    Verdict -->|ITERATE\nCat-A/B findings| Fixer
    Fixer["Fixer Agent\n──────────────\nminimal targeted fixes\nfor Cat-A/B findings"]
    Fixer -->|updates STRATEGY.md\nmax 3 iterations| Gate

    Verdict -->|PASS| Codesign{Codesign\nenabled?}

    Codesign -->|no| Done
    Codesign -->|yes| CodesignAgent

    subgraph CodesignGate["Codesign Gate (optional, --codesign)"]
        CodesignAgent["Codesign Agent\n──────────────\nwrites human-readable summary\nfacilitates physicist review\nrecords OPEN concerns\ntools: read_file, write_review,\nask_user_for_info"]
        CodesignAgent -->|writes| CodesignArtifacts
        CodesignArtifacts[("phase1_strategy/codesign/\nCODESIGN_SUMMARY.md\nHUMAN_FEEDBACK.md")]
        CodesignArtifacts --> CodesignVerdict{Human\nVerdict}
    end

    CodesignVerdict -->|APPROVE| Done
    CodesignVerdict -->|REVISE| ReviseExecutor["Re-run Executor\nwith codesign_feedback\ninjected into prompt"]
    ReviseExecutor --> Gate

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
| `phase1_strategy/codesign/CODESIGN_SUMMARY.md` | Codesign Agent (optional) |
| `phase1_strategy/codesign/HUMAN_FEEDBACK.md` | Codesign Agent (optional) |

## `max_turns` defaults

Each agent in the orchestration stack has a default turn budget that can be overridden globally via `run_jfc_analysis(max_turns=N)`. When `max_turns` is `None` the per-agent defaults apply:

| Agent | Default turns |
|---|---|
| Executor | 50 |
| Note Writer / Typesetter | 30 |
| Review Gate (all reviewers + arbiter) | 20 |
| Fixer | 30 |
| Investigator | 20 |
