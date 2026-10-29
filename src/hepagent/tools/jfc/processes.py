"""Agent-facing tools for the process inventory.

The strategy node decides what the analysis is *about* — the signal, the
backgrounds and how each background evades the selection, and the datasets that
carry them. Written as prose in `STRATEGY.md` that decision is unusable by
anything downstream; written here it becomes a checked document every later node
reads and the progress panel renders live.

`record_process_inventory` is deliberately one call for the whole inventory
rather than one per process. The classification is only meaningful as a set — a
background list missing its dominant irreducible component is wrong in a way no
single row shows — and a whole-document write gives one complete validation
answer instead of a round trip per row.

Writing is bounded by the same write-back contract as `graph_add_node`: a node
that may not create `process` and `dataset` graph nodes may not record an
inventory either, because recording one creates exactly those.
"""

from __future__ import annotations

from pathlib import Path

from agents import function_tool
from hepagent.agents.jfc.processes import (
    BACKGROUND_CATEGORIES,
    DATASET_SOURCES,
    IMPORTANCE_LEVELS,
    OWNER_NODE_TYPES,
    PROCESS_ROLES,
    InventoryError,
    ProcessInventory,
    find_inventory,
    save_inventory,
)
from hepagent.tools.jfc._resolve import resolve_node


@function_tool
async def record_process_inventory(
    analysis_root: str,
    node_id: str,
    inventory_json: str,
) -> str:
    """
    Record the signal, backgrounds and datasets this analysis will model.

    Write the whole inventory in one call. It is validated before it is stored,
    and an invalid document is refused with every problem listed, so fix them all
    and call again.

    `inventory_json` is a JSON object with a `processes` list. Each process:

        {"id": "dy", "label": "Z/gamma* -> ll (Drell-Yan)",
         "role": "background", "category": "irreducible",
         "importance": "dominant",
         "rationale": "same mu+tau_h final state as the signal",
         "estimation": "MC, normalised in a Z-enriched control region",
         "datasets": [{"name": "DYJetsToLL.root",
                       "path": "/path/to/DYJetsToLL.root",
                       "kind": "mc", "source": "prompt"}]}

    Rules:
      - `role` is one of: signal, background, data. At least one signal.
      - a background MUST carry `category`: irreducible (shares the signal's
        final state), reducible (removed by tighter selection), or instrumental
        (mis-measurement, mis-identification, fakes).
      - a process that is not a background carries no `category`.
      - `importance` is one of: dominant, major, minor, negligible, unknown.
      - every process needs at least one dataset.
      - `source` says where the dataset entry came from: prompt (the physics
        brief), rucio/catalog (a lookup), or inferred (your own reasoning).

    Returns a confirmation with the recorded inventory, or an error string
    starting with "Error:".

    Args:
        analysis_root: Absolute path to the analysis root directory.
        node_id: The plan node you are working on, e.g. "strategy".
        inventory_json: The inventory document, as JSON.
    """
    plan_node, error = resolve_node(analysis_root, node_id)
    if plan_node is None:
        return error

    allowed = set(plan_node.contract.node_types)
    missing = [name for name in OWNER_NODE_TYPES if name not in allowed]
    if missing:
        return (
            f"Error: node '{plan_node.id}' may not record a process inventory — its "
            f"graph write-back contract does not allow the node type(s): "
            f"{', '.join(missing)}. Allowed: {', '.join(sorted(allowed)) or 'none'}."
        )

    try:
        inventory = ProcessInventory.from_json(inventory_json)
    except InventoryError as exc:
        return f"Error: {exc}"

    problems = inventory.problems()
    if problems:
        return (
            "Error: the inventory was not recorded. Fix all of these and call again:\n"
            + "\n".join(f"  - {problem}" for problem in problems)
            + f"\n\nroles: {', '.join(PROCESS_ROLES)}"
            + f"\nbackground categories: {', '.join(BACKGROUND_CATEGORIES)}"
            + f"\nimportance: {', '.join(IMPORTANCE_LEVELS)}"
            + f"\ndataset sources: {', '.join(DATASET_SOURCES)}"
        )

    root = Path(analysis_root)
    try:
        path = save_inventory(root, plan_node, inventory)
    except OSError as exc:
        return f"Error: could not write the inventory: {exc}"

    # Ingest straight away rather than at the node boundary: the progress panel
    # and any reviewer reading the graph should see the inventory the moment it
    # exists. Ingestion is idempotent, so the node-boundary pass is still correct.
    ingested = ""
    try:
        from hepagent.agents.jfc.graph_builder import ingest_node

        report = ingest_node(root, plan_node.id)
        ingested = f"\nGraph: {report.summary()}"
    except Exception as exc:  # noqa: BLE001 - graph work never fails a run
        ingested = f"\nGraph ingestion deferred to the node boundary ({exc})."

    counts = {role: len(inventory.by_role(role)) for role in PROCESS_ROLES}
    return (
        f"Recorded {len(inventory.processes)} processes to {path} "
        f"({counts['signal']} signal, {counts['background']} background, "
        f"{counts['data']} data; {len(inventory.datasets())} datasets).\n"
        f"{inventory.summary()}{ingested}"
    )


@function_tool
async def read_process_inventory(analysis_root: str) -> str:
    """
    Read the signal, backgrounds and datasets the strategy settled on.

    Use this before doing anything per-process — plotting one histogram per
    sample, defining a control region, building fit templates — so the names,
    the classification and the dataset paths match what the strategy recorded
    rather than being invented again.

    Returns the inventory as text, or an error string starting with "Error:".

    Args:
        analysis_root: Absolute path to the analysis root directory.
    """
    root = Path(analysis_root)
    if not root.is_dir():
        return f"Error: analysis root not found: {analysis_root}"
    try:
        from hepagent.plan.store import load_plan

        plan = load_plan(root)
    except Exception as exc:  # noqa: BLE001 - reported to the agent, not raised
        return f"Error: could not read the analysis plan: {exc}"

    inventory, owner = find_inventory(root, plan)
    if inventory is None:
        return (
            "No process inventory has been recorded for this analysis yet. "
            "The strategy node records it with record_process_inventory."
        )

    lines = [f"Process inventory (recorded by node '{owner.id if owner else '?'}'):"]
    for process in inventory.processes:
        what = process.role if process.role != "background" else f"background/{process.category}"
        lines.append(f"\n{process.id} — {process.label}")
        lines.append(f"  role: {what}    importance: {process.importance}")
        if process.rationale:
            lines.append(f"  why: {process.rationale}")
        if process.estimation:
            lines.append(f"  estimated by: {process.estimation}")
        for dataset in process.datasets:
            location = f" @ {dataset.path}" if dataset.path else ""
            lines.append(f"  dataset: {dataset.name} [{dataset.kind}, {dataset.source}]{location}")
    if inventory.notes:
        lines.append(f"\nNotes: {inventory.notes}")
    return "\n".join(lines)
