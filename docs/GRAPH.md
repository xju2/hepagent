# Analysis Graph

Every JFC analysis carries a durable provenance graph under
`<analysis_root>/graph/`. It answers questions the directory layout cannot:
*what produced this plot*, *what evidence closed commitment D1*, *what did the
Phase 3 reviewer reject*, *which commitments are still open*.

Read this before changing graph behaviour, the way `docs/WEB.md` governs the web
UI. The invariants below are load-bearing.

---

## Files

```
<analysis_root>/graph/
    nodes.jsonl    one JSON object per line, one line per node revision
    edges.jsonl    one JSON object per line, one line per edge revision
    README.md      format note for humans reading the analysis directory
```

**Append-only.** Records are never rewritten in place. A later line with the
same node `id` — or the same `(src, dst, type)` edge triple — supersedes the
earlier one. The file therefore keeps its own history, stays readable in a git
diff, and can be inspected with `jq`. `git_commit_phase` runs `git add -A`, so
each phase commit snapshots the graph automatically.

**Deterministic ids.** Node ids are `<type>:<slug>`, content-addressed on the
analysis-root-relative path wherever a file exists:

```
problem:zbb
artifact:phase1_strategy/outputs/STRATEGY.md
figure:phase2_exploration/outputs/figures/mjj.png
evidence:phase3_selection/outputs/results/closure.json
decision:phase3_selection/review/ADJUDICATION.md
commitment:D1
```

This is what makes ingestion idempotent — see [Invariants](#invariants).

---

## Node types

| Type | Meaning |
|------|---------|
| `problem` | The physics question, from `prompt.md` |
| `analysis_root` | The analysis itself |
| `commitment` | One `[D1]`-style row from `COMMITMENTS.md` |
| `dataset` | A data or MC sample, with AMI tag / campaign metadata |
| `method` | A selection, calibration, unfolding or statistical method |
| `artifact` | A phase's primary markdown output |
| `figure` | One plot file under `outputs/figures/` |
| `evidence` | A `results/*.json` file, closure number, or citation |
| `review` | One reviewer's findings document |
| `decision` | An `ADJUDICATION.md` and its verdict |
| `execution` | An analysis script, job or notebook |

Node status is one of `active`, `pending`, `superseded`, `resolved`,
`downscoped`. **`pending` means "declared but not yet produced"** — a plan, not
a claim. Validation deliberately exempts pending nodes from the
content-exists and provenance rules.

## Edge types

| Edge | Reads as |
|------|----------|
| `requires` | A depends on B being available first |
| `derives_from` | A was produced from B |
| `supports` | Evidence A backs claim B |
| `invalidates` | Review/decision A rejects B |
| `commits_to` | Artifact A promises commitment B |
| `resolves` | Commitment A is closed by evidence B |
| `downscopes` | Commitment A was narrowed, justified by B |
| `reviewed_by` | Artifact A was examined by review B |
| `approved_by` | Artifact A was cleared by decision B |
| `regresses_to` | Finding A traces back to earlier work B |

`schema.py` holds `EDGE_DOMAIN`, a matrix of which `(source type, target type)`
pairs each edge may connect. `add_edge` rejects anything outside it, along with
self-edges and dangling endpoints.

## ATLAS metadata keys

Experiment-specific structure rides in `Node.metadata` rather than in new node
types. These keys are conventions, not enforced schema — but builders, tools and
queries should prefer them so queries stay portable:

`ami_tag`, `campaign`, `derivation`, `lumi_fb`, `reco_tag`, `trigger`, `region`,
`syst_source`, `np_name`, `generator`.

---

## How the graph gets written

Two paths, in this order:

**1. Agents write meaning** (`hepagent/tools/jfc/graph.py`). Executors call
`graph_add_node` / `graph_add_edge` / `graph_query` to record what the
filesystem cannot show: which dataset a selection was tuned on, which closure
test resolves which commitment.

**2. The builder writes structure** (`hepagent/agents/jfc/graph_builder.py`).
Deterministic parsing of what is already on disk — no model calls. It reuses the
pipeline's own parsers so the graph cannot drift from what agents actually read:

| Source | Reused from | Produces |
|--------|-------------|----------|
| `UPSTREAM_ARTIFACTS`, `PHASE_SPECS` | `agents/jfc/executor.py` | `requires` / `derives_from` edges |
| `check_phase1_commitments` | `agents/jfc/commitment_checker.py` | `commitment` nodes + closing edges |
| `parse_verdict_from_adjudication` | `agents/jfc/review_gate.py` | `decision` nodes + verdict edges |

Ingestion runs *after* agent write-back and merges — it never clobbers.

### The write-back contract

Each phase may create only the node and edge types that belong to it. A call
outside the contract is refused with an explanation rather than silently
widening what the phase can assert. The table lives in `PHASE_CONTRACTS`
(`tools/jfc/graph.py`) and is injected into every executor prompt by
`_graph_contract_section` in `executor.py`.

| Phase | Node types | Edge types |
|-------|-----------|-----------|
| 1 | commitment, method, dataset | commits_to, requires |
| 2 | dataset, evidence, figure | derives_from, supports |
| 3 | method, evidence, figure | derives_from, supports, resolves |
| 4a / 4b / 4c | evidence, figure, method | derives_from, supports, resolves, downscopes |
| 5 | artifact, evidence, figure | derives_from, supports |

A `downscopes` edge additionally requires a non-empty `evidence_ref`: a narrowed
commitment must say why.

---

## Orchestrator integration

**Execution order is derived from the graph, not declared.** `planner.py` reads
the `requires` edges — which `bootstrap_graph` builds from `UPSTREAM_ARTIFACTS`,
the same map executors read their upstream artifacts from — and reports which
phases are runnable. `_phase_sequence` re-asks after every phase, so a
regression that un-completes phase 3 makes 3 runnable again without any special
casing.

The resulting order is byte-identical to the old hardcoded list; it is simply
derived. `PHASE_ORDER` survives as the tiebreak between equally-ready phases and
as the fallback when the graph is missing or unreadable.

- `run_jfc_analysis` seeds the graph via `_ensure_graph` (scaffold already does
  this for new analyses; this covers resumed or pre-graph directories).
- `run_phase_with_review` calls `update_graph(..., "phase")` after the executor
  and `update_graph(..., "review")` after the review gate — including when the
  gate raises, since an escalation is when provenance matters most.
- The Phase 4a gate runs `_graph_commitment_findings` alongside
  `check_phase1_commitments`. The markdown table says what a commitment's status
  *is*; the graph says which evidence actually closed it. A commitment marked
  resolved with no `resolves` edge is caught here and nowhere else.

**Graph failures never abort a run.** Every ingestion call is wrapped and
reports through the progress callback, the same posture as `git_commit_phase`.
Planning falls back to `PHASE_ORDER`. Bookkeeping must not take down an analysis
that is otherwise progressing.

### Resumption

`hepagent jfc resume --name X` with no `--from-phase` asks
`planner.resume_point`, which restarts from the most recent **consistent**
checkpoint — not from "the phase after the last one that finished". A phase is a
checkpoint only when its artifact exists *and* nothing in the graph invalidates
it, so a phase a later reviewer rejected gets redone rather than skipped past.

---

## Validation rules

`hepagent/graph/validation.py`. `validate()` runs all of them;
`validate_commitments()` runs only R2 (the Phase 4a gate); `validate_gating()`
returns just the blocking subset.

| Rule | Checks |
|------|--------|
| R1-schema | Every edge has existing endpoints and a legal type domain |
| R2-commitments | Every commitment has a `resolves`/`downscopes` edge; downscopes cite evidence |
| R3-provenance | Artifacts and figures record `derives_from` lineage |
| R4-content | File-backed nodes point at files that exist |
| R5-no-deletion | No commitment present in the log history vanished from the current graph |
| R6-figure-refs | Every figure an analysis note references is a declared figure node |

R3 exempts the Phase 1 artifact (it has no upstream by construction). R3 and R4
both exempt `pending` nodes — a pending node is a plan, not a claim.

### Severity is the gate

`error` states something false about the analysis regardless of context — a
dangling edge, an unclosed commitment, a note citing a figure that was never
produced. `run_review_gate` downgrades PASS to ITERATE on any error, whatever
the reviewers concluded, and appends the findings as Category A.

`warning` is a judgement call about what a phase was meant to have produced by
now. Warnings are injected into the arbiter's prompt for it to weigh and never
force a verdict on their own.

`report.blocking` and `report.advisory` split a report along that line.

---

## Review integration

Before reviewers run, `write_graph_validation` persists the report to
`<phase>/review/GRAPH_VALIDATION.md` so reviewers (which have `read_file`) can
cite it as evidence. `reviewers._graph_section` injects the phase's graph slice
into the critical reviewer, the plot validator and the arbiter, along with the
four checks the graph makes possible:

- does every important claim trace to a node with lineage?
- does every referenced plot correspond to a figure node?
- is every commitment resolved or downscoped, with evidence?
- does every number match the machine-readable results?

The arbiter is told which findings are already enforced in code so it does not
spend its verdict re-deciding them.

---

## Note generation

`report.note_brief` gives the note writer the graph slice it must write from:
the figure manifest (the only paths it may reference), the evidence digest (the
numbers it must quote exactly, flattened from `results/*.json`), and the
commitment ledger (what it must account for). The digest is declared
authoritative over the phase artifacts — the JSON is what the code produced.

The loop closes at the review gate: R6 checks that every figure the note
references is a declared node, and errors block the phase. A note cannot quietly
cite a plot that was never made.

The note writer also gets `graph_query` — read-only, so it can trace a claim's
provenance without being able to write anything.

---

## CLI

```bash
hepagent jfc graph show     --name <analysis> [--format table|mermaid] [--type figure]
hepagent jfc graph validate --name <analysis>          # exits 1 on blocking findings
hepagent jfc graph trace    --name <analysis> --node mjj.png
hepagent jfc graph rebuild  --name <analysis>          # re-derive from disk
```

`trace` accepts a node id, an analysis-root-relative path, or a bare filename.

---

## Invariants

Preserve these when changing graph code:

1. **Ingestion is idempotent.** Running `rebuild` over an unchanged directory
   must append zero lines. Node ids are content-addressed and `add_node` skips
   writes when the payload is unchanged. Two bugs have already been caught here:
   writing the adjudication node twice per pass, and `bootstrap_graph`
   downgrading an ingested artifact back to `pending`. Both are regression-tested
   in `tests/agents/jfc/test_graph_builder.py`.
2. **`bootstrap_graph` never overwrites real nodes.** It only declares
   placeholders that are absent.
3. **Graph work never raises into the orchestrator.** Wrap new call sites.
4. **The builder makes no model calls.** Anything requiring judgement belongs in
   the agent write-back tools, under the contract.
5. **Appends stay atomic.** Reviewers run concurrently under `asyncio.gather`;
   `store.py` guards writes with a lock and one `write()` per record.
6. **The derived order must reproduce `PHASE_ORDER`.** Changing `requires` edges
   changes what runs when. `test_derived_order_reproduces_the_canonical_phase_order`
   pins this; if it fails, the graph and the pipeline have diverged.
7. **Severity, not rule identity, decides what blocks.** A new rule that reports
   `error` will start gating phases. Pick the severity deliberately.

---

## Status

Milestones 1–6 of `tasks/3.0-graph-engineering.md` are implemented.

Known gaps:

- `query.frontier()` computes the expandable set from filesystem state and is
  used by tests and the CLI, but `planner.py` drives orchestration from phase
  completion rather than node realization. The two agree today because a phase
  completes exactly when its artifact lands; they would diverge for sub-phase
  parallelism, which nothing needs yet.
- Number consistency (M4's fourth check) is enforced by giving reviewers the
  authoritative values, not by parsing prose. A reviewer still has to notice a
  mismatch; nothing cross-checks every numeral in the note against the digest.
- The write-back contract and the graph-aware prompts are unit-tested but have
  not been observed across a full orchestrated run against a live model.
