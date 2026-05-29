"""JFC artifact read/write tools."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from agents import function_tool

_ARTIFACT_MAP = {
    "1": ("phase1_strategy", "STRATEGY.md"),
    "2": ("phase2_exploration", "EXPLORATION.md"),
    "3": ("phase3_selection", "SELECTION.md"),
    "4a": ("phase4a_inference_expected", "INFERENCE_EXPECTED.md"),
    "4b": ("phase4b_inference_partial", "INFERENCE_PARTIAL.md"),
    "4c": ("phase4c_inference_observed", "INFERENCE_OBSERVED.md"),
    "5": ("phase5_documentation", "ANALYSIS_NOTE_5_v1.md"),
}

_MAX_CHARS = 8000


def _phase_artifact_path(analysis_root: str, phase: str) -> tuple[Path, str] | tuple[None, str]:
    if phase not in _ARTIFACT_MAP:
        return None, f"Unknown phase '{phase}'. Valid values: {', '.join(_ARTIFACT_MAP)}"
    phase_dir, artifact_name = _ARTIFACT_MAP[phase]
    path = Path(analysis_root) / phase_dir / "outputs" / artifact_name
    return path, ""


@function_tool
async def read_phase_artifact(analysis_root: str, phase: str) -> str:
    """
    Read the primary artifact markdown for a completed phase.

    Returns the artifact content (truncated to 8000 chars if oversized,
    with a summary header).

    Args:
        analysis_root: Absolute path to the analysis root directory.
        phase: Phase identifier: "1", "2", "3", "4a", "4b", "4c", or "5".
    """
    path, err = _phase_artifact_path(analysis_root, phase)
    if path is None:
        return f"Error: {err}"
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
async def write_phase_artifact(analysis_root: str, phase: str, content: str) -> str:
    """
    Write or overwrite the primary artifact for a phase.

    Returns the artifact file path on success.

    Args:
        analysis_root: Absolute path to the analysis root directory.
        phase: Phase identifier: "1", "2", "3", "4a", "4b", "4c", or "5".
        content: The markdown content to write.
    """
    path, err = _phase_artifact_path(analysis_root, phase)
    if path is None:
        return f"Error: {err}"
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
    Read COMMITMENTS.md for the analysis (Phase 1 commitments [D1]-[DN]).

    Args:
        analysis_root: Absolute path to the analysis root directory.
    """
    path = Path(analysis_root) / "COMMITMENTS.md"
    if not path.exists():
        return "COMMITMENTS.md not found. It is created by the Phase 1 executor."
    return path.read_text(encoding="utf-8")


@function_tool
async def update_commitments(
    analysis_root: str,
    commitment_id: str,
    status: Literal["resolved", "downscoped", "pending"],
    evidence: str,
) -> str:
    """
    Mark a Phase 1 commitment as resolved or downscoped with evidence.

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
