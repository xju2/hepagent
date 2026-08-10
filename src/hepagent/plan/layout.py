"""Deterministic layered layout for drawing a plan.

The editor page draws SVG; it does not compute geometry. Layout happens here, in
Python, for three reasons: it is unit-testable, it is identical in the browser
and in `plan show`, and it keeps the browser side small enough to stay a single
self-contained file.

The result is a grid, not pixels — ``(column, row)``. Columns are dependency
depth, so everything a node depends on sits strictly to its left. Turning the
grid into coordinates is the renderer's business.

Positions a user has dragged are stored on the node itself
(``node.metadata["x"]`` / ``["y"]``); this module never reads them, so
`auto_layout` always answers "where would this node go if nobody had moved it".
"""

from __future__ import annotations

from hepagent.plan.schema import AnalysisPlan

#: Cap on the barycentre passes used to reduce edge crossings. One pass fixes
#: the common fan-out/fan-in case; more has diminishing returns and costs
#: determinism nothing, but there is no reason to spend it.
_BARYCENTRE_PASSES = 1


def layer(plan: AnalysisPlan) -> dict[str, tuple[int, int]]:
    """Return ``{node_id: (column, row)}`` for every node in the plan.

    Column is the longest `requires` chain from a starting node, so a node always
    sits to the right of everything it depends on. Row orders nodes within a
    column, pulled towards the average row of their prerequisites so fan-outs
    stay untangled, with declaration order breaking every tie.

    A plan containing a cycle still gets a layout — the nodes caught in it are
    placed in the column after their last resolvable prerequisite — so the editor
    can render the cycle for the user to fix rather than showing nothing.
    """
    columns = _columns(plan)
    order = {node.id: index for index, node in enumerate(plan.nodes)}

    by_column: dict[int, list[str]] = {}
    for node in plan.nodes:
        by_column.setdefault(columns[node.id], []).append(node.id)
    for members in by_column.values():
        members.sort(key=lambda node_id: order[node_id])

    rows = {node_id: row for members in by_column.values() for row, node_id in enumerate(members)}

    for _ in range(_BARYCENTRE_PASSES):
        for column in sorted(by_column):
            if column == 0:
                continue
            members = by_column[column]
            members.sort(key=lambda node_id: (_barycentre(plan, node_id, rows), order[node_id]))
            for row, node_id in enumerate(members):
                rows[node_id] = row

    return {node.id: (columns[node.id], rows[node.id]) for node in plan.nodes}


def columns(plan: AnalysisPlan) -> dict[str, int]:
    """Return ``{node_id: column}`` — dependency depth for every node."""
    return _columns(plan)


def width(plan: AnalysisPlan) -> int:
    """Number of columns the plan occupies."""
    positions = layer(plan)
    return max((column for column, _ in positions.values()), default=-1) + 1


def height(plan: AnalysisPlan) -> int:
    """Number of rows in the tallest column."""
    positions = layer(plan)
    return max((row for _, row in positions.values()), default=-1) + 1


def _columns(plan: AnalysisPlan) -> dict[str, int]:
    """Longest-path depth per node, cycle-safe.

    Depth is resolved iteratively rather than recursively: on each pass a node
    whose prerequisites are all resolved takes one more than their maximum. When
    a pass resolves nothing, whatever is left is in a cycle and is placed after
    the deepest prerequisite that *did* resolve.
    """
    known = set(plan.node_ids())
    prerequisites = {
        node.id: [p for p in plan.prerequisites(node.id) if p in known] for node in plan.nodes
    }

    depth: dict[str, int] = {}
    pending = [node.id for node in plan.nodes]

    while pending:
        progressed = False
        deferred = []
        for node_id in pending:
            upstream = prerequisites[node_id]
            if all(p in depth for p in upstream):
                depth[node_id] = 1 + max((depth[p] for p in upstream), default=-1)
                progressed = True
            else:
                deferred.append(node_id)
        if not progressed:
            for node_id in deferred:
                resolved = [depth[p] for p in prerequisites[node_id] if p in depth]
                depth[node_id] = 1 + max(resolved, default=-1)
            break
        pending = deferred

    return depth


def _barycentre(plan: AnalysisPlan, node_id: str, rows: dict[str, int]) -> float:
    """Average row of a node's prerequisites, or its own row when it has none."""
    upstream = [rows[p] for p in plan.prerequisites(node_id) if p in rows]
    if not upstream:
        return float(rows.get(node_id, 0))
    return sum(upstream) / len(upstream)
