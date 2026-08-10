"""Append-only JSONL store for the analysis graph.

Two files live under ``<analysis_root>/graph/``:

    nodes.jsonl   one JSON object per line, one line per node revision
    edges.jsonl   one JSON object per line, one line per edge revision

Records are never rewritten in place. A later line with the same node id (or the
same ``(src, dst, type)`` edge key) supersedes the earlier one, so the file
doubles as a mutation history while staying readable in a git diff. Because the
JFC orchestrator runs ``git add -A`` at every phase boundary, each phase commit
snapshots the graph automatically.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from pathlib import Path

from hepagent.graph.schema import Edge, GraphSchemaError, Node, check_edge_domain

GRAPH_DIRNAME = "graph"
NODES_FILENAME = "nodes.jsonl"
EDGES_FILENAME = "edges.jsonl"

# Reviewers run concurrently under asyncio.gather and each may append. A single
# process-wide lock plus one write() per record keeps the log well-formed.
_WRITE_LOCK = threading.Lock()

_README = """# Analysis graph

Append-only ledger of this analysis, written by `hepagent`.

- `nodes.jsonl` — durable analysis objects (artifacts, figures, commitments,
  evidence, reviews, decisions, ...). One JSON object per line.
- `edges.jsonl` — typed relationships between them (`derives_from`, `supports`,
  `resolves`, `invalidates`, ...). One JSON object per line.

Records are never edited in place. A later line with the same node `id` — or the
same `(src, dst, type)` edge triple — supersedes the earlier one, so the files
keep their own history. Both are safe to read with `jq`.

Rebuild the deterministic parts from the artifacts on disk at any time:

    hepagent jfc graph rebuild --name <analysis>

See `docs/GRAPH.md` in the hepagent repository for the full schema.
"""


class AnalysisGraph:
    """In-memory view of the graph for one analysis, backed by JSONL on disk.

    Args:
        root: The analysis root directory (the parent of ``graph/``).
    """

    def __init__(self, root: Path | str):
        self.root = Path(root)
        self._nodes: dict[str, Node] = {}
        self._edges: dict[tuple[str, str, str], Edge] = {}

    # ---------------------------------------------------------------- paths

    @property
    def graph_dir(self) -> Path:
        return self.root / GRAPH_DIRNAME

    @property
    def nodes_path(self) -> Path:
        return self.graph_dir / NODES_FILENAME

    @property
    def edges_path(self) -> Path:
        return self.graph_dir / EDGES_FILENAME

    # ----------------------------------------------------------- lifecycle

    @classmethod
    def load(cls, root: Path | str) -> AnalysisGraph:
        """Read the graph for `root` from disk. Missing files yield an empty graph."""
        graph = cls(root)
        graph._read_nodes()
        graph._read_edges()
        return graph

    def ensure_dir(self) -> Path:
        """Create ``graph/`` with its README if not already present."""
        self.graph_dir.mkdir(parents=True, exist_ok=True)
        readme = self.graph_dir / "README.md"
        if not readme.exists():
            readme.write_text(_README, encoding="utf-8")
        return self.graph_dir

    def _read_nodes(self) -> None:
        for record in _iter_jsonl(self.nodes_path):
            try:
                node = Node.from_dict(record)
            except (GraphSchemaError, TypeError):
                continue  # skip malformed lines rather than failing the whole load
            self._nodes[node.id] = node

    def _read_edges(self) -> None:
        for record in _iter_jsonl(self.edges_path):
            try:
                edge = Edge.from_dict(record)
            except (GraphSchemaError, TypeError):
                continue
            self._edges[edge.key] = edge

    # --------------------------------------------------------------- write

    def add_node(self, node: Node) -> Node:
        """Append `node`, superseding any earlier record with the same id."""
        existing = self._nodes.get(node.id)
        if existing is not None and _same_node_content(existing, node):
            return existing  # idempotent: nothing changed, nothing to append
        self._nodes[node.id] = node
        self._append(self.nodes_path, node.to_dict())
        return node

    def add_edge(self, edge: Edge) -> Edge:
        """Append `edge` after validating its endpoints and type domain.

        Raises:
            GraphSchemaError: if either endpoint is missing from the graph or
                the edge type may not connect those two node types.
        """
        src = self._nodes.get(edge.src)
        dst = self._nodes.get(edge.dst)
        if src is None:
            raise GraphSchemaError(f"Edge source node '{edge.src}' does not exist in the graph")
        if dst is None:
            raise GraphSchemaError(f"Edge target node '{edge.dst}' does not exist in the graph")

        domain_error = check_edge_domain(edge, src.type, dst.type)
        if domain_error:
            raise GraphSchemaError(domain_error)

        existing = self._edges.get(edge.key)
        if existing is not None and existing.evidence_ref == edge.evidence_ref:
            return existing
        self._edges[edge.key] = edge
        self._append(self.edges_path, edge.to_dict())
        return edge

    def _append(self, path: Path, record: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(record, sort_keys=True, ensure_ascii=False) + "\n"
        with _WRITE_LOCK, open(path, "a", encoding="utf-8") as handle:
            handle.write(line)

    # ---------------------------------------------------------------- read

    def get_node(self, node_id: str) -> Node | None:
        return self._nodes.get(node_id)

    def has_node(self, node_id: str) -> bool:
        return node_id in self._nodes

    def nodes(
        self,
        type: str | None = None,
        phase: str | None = None,
        status: str | None = None,
    ) -> list[Node]:
        """Return nodes matching every supplied filter, ordered by id."""
        result = [
            node
            for node in self._nodes.values()
            if (type is None or node.type == type)
            and (phase is None or node.phase == phase)
            and (status is None or node.status == status)
        ]
        return sorted(result, key=lambda n: n.id)

    def edges(
        self,
        src: str | None = None,
        dst: str | None = None,
        type: str | None = None,
    ) -> list[Edge]:
        """Return edges matching every supplied filter, ordered by key."""
        result = [
            edge
            for edge in self._edges.values()
            if (src is None or edge.src == src)
            and (dst is None or edge.dst == dst)
            and (type is None or edge.type == type)
        ]
        return sorted(result, key=lambda e: e.key)

    def out_edges(self, node_id: str, type: str | None = None) -> list[Edge]:
        return self.edges(src=node_id, type=type)

    def in_edges(self, node_id: str, type: str | None = None) -> list[Edge]:
        return self.edges(dst=node_id, type=type)

    def find_by_content_ref(self, content_ref: str) -> list[Node]:
        """Return nodes whose `content_ref` matches, exactly or by suffix.

        Suffix matching lets a caller pass an absolute path or a bare filename
        and still find the node recorded with an analysis-root-relative path.
        """
        needle = str(content_ref).strip()
        if not needle:
            return []
        exact = [n for n in self._nodes.values() if n.content_ref == needle]
        if exact:
            return sorted(exact, key=lambda n: n.id)
        suffix = [
            n
            for n in self._nodes.values()
            if n.content_ref and (n.content_ref.endswith(needle) or needle.endswith(n.content_ref))
        ]
        return sorted(suffix, key=lambda n: n.id)

    def node_history(self) -> Iterator[dict]:
        """Yield every node record ever appended, in file order.

        Superseded revisions are included — this is what makes "was anything
        silently deleted?" answerable.
        """
        yield from _iter_jsonl(self.nodes_path)

    def __len__(self) -> int:
        return len(self._nodes)

    def summary(self) -> dict[str, int]:
        """Return a `{node_type: count}` mapping plus a total edge count."""
        counts: dict[str, int] = {}
        for node in self._nodes.values():
            counts[node.type] = counts.get(node.type, 0) + 1
        counts["_edges"] = len(self._edges)
        return counts


def _iter_jsonl(path: Path) -> Iterator[dict]:
    """Yield parsed JSON objects from a JSONL file, skipping unreadable lines."""
    if not path.exists():
        return
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(record, dict):
                yield record


def _same_node_content(a: Node, b: Node) -> bool:
    """True when two node revisions carry identical payloads (timestamps aside)."""
    return (
        a.type == b.type
        and a.label == b.label
        and a.content_ref == b.content_ref
        and a.metadata == b.metadata
        and a.status == b.status
        and a.phase == b.phase
    )
