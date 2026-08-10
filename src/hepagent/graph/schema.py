"""Node/edge vocabulary and record types for the analysis graph.

The graph is the durable ledger of an analysis: what was produced, what it was
derived from, what evidence supports it, and what a reviewer decided about it.
Node identifiers are deterministic and content-addressed wherever a filesystem
path exists, so re-ingesting an unchanged analysis directory reproduces exactly
the same logical graph.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal, get_args

NodeType = Literal[
    "problem",
    "analysis_root",
    "commitment",
    "dataset",
    "method",
    "artifact",
    "figure",
    "evidence",
    "review",
    "decision",
    "execution",
]

EdgeType = Literal[
    "requires",
    "derives_from",
    "supports",
    "invalidates",
    "commits_to",
    "resolves",
    "downscopes",
    "reviewed_by",
    "approved_by",
    "regresses_to",
]

NODE_TYPES: tuple[str, ...] = get_args(NodeType)
EDGE_TYPES: tuple[str, ...] = get_args(EdgeType)

# Node statuses. "active" is the default; the others let the append-only log
# record supersession without deleting history.
NodeStatus = Literal["active", "superseded", "pending", "resolved", "downscoped"]
NODE_STATUSES: tuple[str, ...] = get_args(NodeStatus)

# Which (source type, destination type) pairs each edge type may connect.
# An empty source or destination set means "any node type".
_ANY: frozenset[str] = frozenset(NODE_TYPES)

EDGE_DOMAIN: dict[str, tuple[frozenset[str], frozenset[str]]] = {
    # A depends on B being available first.
    "requires": (_ANY, _ANY),
    # A was produced from B (lineage / provenance).
    "derives_from": (_ANY, _ANY),
    # Evidence A backs claim/artifact/decision B.
    "supports": (
        frozenset({"evidence", "figure", "execution", "artifact"}),
        frozenset({"artifact", "method", "decision", "commitment", "figure", "evidence"}),
    ),
    # Review/decision A rejects B.
    "invalidates": (
        frozenset({"review", "decision", "evidence"}),
        _ANY,
    ),
    # A phase artifact commits to doing B downstream.
    "commits_to": (
        frozenset({"artifact", "problem", "analysis_root"}),
        frozenset({"commitment"}),
    ),
    # Evidence B closes commitment A.
    "resolves": (
        frozenset({"commitment"}),
        frozenset({"evidence", "artifact", "figure", "method", "execution"}),
    ),
    # Commitment A was formally narrowed, justified by B.
    "downscopes": (
        frozenset({"commitment"}),
        frozenset({"evidence", "artifact", "review", "decision"}),
    ),
    # Artifact A was examined by review B.
    "reviewed_by": (
        frozenset({"artifact", "figure", "evidence"}),
        frozenset({"review", "decision"}),
    ),
    # Artifact A was cleared by decision B.
    "approved_by": (
        frozenset({"artifact", "figure", "evidence", "commitment"}),
        frozenset({"decision", "review"}),
    ),
    # A finding at A traces back to earlier work B.
    "regresses_to": (
        frozenset({"review", "decision", "evidence"}),
        _ANY,
    ),
}

# Documented metadata keys for ATLAS-specific structure. These are conventions,
# not enforced schema — `Node.metadata` accepts any JSON-serialisable mapping —
# but builders and reviewers should prefer these names so queries stay portable.
ATLAS_METADATA_KEYS: tuple[str, ...] = (
    "ami_tag",
    "campaign",
    "derivation",
    "lumi_fb",
    "reco_tag",
    "trigger",
    "region",
    "syst_source",
    "np_name",
    "generator",
)


class GraphSchemaError(ValueError):
    """Raised when a node or edge violates the graph contract."""


def utc_now() -> str:
    """Return the current UTC time as an ISO-8601 string (seconds resolution)."""
    return datetime.now(UTC).isoformat(timespec="seconds")


_SLUG_UNSAFE = re.compile(r"[^A-Za-z0-9._/-]+")


def slugify(value: str) -> str:
    """Normalise an arbitrary string into an identifier-safe slug.

    Path separators are preserved so a content-addressed id stays readable as
    the relative path it came from.
    """
    cleaned = _SLUG_UNSAFE.sub("-", value.strip()).strip("-")
    return cleaned or "unnamed"


def make_id(node_type: str, key: str) -> str:
    """Build a deterministic node id of the form ``<type>:<slug>``.

    Two builder runs over the same analysis directory must produce identical
    ids; that is what makes re-ingestion idempotent.
    """
    if node_type not in NODE_TYPES:
        raise GraphSchemaError(f"Unknown node type '{node_type}'. Valid: {', '.join(NODE_TYPES)}")
    return f"{node_type}:{slugify(key)}"


@dataclass(frozen=True)
class Node:
    """A durable analysis object.

    Args:
        id: Deterministic identifier, conventionally built with `make_id`.
        type: One of `NODE_TYPES`.
        label: Short human-readable name shown in listings and diagrams.
        content_ref: Path to the backing file, relative to the analysis root.
        metadata: Free-form structured detail (see `ATLAS_METADATA_KEYS`).
        status: Lifecycle state; see `NODE_STATUSES`.
        phase: Phase identifier that created the node ("1", "4a", ...).
        created_by: Agent name or "builder" for deterministic ingestion.
        created_at: ISO-8601 UTC timestamp.
    """

    id: str
    type: str
    label: str
    content_ref: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    status: str = "active"
    phase: str | None = None
    created_by: str = "builder"
    created_at: str = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        if self.type not in NODE_TYPES:
            raise GraphSchemaError(
                f"Unknown node type '{self.type}'. Valid: {', '.join(NODE_TYPES)}"
            )
        if self.status not in NODE_STATUSES:
            raise GraphSchemaError(
                f"Unknown node status '{self.status}'. Valid: {', '.join(NODE_STATUSES)}"
            )
        if not self.id:
            raise GraphSchemaError("Node id must be non-empty")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Node:
        known = set(cls.__dataclass_fields__)
        return cls(**{k: v for k, v in data.items() if k in known})


@dataclass(frozen=True)
class Edge:
    """A typed, directed relationship between two nodes.

    Args:
        src: Source node id.
        dst: Destination node id.
        type: One of `EDGE_TYPES`.
        evidence_ref: Path or locator backing this relationship.
        created_by: Agent name or "builder".
        phase: Phase identifier that created the edge.
        created_at: ISO-8601 UTC timestamp.
    """

    src: str
    dst: str
    type: str
    evidence_ref: str | None = None
    created_by: str = "builder"
    phase: str | None = None
    created_at: str = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        if self.type not in EDGE_TYPES:
            raise GraphSchemaError(
                f"Unknown edge type '{self.type}'. Valid: {', '.join(EDGE_TYPES)}"
            )
        if not self.src or not self.dst:
            raise GraphSchemaError("Edge src and dst must both be non-empty")
        if self.src == self.dst:
            raise GraphSchemaError(f"Self-edges are not allowed (node '{self.src}')")

    @property
    def key(self) -> tuple[str, str, str]:
        """Identity of this edge: two edges with the same key supersede."""
        return (self.src, self.dst, self.type)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Edge:
        known = set(cls.__dataclass_fields__)
        return cls(**{k: v for k, v in data.items() if k in known})


def check_edge_domain(edge: Edge, src_type: str, dst_type: str) -> str:
    """Return an error message if `edge` may not connect these node types.

    Returns an empty string when the edge is legal.
    """
    allowed = EDGE_DOMAIN.get(edge.type)
    if allowed is None:
        return f"Unknown edge type '{edge.type}'"
    src_ok, dst_ok = allowed
    if src_type not in src_ok:
        return (
            f"Edge '{edge.type}' cannot start at a '{src_type}' node "
            f"(allowed: {', '.join(sorted(src_ok))})"
        )
    if dst_type not in dst_ok:
        return (
            f"Edge '{edge.type}' cannot end at a '{dst_type}' node "
            f"(allowed: {', '.join(sorted(dst_ok))})"
        )
    return ""
