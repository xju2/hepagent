"""Reading and writing `<analysis_root>/plan.json`.

Unlike the provenance graph, the plan is edited **wholesale**: a user adds a
node, retypes an edge, rewrites a prompt, and saves. Append-only semantics would
fight that — deleting a node would need a tombstone and every reader would have
to replay the log to know the current shape.

So the plan is a single JSON document rewritten in place, and history is kept
beside it: every save copies the outgoing version to
``plan.history/<revision>.json`` before overwriting. Nothing is ever lost, the
current shape is one `json.load` away, and both files stay git-diffable and ride
the existing per-phase commits.
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path

from hepagent.plan.schema import AnalysisPlan, PlanSchemaError, utc_now

PLAN_FILENAME = "plan.json"
HISTORY_DIRNAME = "plan.history"


class PlanNotFoundError(FileNotFoundError):
    """Raised when an analysis directory carries no plan."""


class PlanFormatError(PlanSchemaError):
    """Raised when `plan.json` exists but cannot be read as a plan."""


def plan_path(analysis_root: Path | str) -> Path:
    """Return the path to the plan document for this analysis."""
    return Path(analysis_root) / PLAN_FILENAME


def history_dir(analysis_root: Path | str) -> Path:
    """Return the directory holding superseded plan revisions."""
    return Path(analysis_root) / HISTORY_DIRNAME


def has_plan(analysis_root: Path | str) -> bool:
    """True when this analysis directory carries a plan document."""
    return plan_path(analysis_root).is_file()


def load_plan(analysis_root: Path | str) -> AnalysisPlan:
    """Read the plan for `analysis_root`.

    Raises:
        PlanNotFoundError: if the directory has no `plan.json`.
        PlanFormatError: if the file is not readable as a plan.
    """
    path = plan_path(analysis_root)
    if not path.is_file():
        raise PlanNotFoundError(f"No plan at {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PlanFormatError(f"Could not read {path}: {exc}") from exc
    return plan_from_dict(data, source=str(path))


def resolve_plan(
    analysis_root: Path | str,
    plan: AnalysisPlan | None = None,
) -> AnalysisPlan:
    """Return `plan`, or read the analysis's own plan from disk.

    Every runtime entry point accepts an already-loaded plan so an orchestrated
    run reads `plan.json` once, while a one-off call from the CLI or an agent
    tool can just name the directory.
    """
    return plan if plan is not None else load_plan(analysis_root)


def plan_from_dict(data: object, *, source: str = "plan") -> AnalysisPlan:
    """Build a plan from a decoded JSON payload, reporting problems as PlanFormatError."""
    if not isinstance(data, dict):
        raise PlanFormatError(f"{source} must contain a JSON object")
    try:
        return AnalysisPlan.from_dict(data)
    except PlanSchemaError:
        raise
    except (TypeError, ValueError, AttributeError) as exc:
        raise PlanFormatError(f"Could not read {source} as a plan: {exc}") from exc


def save_plan(
    analysis_root: Path | str,
    plan: AnalysisPlan,
    *,
    snapshot: bool = True,
) -> AnalysisPlan:
    """Write `plan`, bumping its revision and archiving the version it replaces.

    Args:
        analysis_root: The analysis root directory.
        plan: The plan to write. Its `revision` and `updated_at` are ignored; the
            stored revision is one past whatever is currently on disk.
        snapshot: Copy the outgoing document into `plan.history/` first. Only
            turn this off for a first write in a throwaway directory.

    Returns:
        The plan as written, with `revision` and `updated_at` set.
    """
    root = Path(analysis_root)
    root.mkdir(parents=True, exist_ok=True)
    path = plan_path(root)

    current_revision = 0
    if path.is_file():
        try:
            current_revision = int(json.loads(path.read_text(encoding="utf-8")).get("revision", 0))
        except (OSError, json.JSONDecodeError, TypeError, ValueError, AttributeError):
            # An unreadable document still deserves to be archived rather than
            # silently overwritten, so keep going from revision 0.
            current_revision = 0
        if snapshot:
            _archive(root, path, current_revision)

    written = dataclasses.replace(plan, revision=current_revision + 1, updated_at=utc_now())
    path.write_text(
        json.dumps(written.to_dict(), indent=2, ensure_ascii=False, sort_keys=False) + "\n",
        encoding="utf-8",
    )
    return written


def list_revisions(analysis_root: Path | str) -> list[int]:
    """Return archived revision numbers, oldest first."""
    directory = history_dir(analysis_root)
    if not directory.is_dir():
        return []
    revisions = []
    for entry in directory.glob("*.json"):
        try:
            revisions.append(int(entry.stem))
        except ValueError:
            continue
    return sorted(revisions)


def load_revision(analysis_root: Path | str, revision: int) -> AnalysisPlan:
    """Read an archived plan revision.

    Raises:
        PlanNotFoundError: if that revision was never archived.
    """
    path = history_dir(analysis_root) / f"{revision:04d}.json"
    if not path.is_file():
        raise PlanNotFoundError(f"No archived plan revision {revision} at {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PlanFormatError(f"Could not read {path}: {exc}") from exc
    return plan_from_dict(data, source=str(path))


def _archive(root: Path, path: Path, revision: int) -> None:
    """Copy the current plan document into `plan.history/` under its revision."""
    directory = history_dir(root)
    directory.mkdir(parents=True, exist_ok=True)
    try:
        (directory / f"{revision:04d}.json").write_text(
            path.read_text(encoding="utf-8"), encoding="utf-8"
        )
    except OSError:
        # History is a convenience, not a correctness requirement; a failure to
        # archive must not stop the user from saving their edit.
        pass
