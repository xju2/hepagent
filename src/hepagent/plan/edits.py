"""Structural edits to an analysis plan.

The Architect does not write a plan from nothing. It starts from a template and
proposes *edits* — split this node per channel, drop that one, rewire this edge,
rewrite this prompt. That framing is deliberate:

- The shipped prompts are long and carefully written. An agent asked to reproduce
  them verbatim will paraphrase them, and the paraphrase is what would run.
- A short edit list is reviewable. A regenerated plan is not.
- An edit that cannot be applied is *skipped and reported*, so a partly-wrong
  proposal degrades to a partly-edited plan rather than to nothing.

Nothing here calls a model, and nothing here validates: apply first, then run
`validate_plan` over the result. Applying an edit is allowed to produce an
invalid plan — that is what the validator is for, and what the repair round in
`agents.jfc.architect` feeds back.
"""

from __future__ import annotations

import dataclasses
import re
from dataclasses import dataclass, field

from hepagent.plan.schema import (
    AnalysisPlan,
    ConditionMetric,
    PlanCondition,
    PlanEdge,
    PlanNode,
)

#: Operations an edit may name. Anything else is skipped with a note.
OPS = (
    "add_node",
    "remove_node",
    "split_node",
    "add_edge",
    "remove_edge",
    "set_prompt",
    "add_loop",
)

_SLUG_UNSAFE = re.compile(r"[^a-z0-9_-]+")


@dataclass
class PlanEdit:
    """One structural change to a plan.

    Fields are a flat union over the operations rather than a nested variant
    type: the schema is handed to a model, and a flat object with unused fields
    left null is markedly easier to emit correctly than a discriminated union.

    Args:
        op: One of `OPS`.
        node_id: The node being added, removed, split, or re-prompted.
        label: Display name, for `add_node`.
        directory: Working directory, for `add_node`. Defaults to `node_id`.
        artifact: Primary output filename, for `add_node`.
        prompt: Executor prompt, for `add_node` and `set_prompt`.
        like: An existing node whose reviewers, contract, gates, role and
            iteration budget the new node should inherit.
        into: New node ids, for `split_node`.
        upstream: Producing node, for the edge operations. For `add_loop`, the
            node whose output the condition tests.
        downstream: Consuming node, for the edge operations. For `add_loop`, the
            node the analysis moves on to once the condition holds.
        kind: `requires` (blocking), `informs` (context only), or `on_true` /
            `on_false` for a condition's branches.
        inject: `full`, `summary` or `none` — how the artifact enters the prompt.
        loop_to: For `add_loop`, the node to rewind to when the condition does
            not hold. This is what makes the plan loop.
        question: For `add_loop`, the natural-language break condition.
        metric_source: For `add_loop`, an analysis-root-relative results JSON.
        metric_key: For `add_loop`, a dotted path into that JSON.
        metric_compare: For `add_loop`, see `COMPARISONS`.
        metric_value: For `add_loop`, the threshold.
        max_iterations: For `add_loop`, how many times the body may run. This is
            the loop's termination guarantee, so it is never left unset.
    """

    op: str
    node_id: str | None = None
    label: str | None = None
    directory: str | None = None
    artifact: str | None = None
    prompt: str | None = None
    like: str | None = None
    into: tuple[str, ...] = ()
    upstream: str | None = None
    downstream: str | None = None
    kind: str | None = None
    inject: str | None = None
    loop_to: str | None = None
    question: str | None = None
    metric_source: str | None = None
    metric_key: str | None = None
    metric_compare: str | None = None
    metric_value: float | None = None
    max_iterations: int | None = None


@dataclass
class EditReport:
    """The outcome of applying an edit list.

    Args:
        plan: The edited plan. Equal to the input when nothing applied.
        applied: One line per edit that took effect.
        skipped: One line per edit that did not, saying why.
    """

    plan: AnalysisPlan
    applied: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)

    def summary(self) -> str:
        return f"{len(self.applied)} edit(s) applied, {len(self.skipped)} skipped"


def slugify(value: str) -> str:
    """Coerce a string into a plan-safe node id, or return "" if impossible."""
    slug = _SLUG_UNSAFE.sub("_", value.strip().lower()).strip("_-")
    return slug if slug and slug[0].isalpha() else ""


def apply_edits(plan: AnalysisPlan, edits: list[PlanEdit]) -> EditReport:
    """Apply edits in order, skipping any that cannot be applied.

    Order matters: an edit sees the result of the ones before it, so a proposal
    may split a node and then rewire one of the halves.
    """
    report = EditReport(plan=plan)
    for index, edit in enumerate(edits):
        handler = _HANDLERS.get(edit.op)
        if handler is None:
            report.skipped.append(f"edit {index}: unknown op '{edit.op}'")
            continue
        try:
            result = handler(report.plan, edit)
        except _EditRejected as exc:
            report.skipped.append(f"edit {index} ({edit.op}): {exc}")
            continue
        report.plan, note = result
        report.applied.append(f"edit {index} ({edit.op}): {note}")
    return report


class _EditRejected(ValueError):
    """An edit that cannot be applied to this plan. Reported, never raised out."""


def _require_node(plan: AnalysisPlan, node_id: str | None, what: str) -> PlanNode:
    if not node_id:
        raise _EditRejected(f"no {what} given")
    node = plan.node(node_id)
    if node is None:
        raise _EditRejected(f"no node '{node_id}' in the plan")
    return node


def _insert_after(nodes: tuple[PlanNode, ...], anchor: str, new: list[PlanNode]):
    """Place new nodes just after `anchor`, keeping declaration order meaningful."""
    ids = [n.id for n in nodes]
    at = ids.index(anchor) + 1 if anchor in ids else len(nodes)
    return tuple(list(nodes[:at]) + new + list(nodes[at:]))


def _add_node(plan: AnalysisPlan, edit: PlanEdit) -> tuple[AnalysisPlan, str]:
    node_id = slugify(edit.node_id or "")
    if not node_id:
        raise _EditRejected(f"'{edit.node_id}' is not a usable node id")
    if plan.node(node_id) is not None:
        raise _EditRejected(f"node '{node_id}' already exists")
    if not (edit.prompt or "").strip():
        raise _EditRejected("a new node needs a prompt")

    template = plan.node(edit.like) if edit.like else None
    if edit.like and template is None:
        raise _EditRejected(f"cannot inherit from '{edit.like}': no such node")

    blank = PlanNode(id=node_id, label=node_id, directory=node_id, artifact="")
    node = dataclasses.replace(
        template if template is not None else blank,
        id=node_id,
        label=edit.label or node_id.replace("_", " ").title(),
        directory=edit.directory or node_id,
        artifact=edit.artifact or f"{node_id.upper()}.md",
        note_artifact="",
        produces_note=False,
        prompt=edit.prompt,
    )
    anchor = edit.like or (plan.node_ids()[-1] if plan.nodes else "")
    return (
        dataclasses.replace(plan, nodes=_insert_after(plan.nodes, anchor, [node])),
        f"added '{node_id}'" + (f" modelled on '{edit.like}'" if template else ""),
    )


def _remove_node(plan: AnalysisPlan, edit: PlanEdit) -> tuple[AnalysisPlan, str]:
    node = _require_node(plan, edit.node_id, "node id")
    edges = tuple(e for e in plan.edges if node.id not in (e.upstream, e.downstream))
    dropped = len(plan.edges) - len(edges)
    return (
        dataclasses.replace(
            plan,
            nodes=tuple(n for n in plan.nodes if n.id != node.id),
            edges=edges,
        ),
        f"removed '{node.id}' and {dropped} edge(s)",
    )


def _split_node(plan: AnalysisPlan, edit: PlanEdit) -> tuple[AnalysisPlan, str]:
    """Replace one node with several copies that share its wiring.

    This is the multi-channel primitive: per-channel selection nodes that all
    consume the same upstream and all feed the same downstream. Directories are
    derived from the new ids rather than taken from the proposal, so the copies
    cannot collide on disk.
    """
    source = _require_node(plan, edit.node_id, "node id")
    new_ids = [slugify(i) for i in edit.into]
    if len(new_ids) < 2 or not all(new_ids):
        raise _EditRejected("a split needs at least two usable node ids")
    if len(set(new_ids)) != len(new_ids):
        raise _EditRejected("split ids must be distinct")
    clash = [i for i in new_ids if i != source.id and plan.node(i) is not None]
    if clash:
        raise _EditRejected(f"split ids already in the plan: {', '.join(clash)}")

    replicas = [
        dataclasses.replace(
            source,
            id=new_id,
            label=f"{source.label} ({new_id.rsplit('_', 1)[-1]})",
            directory=f"{source.directory}_{new_id.rsplit('_', 1)[-1]}",
        )
        for new_id in new_ids
    ]

    edges: list[PlanEdge] = []
    for edge in plan.edges:
        if edge.upstream == source.id:
            edges += [dataclasses.replace(edge, upstream=r.id) for r in replicas]
        elif edge.downstream == source.id:
            edges += [dataclasses.replace(edge, downstream=r.id) for r in replicas]
        else:
            edges.append(edge)

    # The replicas take the source's place in declaration order, which is what
    # breaks ties when several of them come ready at once.
    at = [n.id for n in plan.nodes].index(source.id)
    others = [n for n in plan.nodes if n.id != source.id]
    nodes = tuple(others[:at] + replicas + others[at:])

    return (
        dataclasses.replace(plan, nodes=nodes, edges=tuple(edges)),
        f"split '{source.id}' into {', '.join(new_ids)}",
    )


def _add_edge(plan: AnalysisPlan, edit: PlanEdit) -> tuple[AnalysisPlan, str]:
    upstream = _require_node(plan, edit.upstream, "upstream node")
    downstream = _require_node(plan, edit.downstream, "downstream node")
    if upstream.id == downstream.id:
        raise _EditRejected("a node cannot depend on itself")
    if any(e.upstream == upstream.id and e.downstream == downstream.id for e in plan.edges):
        raise _EditRejected(f"'{upstream.id}' already feeds '{downstream.id}'")

    edge = PlanEdge(
        upstream=upstream.id,
        downstream=downstream.id,
        kind=edit.kind or "requires",
        inject=edit.inject or "full",
    )
    return (
        dataclasses.replace(plan, edges=plan.edges + (edge,)),
        f"{edge.upstream} --{edge.kind}--> {edge.downstream}",
    )


def _remove_edge(plan: AnalysisPlan, edit: PlanEdit) -> tuple[AnalysisPlan, str]:
    edges = tuple(
        e
        for e in plan.edges
        if not (e.upstream == edit.upstream and e.downstream == edit.downstream)
    )
    if len(edges) == len(plan.edges):
        raise _EditRejected(f"no edge '{edit.upstream}' -> '{edit.downstream}'")
    return (
        dataclasses.replace(plan, edges=edges),
        f"removed {edit.upstream} -> {edit.downstream}",
    )


def _set_prompt(plan: AnalysisPlan, edit: PlanEdit) -> tuple[AnalysisPlan, str]:
    node = _require_node(plan, edit.node_id, "node id")
    if not (edit.prompt or "").strip():
        raise _EditRejected("an empty prompt would leave the node with nothing to do")
    updated = dataclasses.replace(node, prompt=edit.prompt)
    return (
        dataclasses.replace(
            plan, nodes=tuple(updated if n.id == node.id else n for n in plan.nodes)
        ),
        f"rewrote the prompt for '{node.id}' ({len(edit.prompt or '')} chars)",
    )


def _add_loop(plan: AnalysisPlan, edit: PlanEdit) -> tuple[AnalysisPlan, str]:
    """Insert a condition node that loops, in one atomic edit.

    A loop is three things at once — a condition node, a branch back to the node
    that gets re-run, and a branch forward to whatever comes after. Proposing
    them as three separate edits means any one of them can be skipped, leaving a
    plan with a condition that routes nowhere or a branch from a work node. This
    is the same reasoning as `split_node`: the reviewable unit is the structural
    change, not its parts.
    """
    tested = _require_node(plan, edit.upstream, "upstream node")
    rewind = _require_node(plan, edit.loop_to, "loop_to node")
    node_id = slugify(edit.node_id or f"{tested.id}_converged")
    if not node_id:
        raise _EditRejected(f"'{edit.node_id}' is not a usable node id")
    if plan.node(node_id) is not None:
        raise _EditRejected(f"node '{node_id}' already exists")

    exit_node = plan.node(edit.downstream) if edit.downstream else None
    if edit.downstream and exit_node is None:
        raise _EditRejected(f"no node '{edit.downstream}' to leave the loop for")

    metric = None
    if edit.metric_source and edit.metric_key:
        metric = ConditionMetric(
            source=edit.metric_source,
            key=edit.metric_key,
            compare=edit.metric_compare or "improvement_below",
            value=float(edit.metric_value or 0.0),
        )
    if metric is None and not (edit.question or "").strip():
        raise _EditRejected("a loop needs a metric or a question to break on")

    condition = PlanNode(
        id=node_id,
        label=edit.label or node_id.replace("_", " ").title(),
        directory=edit.directory or node_id,
        artifact=edit.artifact or f"{node_id.upper()}.md",
        kind="condition",
        condition=PlanCondition(
            question=edit.question or "",
            metric=metric,
            # Bounded whatever the proposal said: an unbounded loop is the one
            # thing a condition node exists to make impossible.
            max_iterations=max(int(edit.max_iterations or 3), 1),
        ),
    )

    edges = [
        PlanEdge(upstream=tested.id, downstream=node_id, inject=edit.inject or "summary"),
        PlanEdge(upstream=node_id, downstream=rewind.id, kind="on_false", inject="full"),
    ]
    if exit_node is not None:
        edges.append(PlanEdge(upstream=node_id, downstream=exit_node.id, kind="on_true"))
        # Whatever used to depend on the tested node now waits for the loop to
        # finish instead; otherwise the exit node runs before the first pass.
        remaining = tuple(
            e for e in plan.edges if not (e.upstream == tested.id and e.downstream == exit_node.id)
        )
    else:
        remaining = plan.edges

    return (
        dataclasses.replace(
            plan,
            nodes=_insert_after(plan.nodes, tested.id, [condition]),
            edges=remaining + tuple(edges),
        ),
        f"added loop '{node_id}': {tested.id} → {node_id}, "
        f"rewinding to '{rewind.id}', max {condition.condition.max_iterations} pass(es)",
    )


_HANDLERS = {
    "add_node": _add_node,
    "remove_node": _remove_node,
    "split_node": _split_node,
    "add_edge": _add_edge,
    "remove_edge": _remove_edge,
    "set_prompt": _set_prompt,
    "add_loop": _add_loop,
}
