"""JFC artifact read/write tools."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from agents import function_tool
from hepagent.tools.jfc._resolve import resolve_node

_MAX_CHARS = 8000


@function_tool
async def read_phase_artifact(analysis_root: str, node_id: str) -> str:
    """
    Read the primary artifact markdown a node produces.

    Returns the artifact content (truncated to 8000 chars if oversized,
    with a summary header).

    Args:
        analysis_root: Absolute path to the analysis root directory.
        node_id: Plan node id, e.g. "strategy" or "selection_ee". Run
            `graph_query` with question "nodes" if you are unsure.
    """
    node, err = resolve_node(analysis_root, node_id)
    if node is None:
        return err
    path = Path(analysis_root) / node.artifact_path
    if not path.exists():
        return f"Artifact not found: {path}"
    content = path.read_text(encoding="utf-8")
    if len(content) > _MAX_CHARS:
        return (
            f"[Artifact truncated: {len(content)} chars → {_MAX_CHARS} shown]\n\n"
            + content[:_MAX_CHARS]
        )
    return content


@function_tool
async def write_phase_artifact(analysis_root: str, node_id: str, content: str) -> str:
    """
    Write or overwrite the primary artifact for a node.

    Returns the artifact file path on success.

    Args:
        analysis_root: Absolute path to the analysis root directory.
        node_id: Plan node id, e.g. "strategy" or "selection_ee".
        content: The markdown content to write.
    """
    node, err = resolve_node(analysis_root, node_id)
    if node is None:
        return err
    path = Path(analysis_root) / node.artifact_path
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return str(path)
    except OSError as e:
        return f"Error writing artifact: {e}"


@function_tool
async def append_experiment_log(analysis_root: str, entry: str) -> str:
    """
    Append an entry to the experiment_log.md (append-only lab notebook).

    Prepends a timestamp and phase separator automatically.

    Args:
        analysis_root: Absolute path to the analysis root directory.
        entry: The log entry text to append.
    """
    log_path = Path(analysis_root) / "experiment_log.md"
    ts = datetime.now(UTC).isoformat(timespec="seconds")
    formatted = f"\n---\n**{ts}**\n\n{entry.strip()}\n"
    try:
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(formatted)
        return f"Appended to {log_path}"
    except OSError as e:
        return f"Error appending to experiment log: {e}"


@function_tool
async def read_commitments(analysis_root: str) -> str:
    """
    Read COMMITMENTS.md for the analysis — the commitments [D1]-[DN] declared
    up front, which later nodes must resolve or explicitly downscope.

    Args:
        analysis_root: Absolute path to the analysis root directory.
    """
    path = Path(analysis_root) / "COMMITMENTS.md"
    if not path.exists():
        return "COMMITMENTS.md not found. It is created by the commitment-declaring node."
    return path.read_text(encoding="utf-8")


@function_tool
async def update_commitments(
    analysis_root: str,
    commitment_id: str,
    status: Literal["resolved", "downscoped", "pending"],
    evidence: str,
) -> str:
    """
    Mark a declared commitment as resolved or downscoped with evidence.

    Updates the row in COMMITMENTS.md for the given commitment ID.

    Args:
        analysis_root: Absolute path to the analysis root directory.
        commitment_id: Commitment ID, e.g. "D1", "D2".
        status: New status: "resolved", "downscoped", or "pending".
        evidence: Evidence string (file path, chi2 value, etc.) or downscope justification.
    """
    path = Path(analysis_root) / "COMMITMENTS.md"
    if not path.exists():
        return "Error: COMMITMENTS.md not found."

    content = path.read_text(encoding="utf-8")
    # Match the table row for this commitment ID
    pattern = re.compile(
        rf"(\|\s*{re.escape(commitment_id)}\s*\|[^|]*\|)[^|]*\|[^|]*\|([^|]*\|)",
        re.MULTILINE,
    )
    replacement = rf"\1 {status} | {evidence} |"
    new_content, count = pattern.subn(replacement, content)
    if count == 0:
        return f"Error: commitment '{commitment_id}' not found in COMMITMENTS.md."

    try:
        path.write_text(new_content, encoding="utf-8")
        return f"Updated commitment {commitment_id} → {status}"
    except OSError as e:
        return f"Error writing COMMITMENTS.md: {e}"
