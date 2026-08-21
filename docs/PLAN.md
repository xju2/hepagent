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
| `kind` | `work` runs an agent; `gate` only interposes a check; `condition` evaluates a test and routes — see [Conditions and loops](#conditions-and-loops) |
| `role` | `executor`, `note_writer`, `typesetter` |
| `context_paths` | Ambient files injected alongside upstream artifacts (`COMMITMENTS.md`). Carry no dependency — read if present, never delay the node. |
| `reviewers` | Reviewer names the review gate runs. One of `physics`, `critical`, `constructive`, `plot`, `rendering`, `bibtex`. |
| `skills` | Skill names whose `SKILL.md` is loaded into this node's prompt up front |
| `mcp_servers` | MCP servers this node may reach. **Declarative only** — see [Capabilities](#capabilities) |
| `arbiter` | Whether an arbiter adjudicates this node's reviews |
| `produces_note` | Whether the node runs the note writer and typesetter |
| `gates` | Interposed checks, see below |
| `condition` | The test a `condition` node evaluates. Refused by P10 on any other kind |
| `contract` | What this node may write back into the provenance graph |
| `max_iterations` | Review iterations before escalating |
| `max_turns` | Optional per-agent turn cap; `null` defers to the run, see [The turn cap](#the-turn-cap) |
| `model` | Optional model override, see [Platform and model](#platform-and-model) |
| `tools` | Optional function-tool allowlist by name; `None` means the default set for `role` |
| `metadata` | Free-form. The editor stores layout coordinates here under `x`/`y`. |

Derived paths — `artifact_path`, `note_path`, `note_pdf_path`, `outputs_dir` —
are properties, so no caller reconstructs them by string concatenation.

### Capabilities

`tools`, `skills` and `mcp_servers` say what a node's executor is *given*, the
way `reviewers` and `gates` say what is done *to* what it produced. All three are
authored on the node and edited in the browser; none of them is looked up from a
table keyed on anything.

- **`tools`** is a tri-state and the distinction matters. `None` means "the
  default set" — `executor_tools()` in `agents/jfc/executor.py`, which is both
  what the agent gets and the catalog the editor offers, so a rename cannot make
  the two disagree. A list narrows that set by name. `[]` really does mean no
  tools. An allowlisted name that no longer exists is logged and skipped rather
  than raised: `validate_plan` is where a user hears about it, and a plan that
  outlived a tool rename must not take a run down.
- **`skills`** are loaded into `_assemble_executor_prompt` as an `ACTIVE SKILLS`
  section. A plan node names its skills up front, so unlike the interactive agent
  it never has to discover and activate one mid-run.
- **`mcp_servers`** is **recorded but not connected**. Nothing in hepagent starts
  or attaches an MCP server today. The names come from `config/mcp.toml`
  (packaged default, copied to `~/.hepagent/config/` on first run), the editor
  offers them and `plan.json` stores the selection — that is the whole of it.
  Wiring the selection into a running agent is a separate change; until then do
  not read `node.mcp_servers` as a statement about what the executor can reach.

`agents/jfc/capabilities.plan_vocabulary()` assembles all five catalogs — the
four above plus the platforms below. It is the only place the domain registries
meet `plan/`, which stays domain-agnostic: `PlanVocabulary` carries names and
nothing else.

### Platform and model

`node.model` is one string, and the colon is what gives it a reading — the same
reading `parse_model_spec` gives a spec typed at the CLI, so the two cannot
drift:

| Spec | Means |
|---|---|
| `null` | Inherit the run's platform and model. The common case. |
| `"gpt-5-mini"` | That model, on whatever platform the run chose. |
| `"openai:gpt-5-mini"` | Both overridden. |
| `"openai:"` | That platform, at *its own* default model. |

`_model_for` in `agents/jfc/executor.py` is the only place the string is read,
and the editor's two dropdowns are the only place it is assembled. The trailing
colon matters: without it, `"openai"` would be read as a model literally named
`openai` on the run's platform, which is what a platform-only choice must never
become.

The **platform** catalog is `providers.toml`, so it rides along in
`PlanView.catalog` like every other dropdown. The **models** a platform serves
cannot: listing them is a network call against the provider, so the page asks
`GET /api/platforms/{platform}/models` when a user opens a node that names a
platform, once per page per platform. A provider that cannot be reached answers
502 and the page falls back to a free-text model field saying why — an offline
laptop must still be able to edit a plan. For the same reason P6 checks only the
platform half of a spec: which models exist is not a question a validator can
answer offline.

That same response carries `costs`, `{model: {"input": $/M, "output": $/M}}`, so
the dropdown can price what it offers. Pricing is not part of the OpenAI model
API — `/v1/models` returns ids and nothing else — so `list_model_costs` reads a
LiteLLM gateway's `/model/info` instead, and answers `{}` for a provider that
publishes no prices. **A missing price is never an error**: OpenAI and Gemini
quote nothing through their OpenAI-compatible endpoints, and their models must
still be listed and selectable.

### The turn cap

How many turns one agent call may take is settled narrowest-first:

| Source | Set where |
|---|---|
| `node.max_turns` | The node panel in the editor, or the field in `plan.json` |
| the run's cap | `--max-turns` on `jfc run`/`jfc resume`, or the plan page's run settings |
| the role default | executor 50, note writer/fixer 30, reviewer/investigator/typesetter 20, condition 10 |

`_turns_for(node, run_max_turns, role_default)` in `agents/jfc/orchestrator.py`
is the only place that precedence is applied, so a new call site inherits it by
using the helper rather than by remembering the rule. A node declaring its own
cap is the point: a selection step sweeping cuts can be given a long leash
without raising the ceiling for every other node in the plan, which a run-wide
flag cannot express.

`null` and a number are different statements — "the run decides" versus "this
many" — so the field is nullable rather than defaulting to a role's number, and
a cap below 1 is refused by the schema: a node that may take zero turns cannot
run at all.

### The process inventory

One thing a node produces is not prose: the **process inventory** —
`<node.outputs_dir>/processes.json`, written by `record_process_inventory` and
defined in `agents/jfc/processes.py`. It is the strategy's machine-readable
answer to *what is the signal, what are the backgrounds, and which datasets
carry each of them*:

```json
{"id": "dy", "label": "Z/gamma* -> ll", "role": "background",
 "category": "irreducible", "importance": "dominant",
 "rationale": "same mu+tau_h final state as the signal",
 "estimation": "MC, normalised in a Z-enriched control region",
 "datasets": [{"name": "DYJetsToLL.root", "path": "...", "source": "prompt"}]}
```

Four properties are load-bearing:

- **The location comes from the node**, `PlanNode.outputs_dir`, never from a
  phase name. Renaming `phase1_strategy` moves the file with it.
- **The owner comes from the contract.** The first node allowed to create both
  `process` and `dataset` graph nodes owns the inventory
  (`processes.inventory_owner`), and that node's executor prompt tells it to
  record one. There is no `strategy`-shaped special case anywhere.
- **The vocabulary is closed.** A background is `irreducible`, `reducible` or
  `instrumental`; anything else is refused with the three that exist, and every
  problem in the document is reported at once so one call gets one correction.
- **Everyone downstream is handed it.** `_process_inventory_section` in
  `executor.py` injects the recorded inventory into every *other* node's prompt,
  which is the point of writing it once — no later node re-derives the
  background list from `STRATEGY.md`'s prose. `read_process_inventory` gives an
  executor the full detail on demand.

`graph_builder` ingests it into `process`/`dataset` nodes; see
[GRAPH.md](GRAPH.md). The progress panel on the plan page renders it live.

### `PlanEdge`

```json
{"upstream": "strategy", "downstream": "exploration", "kind": "requires", "inject": "full"}
```

| Field | Meaning |
|---|---|
| `upstream` | Node that produces |
| `downstream` | Node that consumes |
| `kind` | `requires` blocks and orders execution; `informs` injects the artifact when it happens to exist but never delays the node; `on_true` / `on_false` leave a condition node for the branch it routes to |
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

## Conditions and loops

A plan is not only a pipeline. A `condition` node evaluates a test and routes the
analysis along one of two branches; when a branch points at a node that has
already run, the plan **loops**. That is what lets a plan say *keep optimizing
the selection until the expected significance stops improving* rather than
producing a working point once and accepting it.

```json
{"id": "converged", "kind": "condition", "label": "Converged?",
 "directory": "phase3_selection/optimization", "artifact": "CONDITION.md",
 "condition": {
   "metric": {"source": "phase3_selection/outputs/results/optimization.json",
              "key": "significance", "compare": "improvement_below", "value": 0.02},
   "question": "Has the expected significance stopped improving meaningfully?",
   "max_iterations": 5,
   "on_exhaustion": "true"}}
```

```
propose ──requires──> evaluate ──requires──> converged?
converged? ──on_false──> propose       (back: rewind and run the body again)
converged? ──on_true───> inference     (forward: compiles to a requires edge)
```

### Forward and back branches

Everything follows from one classification, `AnalysisPlan.back_branch_keys()`:

| Branch | Compiles? | Meaning |
|---|---|---|
| **forward** — treating it as blocking closes no cycle | **yes**, as a graph `requires` edge | the target cannot start until the condition decides |
| **back** — treating it as blocking would close a cycle | **no**, like `informs` | rewind: re-run the body between the target and the condition |

Compiling forward branches is what stops the exit node from running before the
first pass. Not compiling back branches is what stops a loop from deadlocking the
planner, where every node in the cycle would wait forever on another.

Branches are classified one at a time in declaration order against the blocking
edges accepted so far — **not** against the `requires` edges alone. With nested
loops the outer back branch only closes its cycle through the inner loop's
forward branch, and classifying it forward would put a real cycle into the
blocking graph.

`prerequisites()` therefore means "`requires` edges plus forward branches", and
P4, `layout`, `execution_order`, `entry_nodes` and `plan_to_graph` all inherit
the right answer from it without knowing conditions exist.

### How a condition is decided

`agents/jfc/condition.py`, in this order:

1. **The metric**, when one is declared and readable — a number from a results
   JSON, compared with no model call. `improvement_*` compares against the
   previous pass, which is the convergence test; on the first pass there is
   nothing to compare, and that resolves to *not converged* rather than to an
   unevaluable condition.
2. **The question**, judged by a small read-only agent that answers YES or NO.
3. **`on_exhaustion`**, when neither can be evaluated — reported loudly. A
   condition is control flow, not bookkeeping: silently picking a branch would
   send an analysis down a path nobody chose.

### Termination

`max_iterations` bounds **how many times the loop body runs** — the cost the
author is choosing, not an evaluation count. The condition is still evaluated on
the final pass, so the decision record says what the metric actually showed when
the budget rather than convergence ended the loop.

The orchestrator refuses a rewind that would exceed the budget, and refuses one
whose `on_exhaustion` branch also loops. Running forever is the single outcome
this design exists to rule out, and no plan can ask for it.

### What execution does

`orchestrator.run_condition_node` — no executor, no note writer, no review gate.
That bypass is explicit because `reviewers_for` deliberately gives a node with no
reviewers the critical reviewer, so an empty panel would not have been enough.

- **Back branch** → the body (`_between`, the same set a regression rewinds)
  leaves `completed_nodes` *and* the caller-owned `attempted` set. **The
  condition rewinds with its body**; leaving it complete would run the body once
  more and then walk straight past the test that sent it round.
- **Forward branch** → whatever the untaken branch *exclusively* leads to is
  marked `skipped`. A node downstream of both branches is reachable from the
  taken one, so a fork that re-joins still runs; `phase_readiness` treats a
  prerequisite as met when it is complete **or** skipped.

Each evaluation writes `<directory>/decisions/iteration_NN.md` and refreshes the
node's artifact as a running log. Because `executor._read_upstream_artifacts`
iterates `upstream_edges` without filtering by kind, a back branch injects the
condition's rationale into the loop head's next pass — that is how the optimizer
learns between iterations.

---

## Validation

`hepagent/plan/validate.py`. `validate_plan(plan, root)` returns a
`GraphValidationReport` — the *same* record type the graph validator returns, so
the severity model, the `.blocking`/`.advisory` split and the rendering are
shared rather than reimplemented.

The optional `vocabulary=PlanVocabulary(...)` argument carries the names this
installation can actually build — reviewers, tools, skills, MCP servers,
platforms. Each
field is independently optional and `None` means *do not check*, not *nothing is
valid*: a caller that only knows the reviewers must not turn every tool name in
the plan into a blocking finding. Names are checked for being non-empty either
way.

| Rule | Checks | Severity |
|---|---|---|
| P1-ids | Node ids are unique and slug-safe | error |
| P2-outputs | No two nodes write the same artifact; no directory escapes the root | error |
| P3-edges | Edge endpoints exist; no duplicates; no self-edges | error |
| P4-acyclic | The blocking subgraph — `requires` edges plus forward branches — is acyclic | error |
| P5-entry | Exactly one starting point | warning |
| P6-vocabulary | Roles, gates, reviewers, capabilities, model platforms, edge kinds and inject modes are recognised | error |
| P7-contract | Write-back contracts name real graph node and edge types | error |
| P8-notes | A `produces_note` node produces markdown | error |
| P9-reachable | Every node is downstream of an entry node | warning |
| P10-conditions | Condition nodes are well formed and every loop is bounded | error / warning |

**Severity is the gate**, exactly as in the graph: an `error` blocks — the
editor disables *Approve & run*, `jfc plan validate` exits 1, and the
orchestrator refuses to start. A `warning` is advisory. A plan with two entry
nodes (P5) or an unreachable node (P9) is unusual but not wrong; a plan with a
cycle (P4) cannot execute at all.

P10 blocks a condition with no test, a condition with nowhere to route, a branch
leaving a node that is not a condition, a condition feeding a `requires` edge (its
successors are its branches, and that node would run whichever way it went), a
budget below 1, an unknown comparison, and a metric source escaping the root. It
warns on a single branch, a budget above 20, a loop with neither metric nor
question, reviewers on a condition node, and nested loops — naming the worst-case
body-execution count, because nested budgets multiply. It also warns on tools,
skills or MCP servers declared on a condition node: a condition is evaluated, not
executed, so none of them would ever be attached.

"A cycle is forbidden" has become "a cycle must be bounded", which is the
guarantee P4 existed to provide, kept.

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
| Function tools the executor gets | `node.tools`, narrowing `executor_tools()` |
| Skills in the executor prompt | `node.skills` |
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

**Back branches are not compiled either**, for a stronger reason: a `requires`
edge closing a cycle is a deadlock, not a loop. Forward branches *are* compiled,
so a condition still orders what follows it. `compile.py` needs no special case
for either — it reads `prerequisites()`, which already draws the line.

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

### Predefined nodes

`predefined_nodes(analysis_name=...)` flattens every built-in template into a
catalog of **whole nodes** — prompt inlined, reviewers, gates and contract
included — that the editor offers under *+ Predefined*, served by
`GET /api/plan/{name}/predefined` via `service.predefined_nodes`. Three things
about it:

- A pick is a **copy**, not a reference. The node lands in the plan and is an
  ordinary node from then on; nothing tracks where it came from, and editing it
  cannot change the template.
- Prompts resolve against the **template's own** analysis type, so a node lifted
  out of the search pipeline keeps the search conventions it was written for
  wherever it is dropped.
- The page makes the id and the working directory unique against the plan it is
  inserting into (`strategy` → `strategy_2`, `phase1_strategy` →
  `phase1_strategy_2`): a duplicate id is refused by P1, and a shared directory
  would put two nodes' artifacts on top of each other.

Growing the library means appending a source in `service.predefined_nodes`; the
route and the page learn nothing new.

---

## The architect

`hepagent/agents/jfc/architect.py`. `propose_plan(...)` hands the model a
template plus the physics prompt and asks for **structural edits**, not a whole
plan. Typical edits: fan selection out per channel, insert a calibration
sub-analysis, drop the partial-unblinding node when there is nothing to
partially unblind.

Seven operations, applied by `plan/edits.py` (pure, no model):

| Op | Effect |
|---|---|
| `add_node` | New node, optionally `like` an existing one, wired `upstream`/`downstream` |
| `remove_node` | Delete a node and its edges |
| `split_node` | Replicate one node into several — the fan-out primitive |
| `add_edge` / `remove_edge` | Rewire |
| `set_prompt` | Rewrite a node's spec |
| `add_loop` | Condition node plus both branches, in one edit — the loop primitive |

`add_loop` is atomic for the same reason `split_node` is: a loop is a condition,
a branch back and a branch forward *together*, and proposing them separately
means any one can be skipped, leaving a condition that routes nowhere. It also
clamps `max_iterations` to at least 1 and rewires the exit node to wait on the
condition rather than on the node the loop wraps, and it refuses a loop with
nothing to break on.

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
retype edges, and add or delete either. With nothing selected the side panel
shows `plan.problem`, the physics prompt the plan was written for, read-only:
it mirrors the analysis's `prompt.md`, and an editable copy would only let the
two disagree.

The transport lives in `web/plan_api.py` (the only FastAPI importer); every
decision lives in `plan/service.py`, which is stdlib-only and fully CI-tested.
See `docs/WEB.md` for the route-ordering hazard and the import boundary.

**Every dropdown is fed by the server.** `PlanView.catalog` carries the
reviewers, tools, skills, MCP servers and platforms `plan_vocabulary()` found,
and the model names come from `/api/platforms/{platform}/models`; the page
offers those and nothing else — the same lists P6 validates against, so the
editor cannot produce a name the validator then refuses. Where a catalog is
absent (a view built without a vocabulary), the page offers the names the plan
already uses rather than inventing any. An absent key and an empty list are
different answers, and `catalog_of` keeps them apart.

A selected node's panel leads with **Run · Delete · Save** — the three things
that can be done to a node — rather than trailing them under a form whose last
field is a screenful of executor prompt. Save is disabled while there is nothing
to save, and enabling it must not rebuild the form: that would pull the focus out
of the field being typed in, which is precisely when it lights up.

`kind` is a dropdown like any other field. Switching a node *to* `condition`
seeds the condition payload and switching it *away* clears it, because P10
refuses both a condition node with nothing to evaluate and a condition on a node
that is not one. The `tools` tri-state is a checkbox plus a picker: the checkbox
owns `null` versus a list, the picker only edits the list.

A condition node is drawn as a **diamond**, its branches are coloured and
labelled *yes*/*no*, and a back branch is dashed and bows below the row it
re-runs — a loop has to be visible as a loop. Selecting one says so in words, and
selecting the condition opens the break condition for editing: budget,
`on_exhaustion`, metric source/key/comparison/threshold, and the question.

The page classifies back branches itself, by the same rule as
`back_branch_keys`. That is the one deliberate exception to "the server owns
structure": the server's answer still decides what runs, but a loop the user
cannot see is a loop they cannot fix. `test_the_page_classifies_the_back_branch_the_way_the_server_does`
pins the two together.

### Geometry

The page owns pixels; `plan/layout.py` owns layering, and it is the *only*
implementation — the browser must never grow a second one. Three rules follow:

- **A node's position is `metadata.x`/`y` when it has one**, and the view's
  server-computed grid otherwise. That fallback is only sound for nodes the
  server has seen, so anything the page creates — a new node, an auto-layout —
  writes explicit coordinates rather than relying on it.
- **Auto-layout posts the plan on screen** to `POST /api/plan/{name}/layout`
  and applies the grid that comes back. It saves nothing. Falling back to the
  layout that arrived with the last view instead is the bug this replaced: a
  node added in the browser had no entry there and stacked up on the origin,
  so nothing appeared to happen until a save went through the same layering.
- **A drag mutates the SVG in place** — the dragged group's transform, the
  paths incident to it, the canvas bounds — and re-renders only on release.
  A full render mid-drag destroys the element holding the pointer capture,
  which the browser reports as `lostpointercapture`, and the drag dies with it.

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

### Launching

Approval only *releases* a run; it does not start one. With `jfc run
--review-plan` that is the whole story, because an orchestrator is already
blocked on the gate. Everywhere else — a page opened from the chat, or by `jfc
plan edit` — nothing is waiting, and approving alone would leave the physicist
looking at a green pill and no analysis.

So `POST /api/plan/{name}/approve` reports `awaited` (from
`PlanApprovalGate.waiting()`), and the page starts the run itself when nothing
was. Launching is a separate route, `POST /api/plan/{name}/run`, which refuses an
unapproved plan: approval is where a human took responsibility for what runs, and
an API caller must not be able to skip it any more than the button can.

`POST /run` also takes **`only_node`**, which the side panel's per-node *Run*
button sends: that node runs, and nothing else. It skips the planner rather than
asking `next_phase` for a frontier of one — the point of the button is to re-run
*this* node whatever the graph thinks is ready — and it is a resume rather than a
fresh start, so `.orchestration_state.json` and everything already completed
stand. It returns the node's primary artifact instead of the final PDF.

Running a *condition* node this way evaluates it — including the rewind a back
branch implies, which un-completes the loop body — but nothing follows it, so
the branch it chose is left for the next run to take.

A single-node run does not consult the approval latch and does not set it.
Running one node is a statement about that node; a blocking finding refuses it
just as it refuses approval, and requiring approval would be circular anyway,
because saving the edit the user is about to test withdraws approval (invariant
6).

The run then belongs to `plan/runs.py`, not to the plan layer proper — it is
supervised on the page, and how that works (the worker thread, the pending
prompts, the cooperative stop) is documented in [WEB.md](WEB.md).

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
10. **A cycle is legal only through a bounded condition node, and back branches
    never compile.** Both halves are load-bearing: the bound is what guarantees
    termination, and not compiling the back edge is what stops the loop becoming
    the deadlock P4 was written to prevent. Anything that classifies branches
    outside `back_branch_keys`, or that lets a rewind ignore
    `condition_iterations`, breaks this.
11. **A condition is evaluated, never executed.** No executor, no note writer, no
    review gate — and the bypass is explicit, because a node declaring no
    reviewers is deliberately given one.
12. **The editor offers only what the server says exists.** Capability and
    platform dropdowns come from `PlanView.catalog`, and model names from
    `/api/platforms/{platform}/models`; the page must never hardcode a list of
    tools, skills, servers or platforms, or it will let a user author a plan P6
    then refuses.
13. **`plan/` knows names, not registries.** `PlanVocabulary` carries strings.
    The moment `plan/` imports the reviewer registry or the tool list, the layer
    has stopped being domain-agnostic.
14. **Approving and launching are two steps.** `POST /approve` releases the gate
    and says whether anything was waiting on it; `POST /run` starts an analysis
    and refuses one that is not approved. Collapsing them would make `jfc run
    --review-plan` run the same analysis twice — once from the CLI it released,
    once from the page. The one exception is `only_node`: a single-node run
    neither reads nor sets the latch, and is still refused by a blocking
    finding.
15. **`plan/runs.py` stays domain-agnostic too, and stdlib-only.** It supervises
    *a* run and asks *a* human; what a JFC analysis is lives in
    `agents/jfc/launch.py`, which is injected. Same rule as `PlanVocabulary`:
    the moment `plan/` imports the orchestrator, the layer is gone.

---

## Status

Milestones 1–5 of `tasks/4.0-authored-analysis-graphs.md` are implemented.

Known gaps:

- The architect has been exercised against structured-output stubs and one live
  multi-channel prompt, but not across a full orchestrated run.
- `node.mcp_servers` is authored, validated and editable but **not connected**.
  Nothing in hepagent starts or attaches an MCP server; the catalog in
  `config/mcp.toml` exists so the plan can record the intent, and the runtime
  ignores it. See [Capabilities](#capabilities).
- `kind: "gate"` is now selectable in the editor, but the orchestrator still runs
  such a node exactly as it runs a `work` node — gates are node *attributes*
  today, and the kind is a label on the picture. `kind: "condition"` is the one
  standalone kind the discriminator actually branches on.
- `node.role` is validated but never dispatched on: the orchestrator always
  builds an executor, and note-writing is gated on `produces_note`.
- No shipped template restricts `tools` or names a skill, so the default set is
  what every node in the built-in pipeline still gets.
- The editor has no undo beyond `plan.history/` and the *Auto-layout* button; a
  misdrag is cheap to fix, a mistaken delete costs a file copy.
- No shipped template contains a loop, so the pinned pipeline topology is
  unchanged. A loop is authored per analysis, by hand or by the architect's
  `add_loop`.
- Loops are covered end to end by tests with the executor and reviewers mocked;
  like the write-back contract, they have not been observed against a live model
  across a full orchestrated run.
