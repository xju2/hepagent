"""JFC Phase 1 commitment tracking and verification."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from hepagent.helpers import read_md


@dataclass
class CommitmentStatus:
    id: str
    text: str
    status: Literal["pending", "resolved", "downscoped"]
    evidence: str
    phase_resolved: str


@dataclass
class CommitmentCheckResult:
    all_resolved: bool
    pending: list[CommitmentStatus] = field(default_factory=list)
    resolved: list[CommitmentStatus] = field(default_factory=list)
    downscoped: list[CommitmentStatus] = field(default_factory=list)
    blocking_message: str = ""


class CommitmentsNotResolved(Exception):
    """Raised by orchestrator when pending commitments block Phase 4a."""

    def __init__(self, result: CommitmentCheckResult):
        self.result = result
        super().__init__(result.blocking_message)


def check_phase1_commitments(analysis_root: Path) -> CommitmentCheckResult:
    """
    Parse COMMITMENTS.md and return status of all commitments.

    Called by the orchestrator as a pre-advancement gate before Phase 4a.

    Args:
        analysis_root: Path to the analysis root directory.
    """
    path = analysis_root / "COMMITMENTS.md"
    if not path.exists():
        # No commitments file means Phase 1 didn't create one — treat as no commitments
        return CommitmentCheckResult(all_resolved=True)

    content = read_md(path)

    # Parse markdown table rows: | ID | Commitment | Status | Evidence | Phase Resolved |
    row_pattern = re.compile(
        r"\|\s*([A-Z]\d+)\s*\|([^|]+)\|([^|]+)\|([^|]*)\|([^|]*)\|",
        re.MULTILINE,
    )

    pending: list[CommitmentStatus] = []
    resolved: list[CommitmentStatus] = []
    downscoped: list[CommitmentStatus] = []

    for match in row_pattern.finditer(content):
        cid = match.group(1).strip()
        text = match.group(2).strip()
        status_raw = match.group(3).strip().lower()
        evidence = match.group(4).strip()
        phase_resolved = match.group(5).strip()

        if status_raw in ("resolved", "[x]", "x"):
            status: Literal["pending", "resolved", "downscoped"] = "resolved"
        elif status_raw in ("downscoped", "[d]", "d"):
            status = "downscoped"
        else:
            status = "pending"

        item = CommitmentStatus(
            id=cid,
            text=text,
            status=status,
            evidence=evidence,
            phase_resolved=phase_resolved,
        )
        if status == "resolved":
            resolved.append(item)
        elif status == "downscoped":
            downscoped.append(item)
        else:
            pending.append(item)

    all_resolved = len(pending) == 0

    blocking_message = ""
    if not all_resolved:
        lines = [
            f"Phase 4a blocked: {len(pending)} commitment(s) still pending in COMMITMENTS.md:",
        ]
        for item in pending:
            lines.append(f"  [{item.id}] {item.text}")
        lines.append(
            "\nEach pending commitment must be either resolved (with evidence) or "
            "formally downscoped (with documented attempt + failure reason) before Phase 4a."
        )
        blocking_message = "\n".join(lines)

    return CommitmentCheckResult(
        all_resolved=all_resolved,
        pending=pending,
        resolved=resolved,
        downscoped=downscoped,
        blocking_message=blocking_message,
    )
