"""The physics-process inventory an analysis strategy settles on.

Phase 1 answers a question no artifact on disk can be parsed for: *what is the
signal, what are the backgrounds, and which datasets carry each of them*. That
answer is the input every later node needs — the exploration plots one histogram
per process, the selection tunes against the reducible ones, the fit needs to
know which template is irreducible — so it is written once, in a machine-readable
form, rather than re-extracted from prose at each step.

Three rules shape this module:

1. **The file lives on the node, not on a phase.** `inventory_path` is built from
   `PlanNode.outputs_dir`, so a plan that renames `phase1_strategy` or moves the
   inventory to a different node needs no code change. There is deliberately no
   phase -> path table here, exactly as `docs/PLAN.md` requires.
2. **The vocabulary is closed and validated.** A background is `irreducible`,
   `reducible` or `instrumental` and nothing else, because the panel, the
   downstream prompts and the graph all key off that word. A model that invents a
   fourth category is told the three that exist rather than silently recorded.
3. **It is domain data, not runtime state.** The inventory says what the analysis
   is *about*; the graph says what happened to it. `graph_builder` ingests this
   file into `process` and `dataset` nodes, and that ingestion is derived — the
   file stays the source of truth and can be regenerated from at any time.

Dataset provenance is recorded per dataset (`source`), because today the
datasets come from the physics prompt and tomorrow they come from a catalogue
query; a consumer that has to tell those apart should not have to guess.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from hepagent.graph.schema import utc_now
from hepagent.plan.schema import AnalysisPlan, PlanNode

#: The filename an inventory takes inside its node's `outputs/` directory.
INVENTORY_FILENAME = "processes.json"

#: What a process *is* in the analysis. `data` is the observed dataset itself,
#: which is neither signal nor background but has to be inventoried alongside
#: them — it is the one process whose datasets are not simulated.
PROCESS_ROLES: tuple[str, ...] = ("signal", "background", "data")

#: How a background evades the selection, which is what decides how it is
#: estimated. Irreducible shares the signal's final state and must be modelled;
#: reducible is removed by tighter selection; instrumental comes from
#: mis-measurement or mis-identification and is usually estimated from data.
BACKGROUND_CATEGORIES: tuple[str, ...] = ("irreducible", "reducible", "instrumental")

#: Rough size relative to the signal. Ordered, so a consumer can sort by it.
IMPORTANCE_LEVELS: tuple[str, ...] = ("dominant", "major", "minor", "negligible", "unknown")

#: Where a dataset entry came from. "prompt" is the physics brief the user wrote;
#: "rucio" is a catalogue lookup; "inferred" is the executor's own reasoning,
#: which a reviewer should treat as a claim rather than a fact.
DATASET_SOURCES: tuple[str, ...] = ("prompt", "rucio", "catalog", "inferred")


class InventoryError(ValueError):
    """Raised when an inventory document does not satisfy the schema."""


def _clean(value: Any) -> str:
    return str(value or "").strip()


@dataclass(frozen=True)
class Dataset:
    """One concrete file or container carrying a process.

    Args:
        name: Short identifier, unique within the inventory. For a Rucio DID
            this is the DID; for an open-data file, its basename.
        path: Where it actually lives — a directory, URL or file path.
        kind: `mc` or `data`.
        source: One of `DATASET_SOURCES`; how this entry was obtained.
        events: Recorded event count, when known. 0 means "not known".
        metadata: Free-form detail — campaign, generator, cross-section, tag.
    """

    name: str
    path: str = ""
    kind: str = "mc"
    source: str = "prompt"
    events: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "path": self.path,
            "kind": self.kind,
            "source": self.source,
            "events": self.events,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: Any) -> Dataset:
        if isinstance(data, str):  # a bare name is a legal shorthand
            return cls(name=data.strip())
        if not isinstance(data, dict):
            raise InventoryError(
                f"A dataset must be an object or a string, got {type(data).__name__}"
            )
        metadata = data.get("metadata") or {}
        if not isinstance(metadata, dict):
            raise InventoryError(f"Dataset '{data.get('name')}' metadata must be an object")
        try:
            events = int(data.get("events") or 0)
        except (TypeError, ValueError):
            events = 0
        return cls(
            name=_clean(data.get("name") or data.get("did")),
            path=_clean(data.get("path")),
            kind=_clean(data.get("kind")) or "mc",
            source=_clean(data.get("source")) or "prompt",
            events=events,
            metadata=metadata,
        )

    def problems(self) -> list[str]:
        issues = []
        if not self.name:
            issues.append("a dataset has no name")
        if self.kind not in ("mc", "data"):
            issues.append(f"dataset '{self.name}': kind must be 'mc' or 'data', got '{self.kind}'")
        if self.source not in DATASET_SOURCES:
            issues.append(
                f"dataset '{self.name}': source must be one of "
                f"{', '.join(DATASET_SOURCES)}, got '{self.source}'"
            )
        return issues


@dataclass(frozen=True)
class Process:
    """One physics process the analysis models.

    Args:
        id: Stable slug — `ggH`, `dy`, `wjets`. Referenced by later nodes, so it
            must not change once written.
        label: Human-readable name, e.g. "Z/gamma* -> ll (Drell-Yan)".
        role: One of `PROCESS_ROLES`.
        category: For `role="background"` only, one of `BACKGROUND_CATEGORIES`.
        importance: Rough size relative to the signal.
        rationale: Why it is classified this way — the sentence a reviewer reads.
        estimation: How its yield will be obtained (MC, data-driven control
            region, fake factor, ...). Empty until the strategy decides.
        datasets: The files or containers carrying it.
    """

    id: str
    label: str = ""
    role: str = "background"
    category: str = ""
    importance: str = "unknown"
    rationale: str = ""
    estimation: str = ""
    datasets: tuple[Dataset, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "role": self.role,
            "category": self.category,
            "importance": self.importance,
            "rationale": self.rationale,
            "estimation": self.estimation,
            "datasets": [d.to_dict() for d in self.datasets],
        }

    @classmethod
    def from_dict(cls, data: Any) -> Process:
        if not isinstance(data, dict):
            raise InventoryError(f"A process must be an object, got {type(data).__name__}")
        raw = data.get("datasets") or []
        if not isinstance(raw, list):
            raise InventoryError(f"Process '{data.get('id')}': datasets must be a list")
        return cls(
            id=_clean(data.get("id")),
            label=_clean(data.get("label")) or _clean(data.get("id")),
            role=_clean(data.get("role")) or "background",
            category=_clean(data.get("category")),
            importance=_clean(data.get("importance")) or "unknown",
            rationale=_clean(data.get("rationale")),
            estimation=_clean(data.get("estimation")),
            datasets=tuple(Dataset.from_dict(d) for d in raw),
        )

    def problems(self) -> list[str]:
        issues = []
        if not self.id:
            issues.append("a process has no id")
        if self.role not in PROCESS_ROLES:
            issues.append(
                f"process '{self.id}': role must be one of "
                f"{', '.join(PROCESS_ROLES)}, got '{self.role}'"
            )
        if self.role == "background":
            if not self.category:
                issues.append(
                    f"process '{self.id}': a background must be classified as one of "
                    f"{', '.join(BACKGROUND_CATEGORIES)}"
                )
            elif self.category not in BACKGROUND_CATEGORIES:
                issues.append(
                    f"process '{self.id}': category must be one of "
                    f"{', '.join(BACKGROUND_CATEGORIES)}, got '{self.category}'"
                )
        elif self.category:
            issues.append(
                f"process '{self.id}': only a background carries a category "
                f"(this one is '{self.role}')"
            )
        if self.importance not in IMPORTANCE_LEVELS:
            issues.append(
                f"process '{self.id}': importance must be one of "
                f"{', '.join(IMPORTANCE_LEVELS)}, got '{self.importance}'"
            )
        if not self.datasets:
            issues.append(f"process '{self.id}': no dataset recorded")
        for dataset in self.datasets:
            issues.extend(dataset.problems())
        return issues


@dataclass(frozen=True)
class ProcessInventory:
    """Every process an analysis models, with the datasets behind each.

    Args:
        processes: The inventory itself, in the order the author wrote it.
        node_id: Plan node that recorded it.
        updated_at: ISO-8601 UTC timestamp of the last write.
        notes: Anything that belongs to the inventory as a whole.
    """

    processes: tuple[Process, ...] = ()
    node_id: str = ""
    updated_at: str = ""
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "node_id": self.node_id,
            "updated_at": self.updated_at,
            "notes": self.notes,
            "processes": [p.to_dict() for p in self.processes],
        }

    @classmethod
    def from_dict(cls, data: Any) -> ProcessInventory:
        """Parse an inventory document.

        A bare list of processes is accepted as well as the full envelope: the
        shorthand is what a model reaches for, and refusing it buys nothing.
        """
        if isinstance(data, list):
            data = {"processes": data}
        if not isinstance(data, dict):
            raise InventoryError(f"An inventory must be an object, got {type(data).__name__}")
        raw = data.get("processes")
        if raw is None:
            raise InventoryError("An inventory must have a 'processes' list")
        if not isinstance(raw, list):
            raise InventoryError("'processes' must be a list")
        return cls(
            processes=tuple(Process.from_dict(p) for p in raw),
            node_id=_clean(data.get("node_id")),
            updated_at=_clean(data.get("updated_at")),
            notes=_clean(data.get("notes")),
        )

    @classmethod
    def from_json(cls, text: str) -> ProcessInventory:
        try:
            return cls.from_dict(json.loads(text))
        except json.JSONDecodeError as exc:
            raise InventoryError(f"not valid JSON: {exc}") from exc

    # ---------------------------------------------------------------- checks

    def problems(self) -> list[str]:
        """Every schema violation, as sentences an agent can act on.

        Returns an empty list for a valid inventory. Collecting all of them
        rather than raising on the first means one tool call gets one complete
        correction, not a round trip per mistake.
        """
        issues: list[str] = []
        if not self.processes:
            issues.append("the inventory is empty — record at least the signal")
        seen: dict[str, int] = {}
        for process in self.processes:
            issues.extend(process.problems())
            if process.id:
                seen[process.id] = seen.get(process.id, 0) + 1
        issues.extend(f"process id '{pid}' appears {n} times" for pid, n in seen.items() if n > 1)
        if not any(p.role == "signal" for p in self.processes):
            issues.append("no process is marked role='signal'")
        return issues

    def validate(self) -> None:
        """Raise `InventoryError` listing every problem, or return silently."""
        issues = self.problems()
        if issues:
            raise InventoryError("; ".join(issues))

    # ----------------------------------------------------------------- views

    def by_role(self, role: str) -> list[Process]:
        return [p for p in self.processes if p.role == role]

    def by_category(self, category: str) -> list[Process]:
        return [p for p in self.processes if p.role == "background" and p.category == category]

    def datasets(self) -> list[Dataset]:
        """Every distinct dataset, first mention wins."""
        seen: dict[str, Dataset] = {}
        for process in self.processes:
            for dataset in process.datasets:
                seen.setdefault(dataset.name, dataset)
        return list(seen.values())

    def summary(self) -> str:
        """One line per process, for a tool result or a prompt injection."""
        if not self.processes:
            return "No processes recorded."
        lines = []
        for process in self.processes:
            what = (
                process.role if process.role != "background" else f"background/{process.category}"
            )
            names = ", ".join(d.name for d in process.datasets) or "no dataset"
            lines.append(f"  {process.id:<14} {what:<26} {process.importance:<11} {names}")
        return "\n".join(lines)


# --------------------------------------------------------------------- files


def inventory_path(analysis_root: Path | str, node: PlanNode) -> Path:
    """Where the node that owns the inventory keeps it.

    Derived from the node, never from a phase name: a plan is free to rename its
    directories or hand the inventory to a different node.
    """
    return Path(analysis_root) / node.outputs_dir / INVENTORY_FILENAME


def save_inventory(analysis_root: Path | str, node: PlanNode, inventory: ProcessInventory) -> Path:
    """Write the inventory for `node`, stamping it with the node and the time."""
    path = inventory_path(analysis_root, node)
    path.parent.mkdir(parents=True, exist_ok=True)
    stamped = ProcessInventory(
        processes=inventory.processes,
        node_id=node.id,
        updated_at=utc_now(),
        notes=inventory.notes,
    )
    path.write_text(json.dumps(stamped.to_dict(), indent=2) + "\n", encoding="utf-8")
    return path


def load_inventory(analysis_root: Path | str, node: PlanNode) -> ProcessInventory | None:
    """Read the inventory `node` wrote, or None when it has not written one.

    A malformed file is `None` as well: this is read on the request path of the
    progress panel and by downstream prompts, and neither may be taken down by a
    half-written JSON file.
    """
    path = inventory_path(analysis_root, node)
    if not path.is_file():
        return None
    try:
        return ProcessInventory.from_json(path.read_text(encoding="utf-8"))
    except (OSError, InventoryError):
        return None


#: What recording an inventory writes into the graph, and therefore what a plan
#: node's write-back contract must allow before it may own one.
OWNER_NODE_TYPES: tuple[str, ...] = ("process", "dataset")


def inventory_owner(plan: AnalysisPlan) -> PlanNode | None:
    """The node responsible for recording the inventory, or None if no node is.

    Ownership is read off the write-back contract — the node allowed to create
    `process` *and* `dataset` graph nodes is the node that decides what the
    processes are — and never off a node id or a phase number. A plan that hands
    the strategy's job to a differently named node therefore works unchanged, and
    a plan that gives two nodes the allowance gives the job to the earlier one,
    because the inventory is written once and read many times.
    """
    for node in plan.nodes:
        if all(name in node.contract.node_types for name in OWNER_NODE_TYPES):
            return node
    return None


def find_inventory(
    analysis_root: Path | str, plan: AnalysisPlan
) -> tuple[ProcessInventory | None, PlanNode | None]:
    """The inventory this analysis has, wherever in the plan it was written.

    Consumers — the progress panel, a downstream executor — know the analysis,
    not which node happens to own the inventory. Nodes are searched in plan
    order, so the earliest writer wins if a plan ever grows two.
    """
    for node in plan.nodes:
        found = load_inventory(analysis_root, node)
        if found is not None:
            return found, node
    return None, None
