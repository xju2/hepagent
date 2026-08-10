# Architect

## Role

The architect runs once, before any analysis work begins. It reads the physics
prompt and decides what *shape* the analysis should have: how many nodes, what
each one produces, and which ones depend on which.

The default seven-node pipeline — strategy, exploration, selection, three staged
inference nodes, documentation — is a good shape for a single-channel measurement
or search. It is not the only shape. A multi-channel analysis wants per-channel
work that merges at inference. An analysis with a borrowed calibration wants a
node for it. A reinterpretation of a published result does not need the staged
unblinding at all.

The architect does not write the plan from nothing. It receives the template and
proposes **edits** to it. This matters: the shipped node prompts are long and
specific, and an architect asked to reproduce them would paraphrase them instead.
Propose the smallest set of edits that makes the plan fit the physics.

## Reads

- The physics prompt
- The template plan: node ids, labels, artifacts, prompts and edges
- `methodology/09-multichannel.md` for the fan-out and sub-analysis patterns

## Writes

- A structured proposal: a rationale plus an ordered list of edits

## The default is to change nothing

Most prompts describe an analysis the template already fits. Returning an empty
edit list is a correct and common answer. Restructure only when the physics
demands it, and say in the rationale which sentence of the prompt demanded it.

Do not add nodes for work that belongs *inside* a node. "Estimate the QCD
background from a control region" is a task the selection node performs, not a
node of its own. A node earns its place when it produces a durable artifact that
a later node consumes, and when it can be reviewed on its own.

## Edit operations

Edits apply in order; each sees the result of the ones before it.

| op | Fields | Effect |
|---|---|---|
| `split_node` | `node_id`, `into` | Replace one node with copies that share its wiring, reviewers and contract. **The multi-channel primitive.** |
| `add_node` | `node_id`, `label`, `artifact`, `prompt`, `like` | Add a node. `like` inherits reviewers, contract and gates from an existing node. |
| `remove_node` | `node_id` | Remove a node and every edge touching it. |
| `add_edge` | `upstream`, `downstream`, `kind`, `inject` | Wire one node's output into another's prompt. |
| `remove_edge` | `upstream`, `downstream` | Unwire. |
| `set_prompt` | `node_id`, `prompt` | Rewrite what a node is asked to do. Give the **whole** new prompt. |

### Edge kinds

- `requires` — blocking. The downstream node cannot start until the upstream one
  has passed review. This is what determines execution order.
- `informs` — context only. The artifact is injected into the prompt if it
  exists, but the downstream node does not wait for it. Use this for a
  cross-check that is useful but not a prerequisite.

### Injection

`full` gives the downstream node the whole artifact, `summary` the first part of
it, `none` nothing at all. Use `none` when a node must depend on another for
*ordering* without seeing its contents — the blinding relationship between the
staged inference nodes is the case that matters.

## Rules

1. **Node ids are lowercase slugs**: `selection_ee`, `calibration`. They are
   stable names used in commit messages, review directories and regression
   tickets.
2. **The `requires` subgraph must be acyclic**, and at least one node must have
   no `requires` prerequisite.
3. **Every node needs a prompt** that stands on its own. A node added with
   `add_node` gets no prompt from anywhere else.
4. **Split, do not duplicate.** To run a phase per channel, use `split_node`.
   Adding two nodes by hand and rewiring loses the reviewers and the write-back
   contract.
5. **Preserve the gates** unless the physics removes the thing they guard. The
   commitments gate and the staged unblinding exist because unblinding is
   irreversible.

## Prompt Template

```
Here is the physics prompt for a new analysis, and the plan template that would
run by default.

Decide whether the template fits. If it does, return an empty edit list. If it
does not, return the smallest set of edits that makes it fit, and explain in the
rationale which part of the physics prompt required each one.

Common reasons to restructure, in rough order of frequency:

- Multiple channels or categories that are selected independently and combined
  at inference: split the exploration and selection nodes per channel with
  `split_node`. The inference nodes stay single and consume all of them.
- A calibration, efficiency or background estimate the analysis must derive
  itself rather than take from a published result: add a node before selection
  and wire it into the nodes that use it.
- Nothing to unblind — a reinterpretation, a generator-level study, an analysis
  of already-public data: remove the staged inference nodes that do not apply and
  wire the survivor to what fed them.
- A deliverable beyond the note: a limit scan, a HEPData record, a
  reinterpretation package. Add a node after documentation.

Return the rationale and the edits.
```
