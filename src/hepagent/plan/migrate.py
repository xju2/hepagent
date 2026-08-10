"""Give a pre-plan JFC analysis directory a `plan.json`.

Analyses scaffolded before plans existed have their structure implied by
directory names (`phase4a_inference_expected/`) and their progress recorded
against phase keys (`"4a"`). Migration writes the equivalent plan and rewrites
the orchestration state to name nodes instead.

The on-disk directory and artifact names in the shipped templates are unchanged
from the legacy layout, so nothing moves: the migration is a relabelling, and the
provenance graph — whose node ids are content-addressed on paths — survives it
intact. Only `Node.phase` changes, which `graph rebuild` refreshes.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from hepagent.plan.schema import AnalysisPlan
from hepagent.plan.store import has_plan, save_plan
from hepagent.plan.templates import DEFAULT_TEMPLATE, instantiate

STATE_FILENAME = ".orchestration_state.json"

#: Legacy phase key -> node id in the shipped JFC templates. Both templates use
#: the same slugs, so one table covers measurement and search.
PHASE_TO_NODE = {
    "1": "strategy",
    "2": "exploration",
    "3": "selection",
    "4a": "inference_expected",
    "4b": "inference_partial",
    "4c": "inference_observed",
    "5": "documentation",
}


class MigrationError(RuntimeError):
    """Raised when an analysis cannot be migrated without losing information."""


@dataclass
class MigrationResult:
    """What a migration did.

    Args:
        plan: The plan written to the analysis directory.
        notes: Human-readable lines describing each step, for the CLI to echo.
    """

    plan: AnalysisPlan
    notes: list[str] = field(default_factory=list)


def _legacy_state(analysis_root: Path) -> dict:
    path = analysis_root / STATE_FILENAME
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise MigrationError(f"{path} is not readable: {exc}") from exc
    return data if isinstance(data, dict) else {}


def _remap(value: object, known: set[str]) -> str | None:
    """Map a legacy phase key to a node id, passing through ids already migrated."""
    if value is None:
        return None
    key = str(value)
    if key in known:
        return key
    return PHASE_TO_NODE.get(key)


def migrate_state(state: dict, plan: AnalysisPlan) -> tuple[dict, list[str]]:
    """Rewrite an orchestration state dict from phase keys to node ids.

    Unrecognised keys are dropped rather than guessed at — a phase key with no
    counterpart in the plan describes work the plan does not contain, and
    carrying it forward would make `completed_nodes` lie.
    """
    known = set(plan.node_ids())
    notes: list[str] = []
    migrated = dict(state)

    completed_in = state.get("completed_nodes", state.get("completed_phases", []))
    completed: list[str] = []
    for entry in completed_in if isinstance(completed_in, list) else []:
        node_id = _remap(entry, known)
        if node_id is None:
            notes.append(f"dropped completed phase '{entry}' — no matching node in the plan")
            continue
        if node_id not in completed:
            completed.append(node_id)
    migrated["completed_nodes"] = completed
    migrated.pop("completed_phases", None)

    current_in = state.get(
        "current_node", state.get("current_subphase", state.get("current_phase"))
    )
    current = _remap(current_in, known)
    if current_in is not None and current is None:
        notes.append(f"current phase '{current_in}' has no matching node; reset to the entry node")
    if current is None:
        entry_nodes = plan.entry_nodes()
        current = entry_nodes[0].id if entry_nodes else (plan.node_ids()[0] if plan.nodes else "")
    migrated["current_node"] = current
    migrated.pop("current_subphase", None)
    migrated.pop("current_phase", None)

    iterations = state.get("phase_iterations", {})
    if isinstance(iterations, dict):
        remapped: dict[str, int] = {}
        for key, count in iterations.items():
            node_id = _remap(key, known)
            if node_id is not None:
                remapped[node_id] = count
        migrated["phase_iterations"] = remapped

    return migrated, notes


def migrate_analysis(
    analysis_root: Path | str,
    *,
    template: str = DEFAULT_TEMPLATE,
    force: bool = False,
) -> MigrationResult:
    """Write a plan for a legacy analysis and rewrite its orchestration state.

    Args:
        analysis_root: The analysis directory.
        template: Template whose node ids the legacy phase keys map onto.
        force: Overwrite an existing `plan.json` instead of refusing.

    Raises:
        MigrationError: The directory is not an analysis, or already has a plan
            and `force` was not given.
    """
    root = Path(analysis_root)
    if not root.is_dir():
        raise MigrationError(f"not a directory: {root}")
    if has_plan(root) and not force:
        raise MigrationError(
            f"{root} already has a plan.json. Pass --force to overwrite it "
            f"(the current one is archived to plan.history/)."
        )

    state = _legacy_state(root)
    analysis_type = str(state.get("analysis_type") or "measurement")
    prompt_file = root / "prompt.md"
    prompt = prompt_file.read_text(encoding="utf-8") if prompt_file.is_file() else ""

    plan = instantiate(
        template,
        analysis_name=str(state.get("analysis_name") or root.name),
        analysis_type=analysis_type,
        physics_prompt=prompt,
    )

    notes = [f"template '{template}' instantiated as {len(plan.nodes)} nodes"]

    if state:
        migrated, state_notes = migrate_state(state, plan)
        notes.extend(state_notes)
        (root / STATE_FILENAME).write_text(json.dumps(migrated, indent=2) + "\n", encoding="utf-8")
        notes.append(
            f"state rewritten: {len(migrated['completed_nodes'])} completed, "
            f"current node '{migrated['current_node']}'"
        )
    else:
        notes.append("no orchestration state found; nothing to rewrite")

    save_plan(root, plan)
    return MigrationResult(plan=plan, notes=notes)
