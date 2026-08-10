# Agent Definitions

Self-contained definitions for every agent role in the analysis pipeline.
Each file is a complete, auditable specification: role, inputs, outputs,
methodology references, and prompt template.

The orchestrator uses these definitions when spawning subagents.

**Which agents run where is not fixed here.** It is authored in the analysis
plan (`plan.json`): each node names its own reviewers, whether an arbiter
adjudicates it, and whether it writes an analysis note. The tables below describe
the shipped `jfc-measurement` / `jfc-search` templates, which is what an analysis
gets by default — not a constraint on what an analysis may be. Run
`hepagent jfc plan show --name X` to see a particular analysis.

## Planning agents

| Agent | File | Role |
|-------|------|------|
| Architect | `architect.md` | Propose the plan's shape from the physics prompt, before any work begins |

## Executor agents

| Agent | File | Role |
|-------|------|------|
| Executor | `executor.md` | Node execution — plan, code, figures, artifacts |
| Note writer | `note_writer.md` | AN prose — reads artifacts, writes the analysis note |
| Fixer | `fixer.md` | Targeted fixes for review findings and regression tickets |

## Reviewer agents

| Agent | File | Role |
|-------|------|------|
| Physics reviewer | `physics_reviewer.md` | Senior collaboration member review ("would I approve?") |
| Critical reviewer | `critical_reviewer.md` | Find all flaws in correctness and completeness |
| Constructive reviewer | `constructive_reviewer.md` | Strengthen the analysis — clarity, validation, presentation |
| Plot validator | `plot_validator.md` | Code linting + visual inspection of rendered figures |
| BibTeX validator | `bibtex_validator.md` | Verify citations resolve to real, accurate bibliographic records |
| Rendering reviewer | `rendering_reviewer.md` | PDF compilation and rendering inspection |

## Adjudication agents

| Agent | File | Role |
|-------|------|------|
| Arbiter | `arbiter.md` | Adjudicate reviews — PASS / ITERATE / ESCALATE |
| Investigator | `investigator.md` | Regression investigation and scoped fix tickets |

## Specialist agents

| Agent | File | Role |
|-------|------|------|
| Typesetter | `typesetter.md` | LaTeX expert for PDF production |

## Node activation

### Execution pipeline

Each node runs its execution agents in sequence. A node that declares
`produces_note` runs the note writer, and the typesetter after it.

| Node (default template) | Step 1 | Step 2 | Step 3 |
|-------|--------|--------|--------|
| strategy | executor | | |
| exploration | executor | | |
| selection | executor | | |
| inference_expected | executor | note writer (AN v1) | typesetter (compile) |
| inference_partial | executor | note writer (update AN) | typesetter (compile) |
| inference_observed | executor | note writer (update AN) | |
| documentation | executor | note writer (final AN) | typesetter (final PDF) |

The fixer replaces the executor during ITERATE cycles at any node.

### Review panel by node

"x" = agent is active at that node's review gate. This table is the shipped
default; the authority is `PlanNode.reviewers` and `PlanNode.arbiter`.

| Agent | strategy | exploration | selection | inf_expected | inf_partial | inf_observed | documentation |
|-------|-----|-----|-----|------|------|------|-----|
| Physics reviewer | x | | | x | x | | x |
| Critical reviewer | x | | x | x | x | x | x |
| Constructive reviewer | x | | | x | x | | x |
| Plot validator | | x | x | x | x | x | x |
| BibTeX validator | | | | x | x | | x |
| Rendering reviewer | | | | | | | x |
| Arbiter | x | | | x | x | | x |

A node that names no reviewers still gets the critical reviewer, so nothing
passes entirely unexamined.

**Plot validator** runs at every node that produces figures (everything after
strategy, in the default template). At `exploration` (self-review), it runs
alongside the executor's self-check. At `strategy` it is skipped — a strategy
node typically has no figures.

The plot validator has two modes: (1) code linting — greps scripts for
mechanical violations, and (2) visual validation — reads rendered PNGs
and checks readability, overlap, label quality, and layout. Both modes
run every time. The visual validator must enumerate every figure by name.

**BibTeX validator** runs at the note-writing nodes whose citations are new or
revised — in the default template, `inference_expected`, `inference_partial` and
`documentation`. It verifies DOIs, arXiv IDs, and INSPIRE records actually
resolve to the expected papers, catching hallucinated entries.

**Rendering reviewer** runs only at the final documentation node, where the PDF
is the deliverable. At the earlier note-writing nodes, the typesetter's
compilation serves as the rendering check.

## Review panel composition

| Review tier | Nodes | Parallel agents | Then |
|-------------|--------|----------------|------|
| 4-bot | strategy | physics + critical + constructive | arbiter |
| 4-bot+bib | inf_expected, inf_partial | physics + critical + constructive + plot validator + bibtex | arbiter |
| 5-bot | documentation | physics + critical + constructive + plot validator + rendering + bibtex | arbiter |
| 1-bot | selection, inf_observed | critical + plot validator | (no arbiter) |
| Self | exploration | executor self-check + plot validator | — |

## Context assembly

Context assembly follows §3a.4 (three layers: bird's-eye framing, relevant
methodology sections, upstream artifacts). Which upstream artifacts a node sees
is the plan's `requires` and `informs` edges, and how much of each it sees is the
edge's `inject` setting.

Each node's prompt is written to its working directory as `CLAUDE.md` at scaffold
time, so what an agent reads at runtime is the editable prompt from `plan.json` —
not a file in `templates/`. These definitions specify how the *orchestrator*
launches agents that will read those prompts.
