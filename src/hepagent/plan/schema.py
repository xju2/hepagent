"""Record types for the analysis plan.

A plan is a small, readable JSON document: a list of work nodes and a list of
dependency edges. Everything the runtime needs to execute a node — the working
directory, the primary artifact, the prompt, the reviewer set, the gate, the
graph write-back contract — lives on the node, so adding a node to the plan is
the whole of adding a step to the analysis.

Two deliberate choices are worth stating up front.

**Edge direction.** :class:`PlanEdge` names its endpoints ``upstream`` and
``downstream``, never ``src``/``dst``. A plan edge reads in data-flow order
(*strategy* produces what *exploration* consumes), whereas
:class:`hepagent.graph.schema.Edge` with type ``requires`` reads in dependency
order and puts the *dependent* node in ``src``. Naming them differently makes
that inversion impossible to write by accident; :mod:`hepagent.plan.compile` is
the single place it happens.

**Author-stable ids.** Node ids are chosen by whoever writes the plan and never
derived from a label or a path, so renaming a node's label does not orphan its
edges.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any

#: Bumped when a change to this module cannot be read by the previous version.
PLAN_SCHEMA_VERSION = 1

#: What a node *is*. ``work`` runs an agent; ``gate`` only interposes a check.
#: The discriminator exists so fully general agent nodes stay an additive change.
NODE_KINDS: tuple[str, ...] = ("work", "gate")

#: Which agent factory owns a node. ``note_writer``/``typesetter`` normally run
#: as sub-steps of a ``produces_note`` node rather than as nodes of their own.
ROLES: tuple[str, ...] = ("executor", "note_writer", "typesetter")

#: Interposed checks a node may carry. ``commitments`` blocks until every
#: commitment is closed; ``human`` asks a person to approve; ``codesign`` runs the
#: interactive strategy review.
GATES: tuple[str, ...] = ("commitments", "human", "codesign")

#: When a gate runs relative to its node. The three gates the JFC pipeline grew
#: are not all of one kind — the commitment check blocks a node from starting,
#: while the codesign and human reviews judge what it produced — so position is
#: declared rather than implied by the gate's name.
GATE_TIMINGS: tuple[str, ...] = ("before", "after")

#: ``requires`` is blocking and orders execution. ``informs`` injects the upstream
#: artifact when it happens to exist but never delays the downstream node.
EDGE_KINDS: tuple[str, ...] = ("requires", "informs")

#: How much of an upstream artifact enters the downstream prompt.
INJECT_MODES: tuple[str, ...] = ("full", "summary", "none")

_ID_RE = re.compile(r"^[a-z][a-z0-9_-]*$")


class PlanSchemaError(ValueError):
    """Raised when a plan record violates the schema."""


def utc_now() -> str:
    """Return the current UTC time as an ISO-8601 string (seconds resolution)."""
    return datetime.now(UTC).isoformat(timespec="seconds")


def _known(data: dict[str, Any], cls: type) -> dict[str, Any]:
    """Drop keys `cls` does not declare, so a newer file loads in an older build."""
    fields = set(cls.__dataclass_fields__)
    return {k: v for k, v in data.items() if k in fields}


@dataclass(frozen=True)
class PlanGate:
    """A check interposed around a node.

    Args:
        name: See `GATES`.
        when: ``"before"`` blocks the node from starting; ``"after"`` judges what
            it produced. See `GATE_TIMINGS`.
        enabled: Whether the gate runs. Ships ``False`` for gates a CLI flag
            turns on, so the flag flips data rather than branching on a node id.
    """

    name: str
    when: str = "before"
    enabled: bool = True

    def __post_init__(self) -> None:
        if self.when not in GATE_TIMINGS:
            raise PlanSchemaError(
                f"Unknown gate timing '{self.when}'. Valid: {', '.join(GATE_TIMINGS)}"
            )

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "when": self.when, "enabled": self.enabled}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PlanGate:
        return cls(**_known(data, cls))


@dataclass(frozen=True)
class PlanContract:
    """What a node may write back into the provenance graph.

    Mirrors the per-phase allowlist that used to live in ``PHASE_CONTRACTS``. An
    empty contract means the node may read the graph but not write to it.

    Args:
        node_types: Graph node types this node may create.
        edge_types: Graph edge types this node may create.
    """

    node_types: tuple[str, ...] = ()
    edge_types: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {"node_types": list(self.node_types), "edge_types": list(self.edge_types)}

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> PlanContract:
        if not data:
            return cls()
        return cls(
            node_types=tuple(data.get("node_types") or ()),
            edge_types=tuple(data.get("edge_types") or ()),
        )


@dataclass(frozen=True)
class PlanNode:
    """One unit of work in the analysis.

    Args:
        id: Author-chosen stable slug, e.g. ``"strategy"`` or ``"selection_ee"``.
        label: Display name shown in the editor and in listings.
        directory: Working directory, relative to the analysis root.
        artifact: Primary output filename, written to ``<directory>/outputs/``.
        note_artifact: Filename of the analysis note this node writes, when that
            is a *different* document from its primary artifact. Empty means the
            note and the artifact are the same file. Only meaningful alongside
            `produces_note`.
        prompt: The markdown specification this node's executor runs. Editable —
            this is the text a user changes to change what the node does.
        kind: See `NODE_KINDS`.
        role: See `ROLES`.
        context_paths: Ambient analysis-root-relative files to put in the prompt
            alongside the upstream artifacts, e.g. ``["COMMITMENTS.md"]``. Unlike
            an upstream artifact these carry no dependency — they are read if
            present and never delay the node.
        reviewers: Reviewer names the review gate runs for this node.
        arbiter: Whether an arbiter adjudicates this node's reviews.
        produces_note: Whether the node runs the note writer and typesetter.
        gates: Checks interposed around the node, see `PlanGate`.
        contract: The node's graph write-back allowance.
        max_iterations: Review iterations allowed before escalating.
        model: Optional ``"provider:model"`` override for this node.
        tools: Optional tool-name allowlist. ``None`` means the default set for
            `role`.
        metadata: Free-form detail. The editor stores layout coordinates here
            under ``"x"`` and ``"y"``.
    """

    id: str
    label: str
    directory: str
    artifact: str
    note_artifact: str = ""
    prompt: str = ""
    kind: str = "work"
    role: str = "executor"
    context_paths: tuple[str, ...] = ()
    reviewers: tuple[str, ...] = ()
    arbiter: bool = False
    produces_note: bool = False
    gates: tuple[PlanGate, ...] = ()
    contract: PlanContract = field(default_factory=PlanContract)
    max_iterations: int = 3
    model: str | None = None
    tools: tuple[str, ...] | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.id:
            raise PlanSchemaError("Node id must be non-empty")
        if not _ID_RE.match(self.id):
            raise PlanSchemaError(
                f"Node id '{self.id}' must start with a letter and contain only "
                f"lowercase letters, digits, '_' and '-'"
            )
        if self.kind not in NODE_KINDS:
            raise PlanSchemaError(
                f"Unknown node kind '{self.kind}'. Valid: {', '.join(NODE_KINDS)}"
            )

    @property
    def artifact_path(self) -> str:
        """The node's primary artifact, relative to the analysis root."""
        return f"{self.directory}/outputs/{self.artifact}"

    @property
    def note_path(self) -> str:
        """The analysis note this node writes, relative to the analysis root.

        Falls back to the primary artifact, which is the right answer for a node
        whose artifact *is* the note.
        """
        return f"{self.directory}/outputs/{self.note_artifact or self.artifact}"

    @property
    def note_pdf_path(self) -> str:
        """The compiled PDF of this node's analysis note."""
        return self.note_path.removesuffix(".md") + ".pdf"

    @property
    def outputs_dir(self) -> str:
        """The node's outputs directory, relative to the analysis root."""
        return f"{self.directory}/outputs"

    def gates_at(self, when: str) -> tuple[PlanGate, ...]:
        """Enabled gates that run at this position, in declaration order."""
        return tuple(g for g in self.gates if g.when == when and g.enabled)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["contract"] = self.contract.to_dict()
        data["gates"] = [gate.to_dict() for gate in self.gates]
        data["context_paths"] = list(self.context_paths)
        data["reviewers"] = list(self.reviewers)
        data["tools"] = None if self.tools is None else list(self.tools)
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PlanNode:
        payload = _known(data, cls)
        payload["contract"] = PlanContract.from_dict(payload.get("contract"))
        payload["gates"] = tuple(PlanGate.from_dict(g) for g in payload.get("gates") or ())
        payload["context_paths"] = tuple(payload.get("context_paths") or ())
        payload["reviewers"] = tuple(payload.get("reviewers") or ())
        tools = payload.get("tools")
        payload["tools"] = None if tools is None else tuple(tools)
        return cls(**payload)


@dataclass(frozen=True)
class PlanEdge:
    """A dependency between two nodes, in data-flow order.

    Args:
        upstream: Id of the node that produces.
        downstream: Id of the node that consumes.
        kind: See `EDGE_KINDS`. Only ``requires`` orders execution.
        inject: How much of the upstream artifact enters the downstream prompt,
            see `INJECT_MODES`. Ambient files that belong to the node rather than
            to one dependency go in `PlanNode.context_paths`.
        metadata: Free-form detail.
    """

    upstream: str
    downstream: str
    kind: str = "requires"
    inject: str = "full"
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.upstream or not self.downstream:
            raise PlanSchemaError("Edge upstream and downstream must both be non-empty")
        if self.upstream == self.downstream:
            raise PlanSchemaError(f"Self-edges are not allowed (node '{self.upstream}')")

    @property
    def key(self) -> tuple[str, str, str]:
        """Identity of this edge: two edges with the same key are duplicates."""
        return (self.upstream, self.downstream, self.kind)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PlanEdge:
        return cls(**_known(data, cls))


@dataclass(frozen=True)
class AnalysisPlan:
    """The authored structure of one analysis.

    Args:
        name: Short analysis identifier, matching the directory name.
        analysis_type: ``"measurement"`` or ``"search"``.
        nodes: The units of work, in declaration order. Declaration order breaks
            ties between nodes that become runnable at the same moment.
        edges: Dependencies between them.
        template: Name of the template this plan started from.
        problem: The physics question, mirroring ``prompt.md``.
        schema_version: See `PLAN_SCHEMA_VERSION`.
        revision: Incremented by every save; 0 means never saved.
        created_at: ISO-8601 UTC timestamp.
        updated_at: ISO-8601 UTC timestamp of the last save.
        metadata: Free-form detail.
    """

    name: str
    analysis_type: str
    nodes: tuple[PlanNode, ...] = ()
    edges: tuple[PlanEdge, ...] = ()
    template: str = ""
    problem: str = ""
    schema_version: int = PLAN_SCHEMA_VERSION
    revision: int = 0
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.name:
            raise PlanSchemaError("Plan name must be non-empty")

    # ------------------------------------------------------------------ lookup

    def node(self, node_id: str) -> PlanNode | None:
        """Return the node with this id, or None."""
        for node in self.nodes:
            if node.id == node_id:
                return node
        return None

    def require_node(self, node_id: str) -> PlanNode:
        """Return the node with this id, raising if the plan does not declare it."""
        node = self.node(node_id)
        if node is None:
            raise PlanSchemaError(f"Plan '{self.name}' has no node '{node_id}'")
        return node

    def node_ids(self) -> tuple[str, ...]:
        """Node ids in declaration order."""
        return tuple(node.id for node in self.nodes)

    def upstream_edges(self, node_id: str, kind: str | None = None) -> list[PlanEdge]:
        """Edges feeding `node_id`, in declaration order."""
        return [
            edge
            for edge in self.edges
            if edge.downstream == node_id and (kind is None or edge.kind == kind)
        ]

    def downstream_edges(self, node_id: str, kind: str | None = None) -> list[PlanEdge]:
        """Edges fed by `node_id`, in declaration order."""
        return [
            edge
            for edge in self.edges
            if edge.upstream == node_id and (kind is None or edge.kind == kind)
        ]

    def prerequisites(self, node_id: str) -> list[str]:
        """Node ids that must complete before `node_id` may run."""
        return [edge.upstream for edge in self.upstream_edges(node_id, kind="requires")]

    def entry_nodes(self) -> list[PlanNode]:
        """Nodes with no blocking prerequisite — where an analysis starts."""
        return [node for node in self.nodes if not self.prerequisites(node.id)]

    # --------------------------------------------------------- serialisation

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "name": self.name,
            "analysis_type": self.analysis_type,
            "template": self.template,
            "problem": self.problem,
            "revision": self.revision,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "metadata": dict(self.metadata),
            "nodes": [node.to_dict() for node in self.nodes],
            "edges": [edge.to_dict() for edge in self.edges],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AnalysisPlan:
        payload = _known(data, cls)
        payload["nodes"] = tuple(PlanNode.from_dict(n) for n in data.get("nodes") or ())
        payload["edges"] = tuple(PlanEdge.from_dict(e) for e in data.get("edges") or ())
        return cls(**payload)
