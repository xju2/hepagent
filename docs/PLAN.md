# Analysis Plan

Every JFC analysis is driven by a plan: a small JSON document at
`<analysis_root>/plan.json` listing the units of work and the dependencies
between them. The plan is what the user authors — the seven-phase JFC pipeline
is one *template*, not the shape of the runtime.

Read this before changing plan behaviour, the way `docs/GRAPH.md` governs the
provenance graph and `docs/WEB.md` the web UI. The invariants below are
load-bearing.

The plan and the graph are different documents with different jobs:

| | Plan | Graph |
|---|---|---|
| Question | what *will* happen | what *did* happen |
| Mutability | edited freely, whole-document writes | append-only, never rewritten |
| Authored by | the user (with the architect proposing) | the runtime and the executors |
| Lives in | `plan.json` + `plan.history/` | `graph/*.jsonl` |

The plan **compiles into** the graph (`plan/compile.py`), which is how a plan
edit becomes a change in execution order. Nothing goes the other way.

---

## Files

```
<analysis_root>/
    plan.json           the current plan
    plan.history/
        plan.0001.json  a copy of every previous revision
        plan.0002.json
```

`save_plan` writes the whole document, bumps `revision`, stamps `updated_at`,
and archives the *previous* contents into `plan.history/` first. Nothing is
edited in place, so a bad edit is always one file copy away from being undone.
`load_revision(root, n)` reads one back.

`plan.json` is written before `git init` during scaffolding, so it lands in the
first commit alongside the graph.

---

## The schema

`hepagent/plan/schema.py`. Everything the runtime needs to execute a node lives
*on* the node, so adding a node to the plan is the whole of adding a step to the
analysis.

### `PlanNode`

| Field | Meaning |
|---|---|
| `id` | Author-chosen stable slug — `strategy`, `selection_ee`. Never derived from the label, so renaming a label cannot orphan edges. |
| `label` | Display name in the editor and in listings |
| `directory` | Working directory, relative to the analysis root |
| `artifact` | Primary output filename, written to `<directory>/outputs/` |
| `note_artifact` | The analysis note, when that is a different file from the primary artifact |
| `prompt` | The markdown spec the executor runs. **This is the text a user edits to change what the node does.** |
| `kind` | `work` runs an agent; `gate` only interposes a check |
| `role` | `executor`, `note_writer`, `typesetter` |
| `context_paths` | Ambient files injected alongside upstream artifacts (`COMMITMENTS.md`). Carry no dependency — read if present, never delay the node. |
| `reviewers` | Reviewer names the review gate runs. One of `physics`, `critical`, `constructive`, `plot`, `rendering`, `bibtex`. |
| `arbiter` | Whether an arbiter adjudicates this node's reviews |
| `produces_note` | Whether the node runs the note writer and typesetter |
| `gates` | Interposed checks, see below |
| `contract` | What this node may write back into the provenance graph |
| `max_iterations` | Review iterations before escalating |
| `model` | Optional `provider:model` override for this node |
| `tools` | Optional tool allowlist; `None` means the default set for `role` |
| `metadata` | Free-form. The editor stores layout coordinates here under `x`/`y`. |

Derived paths — `artifact_path`, `note_path`, `note_pdf_path`, `outputs_dir` —
are properties, so no caller reconstructs them by string concatenation.

### `PlanEdge`

```json
{"upstream": "strategy", "downstream": "exploration", "kind": "requires", "inject": "full"}
```

| Field | Meaning |
|---|---|
| `upstream` | Node that produces |
| `downstream` | Node that consumes |
| `kind` | `requires` blocks and orders execution; `informs` injects the artifact when it happens to exist but never delays the node |
| `inject` | `full`, `summary` or `none` — how much of the upstream artifact enters the downstream prompt |

**Edge direction is the single sharpest hazard in this code.** A plan edge reads
in *data-flow* order; a `requires` edge in the provenance graph reads in
*dependency* order and puts the **dependent** node in `src`. The two are
inverted. That is why the plan names its endpoints `upstream`/`downstream` and
never `src`/`dst`, and why `plan/compile.py` is the only place the inversion
happens.

### `PlanGate`

```json
{"name": "commitments", "when": "before", "enabled": true}
```

`name` is one of `commitments` (block until every commitment is closed), `human`
(ask a person to approve) or `codesign` (the interactive strategy review).
`when` is `before` (block the node from starting) or `after` (judge what it
produced) — the JFC gates are not all of one kind, so position is declared
rather than implied by the name.

`enabled: false` ships for gates a CLI flag turns on: `--codesign` flips data
rather than branching on a node id.

---

## Validation

`hepagent/plan/validate.py`. `validate_plan(plan, root)` returns a
`GraphValidationReport` — the *same* record type the graph validator returns, so
the severity model, the `.blocking`/`.advisory` split and the rendering are
shared rather than reimplemented.

| Rule | Checks | Severity |
|---|---|---|
| P1-ids | Node ids are unique and slug-safe | error |
| P2-outputs | No two nodes write the same artifact; no directory escapes the root | error |
| P3-edges | Edge endpoints exist; no duplicates; no self-edges | error |
| P4-acyclic | The `requires` subgraph is acyclic — reports the cycle | error |
| P5-entry | Exactly one starting point | warning |
| P6-vocabulary | Roles, gates, reviewers, edge kinds and inject modes are recognised | error |
| P7-contract | Write-back contracts name real graph node and edge types | error |
| P8-notes | A `produces_note` node produces markdown | error |
| P9-reachable | Every node is downstream of an entry node | warning |

**Severity is the gate**, exactly as in the graph: an `error` blocks — the
editor disables *Approve & run*, `jfc plan validate` exits 1, and the
orchestrator refuses to start. A `warning` is advisory. A plan with two entry
nodes (P5) or an unreachable node (P9) is unusual but not wrong; a plan with a
cycle (P4) cannot execute at all.

### A plan is executable data

`node.directory` becomes a real `mkdir` and a real `CLAUDE.md` write. A plan is
therefore checked by `orchestrator.require_runnable_plan` **before anything
touches the filesystem**, and that check covers every source — `--plan`, the
editor, and a `plan.json` already on disk. A hand-edited `plan.json` is exactly
as unchecked as one passed on the command line.

`--plan` in particular bypasses the editor, which is what normally refuses a
blocking plan. Without the check, a plan declaring `directory: "../other-project"`
scaffolded itself over a sibling directory: P2 catches it, but nothing was asking
P2. `scaffold._node_dir` re-checks containment at the point of writing, so a
caller that skips validation still cannot escape the root.

---

## How the plan drives the runtime

Every table that used to hardcode the seven phases now reads the plan. The
pattern is the same at each call site: a `PlanNode` is passed where a phase key
used to be looked up.

| Concern | Read from |
|---|---|
| Execution order | `requires` edges, via `plan_to_graph` → `planner.py` |
| Tiebreak between equally-ready nodes | the plan's node declaration order |
| Working directory, artifact name | `node.directory`, `node.artifact` |
| Executor prompt body | `node.prompt` |
| Upstream artifacts in the prompt | `plan.upstream_edges(node)`, honouring `inject` |
| Ambient files in the prompt | `node.context_paths` |
| Reviewer set, arbiter | `node.reviewers`, `node.arbiter` |
| Gates | `node.gates_at("before")` / `("after")` |
| Graph write-back allowance | `node.contract` |
| Note generation and PDF | `node.produces_note`, `node.note_path` |

Node lookup from agent tools goes through `tools/jfc/_resolve.py`, which returns
an *agent-readable error string* naming the plan's real node ids rather than
raising — a model that guesses a node id gets told the valid ones.

### Compilation

`plan_to_graph(plan)` emits, for each node, a `pending` artifact node whose id is
content-addressed on `node.artifact_path`, plus one `requires` edge per blocking
plan edge. A node with no prerequisites is wired to the `problem` node instead,
so the physics question is the root of the dependency graph rather than a node
floating free of it.

Because ids come from paths and paths do not change, compiling a plan
over an existing analysis leaves the already-ingested nodes alone — this is what
keeps graph ingestion idempotent across a plan edit.

`informs` edges are deliberately **not** compiled. They affect prompt assembly
only. This replaces the old implicit `"/outputs/"` string test that used to
distinguish a real prerequisite from an ambient file like `COMMITMENTS.md`.

---

## Templates

`hepagent/plan/templates/`. `jfc-measurement.json` and `jfc-search.json` are the
built-in starting points; `hepagent jfc templates` lists them.

`instantiate(name, analysis_name, analysis_type, physics_prompt)` stamps a
template with the analysis's identity and returns a plan.

The shipped templates keep the **on-disk** directory and artifact names of the
original pipeline — `phase1_strategy/outputs/STRATEGY.md` and so on — because the
`data/methodology/` and `data/conventions/` corpus cites those paths by name.
They are data now, so a user may rename them; the shipped default does not.

---

## The architect

`hepagent/agents/jfc/architect.py`. `propose_plan(...)` hands the model a
template plus the physics prompt and asks for **structural edits**, not a whole
plan. Typical edits: fan selection out per channel, insert a calibration
sub-analysis, drop the partial-unblinding node when there is nothing to
partially unblind.

Six operations, applied by `plan/edits.py` (pure, no model):

| Op | Effect |
|---|---|
| `add_node` | New node, optionally `like` an existing one, wired `upstream`/`downstream` |
| `remove_node` | Delete a node and its edges |
| `split_node` | Replicate one node into several — the fan-out primitive |
| `add_edge` / `remove_edge` | Rewire |
| `set_prompt` | Rewrite a node's spec |

Editing rather than regenerating is what keeps the shipped prompts
byte-identical: a model asked to reproduce a 4 kB methodology prompt will
paraphrase it, and the paraphrase is worse.

**The proposal is never trusted.** It is validated; blocking findings are fed
back for exactly one repair round; a plan that still fails falls back to the
plain template with a warning. `apply_edits` skips any individual edit it cannot
honour and reports it, rather than raising and losing the rest.

---

## The editor

`GET /plan/{name}` serves a self-contained page — inline CSS and JS, SVG,
no external requests — that draws the plan from `plan/layout.py`'s
server-computed layering and lets a physicist drag nodes, edit any node field,
retype edges, and add or delete either.

The transport lives in `web/plan_api.py` (the only FastAPI importer); every
decision lives in `plan/service.py`, which is stdlib-only and fully CI-tested.
See `docs/WEB.md` for the route-ordering hazard and the import boundary.

### Approval

`PlanApprovalGate` holds one `asyncio.Event` per analysis root, keyed by
resolved path. `run_jfc_analysis(..., require_approval=True)` awaits it after
writing the plan and **before the first node runs**, so nothing an agent does is
visible to the user as a fait accompli.

Two properties matter and are tested end to end in
`tests/web/test_plan_approval_flow.py`:

- **Approval releases what is on disk**, not what was on screen when the run
  started. Edits made while the run waited take effect.
- **Saving withdraws approval.** An edit cannot accidentally start a run, and a
  plan with a blocking finding cannot be approved at all (HTTP 409).

The orchestrator and the editor share one event loop and one gate — that is what
lets a click in the browser release a run already waiting. Two processes would
need real IPC.

---

## CLI

```bash
hepagent jfc templates                                  # list built-in templates

hepagent jfc plan propose  --name X --prompt-file p.md [--template jfc-measurement]
hepagent jfc plan show     --name X [--format table|mermaid|json]
hepagent jfc plan validate --name X                     # exits 1 on blocking findings
hepagent jfc plan edit     --name X [--port 8001]       # browser editor
hepagent jfc plan migrate  --name X                     # pre-plan analysis -> plan.json

hepagent jfc run --name X ... [--template T] [--plan FILE] [--review-plan]
```

`--review-plan` proposes a plan, opens the editor and blocks until you approve.

In the web chat, `/plan` lists the analyses that have one and `/plan <name>`
renders it as Mermaid with a link to the editor.

## Migration

`hepagent jfc plan migrate --name X` gives a pre-plan analysis directory a
`plan.json` and rewrites `.orchestration_state.json` from phase keys to node ids
(`1 → strategy`, `4a → inference_expected`, …), then rebuilds the graph.

Graph node ids are content-addressed on paths, and migration does not move any
file, so the existing graph survives intact — only `Node.phase` is rewritten.
A phase key the mapping does not cover is **dropped and reported**, never
guessed: a wrong remap would silently mark work complete that never ran.

---

## Invariants

Preserve these when changing plan code:

1. **`compile.py` is the only place edge direction inverts.** Plan edges are
   `upstream`/`downstream`; graph `requires` edges put the dependent in `src`.
   Anywhere else that builds a `requires` edge is a bug.
2. **The plan is the single source of structure.** No module may reintroduce a
   phase→directory, phase→reviewer, phase→artifact or phase→contract table.
   That duplication is exactly what this layer replaced.
3. **Node ids are author-stable.** Never derive an id from a label or a path,
   and never rewrite one on edit — edges reference ids.
4. **Validation runs before any agent work and on every save.** A blocking
   finding must stop the run, not warn. "Before any agent work" means before the
   scaffolder, not before the first executor — the scaffolder writes directories
   from `node.directory`, and by then it is too late.
5. **Severity, not rule identity, decides what blocks** — as in the graph. A new
   rule reporting `error` starts refusing plans. Choose deliberately.
6. **Saving withdraws approval.** Approval refers to a specific revision.
7. **`plan/service.py` stays stdlib-only.** It is imported by paths that run
   without the `web` extra. FastAPI belongs in `plan_api.py` and nowhere else.
8. **Compilation preserves already-ingested graph nodes.** `bootstrap_graph`
   declares placeholders only where none exist; it must never downgrade a
   produced artifact back to `pending`.
9. **An edit that cannot be applied is skipped and reported**, never raised —
   one bad edit from the architect must not discard the other nine.

---

## Status

Milestones 1–5 of `tasks/4.0-authored-analysis-graphs.md` are implemented.

Known gaps:

- The architect has been exercised against structured-output stubs and one live
  multi-channel prompt, but not across a full orchestrated run.
- `node.tools` is in the schema and validated but not yet enforced — every
  `executor` node gets the default tool set. The field exists so restricting it
  stays an additive change.
- `kind: "gate"` nodes are accepted by the schema but nothing constructs one;
  gates are node *attributes* today. The discriminator is there so a standalone
  gate node does not need a schema bump.
- The editor has no undo beyond `plan.history/` and the *Relayout* button; a
  misdrag is cheap to fix, a mistaken delete costs a file copy.
