"""Discovering and instantiating built-in plan templates.

This is the one module in :mod:`hepagent.plan` that knows where JFC keeps its
prompt markdown. Everything else in the package is domain-agnostic; the coupling
lives here because a *built-in template* is inherently about the built-in
pipeline. `get_jfc_data_dir` has no package imports of its own, so this creates
no cycle.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from hepagent.agents.jfc._data import get_jfc_data_dir
from hepagent.plan.schema import AnalysisPlan, PlanNode, utc_now

TEMPLATE_DIR = Path(__file__).parent
TEMPLATE_SUFFIX = ".json"

#: Used when a caller does not name one.
DEFAULT_TEMPLATE = "jfc-measurement"

#: Which convention documents apply to each analysis type. Substituted into the
#: phase prompts as ``{{conventions_files}}``; the technique chosen in the first
#: node decides which of the measurement files actually applies.
CONVENTIONS_FOR_TYPE: dict[str, str] = {
    "measurement": (
        "- `conventions/unfolding.md` — for unfolded measurements\n"
        "- `conventions/extraction.md` — for extraction/counting measurements\n"
        "\nThe technique selected in Phase 1 determines which file applies."
    ),
    "search": "- `conventions/search.md`",
}


class TemplateNotFoundError(LookupError):
    """Raised when no built-in template carries the requested name."""


def list_templates() -> list[str]:
    """Return the names of every built-in template, sorted."""
    return sorted(path.stem for path in TEMPLATE_DIR.glob(f"*{TEMPLATE_SUFFIX}"))


def describe_templates() -> list[tuple[str, str]]:
    """Return `(name, description)` for every built-in template, sorted by name."""
    described: list[tuple[str, str]] = []
    for name in list_templates():
        try:
            data = _read(name)
        except TemplateNotFoundError:  # pragma: no cover - glob just found it
            continue
        described.append((name, str(data.get("description", ""))))
    return described


def load_template(name: str) -> dict[str, Any]:
    """Return the raw template document, prompts still unresolved."""
    return _read(name)


def instantiate(
    name: str,
    *,
    analysis_name: str,
    analysis_type: str,
    physics_prompt: str = "",
) -> AnalysisPlan:
    """Build a concrete plan from a built-in template.

    Resolves every node's ``prompt_ref`` into literal markdown and substitutes
    the ``{{name}}`` / ``{{analysis_type}}`` / ``{{conventions_files}}``
    placeholders, so the resulting plan is self-contained and editable.

    Args:
        name: Template name, e.g. ``"jfc-measurement"``.
        analysis_name: Short analysis identifier.
        analysis_type: ``"measurement"`` or ``"search"``.
        physics_prompt: The physics question, stored on the plan.

    Raises:
        TemplateNotFoundError: if no template carries that name.
    """
    data = _read(name)
    variables = {
        "name": analysis_name,
        "analysis_type": analysis_type,
        "conventions_files": CONVENTIONS_FOR_TYPE.get(analysis_type, ""),
    }

    data = dict(data)
    data["nodes"] = [_resolve_node(raw, variables) for raw in data.get("nodes") or ()]
    data["name"] = analysis_name
    data["analysis_type"] = analysis_type
    data["template"] = name
    data["problem"] = physics_prompt
    data["revision"] = 0
    data["created_at"] = utc_now()
    data["updated_at"] = data["created_at"]

    return AnalysisPlan.from_dict(data)


def substitute(template: str, variables: dict[str, str]) -> str:
    """Replace ``{{key}}`` placeholders in `template`."""
    for key, value in variables.items():
        template = template.replace("{{" + key + "}}", value)
    return template


def _read(name: str) -> dict[str, Any]:
    path = TEMPLATE_DIR / f"{name}{TEMPLATE_SUFFIX}"
    if not path.is_file():
        available = ", ".join(list_templates()) or "(none)"
        raise TemplateNotFoundError(f"No plan template named '{name}'. Available: {available}")
    return json.loads(path.read_text(encoding="utf-8"))


def _resolve_node(raw: dict[str, Any], variables: dict[str, str]) -> dict[str, Any]:
    """Turn a template node into a plan node by inlining its prompt."""
    node = {k: v for k, v in raw.items() if k != "prompt_ref"}
    prompt_ref = raw.get("prompt_ref")
    if prompt_ref and not node.get("prompt"):
        node["prompt"] = substitute(_read_prompt(prompt_ref), variables)
    elif node.get("prompt"):
        node["prompt"] = substitute(node["prompt"], variables)
    # Fail loudly on a malformed template rather than scaffolding a node whose
    # executor would run with no instructions.
    PlanNode.from_dict(node)
    return node


def _read_prompt(prompt_ref: str) -> str:
    """Read a template-relative prompt file out of the JFC data directory."""
    path = get_jfc_data_dir() / prompt_ref
    if not path.is_file():
        raise TemplateNotFoundError(f"Template prompt not found: {path}")
    return path.read_text(encoding="utf-8")
