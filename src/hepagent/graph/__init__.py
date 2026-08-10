"""Analysis graph: durable, queryable provenance for an agent-run analysis.

Nodes are analysis objects (artifacts, figures, commitments, evidence, reviews,
decisions); edges are typed relationships between them. The canonical store is
append-only JSONL under ``<analysis_root>/graph/``.

See `docs/GRAPH.md` for the schema and the agent write-back contract.
"""

from hepagent.graph.query import (
    ancestors,
    descendants,
    describe,
    evidence_for,
    frontier,
    is_realized,
    orphan_claims,
    rejections,
    relative_to_root,
    reviews_of,
    to_mermaid,
    to_table,
    unresolved_commitments,
    what_produced,
)
from hepagent.graph.schema import (
    ATLAS_METADATA_KEYS,
    EDGE_DOMAIN,
    EDGE_TYPES,
    NODE_STATUSES,
    NODE_TYPES,
    Edge,
    GraphSchemaError,
    Node,
    check_edge_domain,
    make_id,
    slugify,
    utc_now,
)
from hepagent.graph.store import AnalysisGraph
from hepagent.graph.validation import (
    GraphFinding,
    GraphValidationReport,
    validate,
    validate_commitments,
)

__all__ = [
    "ATLAS_METADATA_KEYS",
    "EDGE_DOMAIN",
    "EDGE_TYPES",
    "NODE_STATUSES",
    "NODE_TYPES",
    "AnalysisGraph",
    "Edge",
    "GraphFinding",
    "GraphSchemaError",
    "GraphValidationReport",
    "Node",
    "ancestors",
    "check_edge_domain",
    "descendants",
    "describe",
    "evidence_for",
    "frontier",
    "is_realized",
    "make_id",
    "orphan_claims",
    "rejections",
    "relative_to_root",
    "reviews_of",
    "slugify",
    "to_mermaid",
    "to_table",
    "unresolved_commitments",
    "utc_now",
    "validate",
    "validate_commitments",
    "what_produced",
]
