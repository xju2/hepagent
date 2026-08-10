"""Analysis scaffolding: turn a plan into a directory tree.

The plan is written first and everything else follows from it — one working
directory per node, each seeded with that node's prompt as its `CLAUDE.md`, and a
provenance graph compiled from the same document. Nothing here knows how many
nodes there are or what they are called.
"""

from __future__ import annotations

import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from agents import function_tool
from hepagent.agents.jfc._data import get_jfc_data_dir
from hepagent.plan.schema import AnalysisPlan
from hepagent.plan.store import save_plan
from hepagent.plan.templates import DEFAULT_TEMPLATE, instantiate
from hepagent.plan.templates.registry import CONVENTIONS_FOR_TYPE, substitute

#: Created inside every node's working directory.
NODE_SUBDIRS = ["outputs", "outputs/figures", "outputs/results", "src", "review", "logs"]


def _write_root_files(
    analysis_root: Path,
    plan: AnalysisPlan,
    physics_prompt: str,
    variables: dict[str, str],
) -> None:
    """Write the analysis-level documents that are not owned by any node."""
    templates_dir = get_jfc_data_dir() / "templates"

    root_template = templates_dir / "root_claude.md"
    if root_template.exists():
        (analysis_root / "CLAUDE.md").write_text(
            substitute(root_template.read_text(encoding="utf-8"), variables),
            encoding="utf-8",
        )

    (analysis_root / "prompt.md").write_text(
        f"# Physics Prompt\n\n{physics_prompt}\n",
        encoding="utf-8",
    )

    ts = datetime.now(UTC).isoformat(timespec="seconds")
    (analysis_root / "experiment_log.md").write_text(
        f"# Experiment Log — {plan.name}\n\nAnalysis type: "
        f"{plan.analysis_type}\nStarted: {ts}\n\n---\n",
        encoding="utf-8",
    )
    (analysis_root / "retrieval_log.md").write_text(
        f"# Retrieval Log — {plan.name}\n",
        encoding="utf-8",
    )

    (analysis_root / "COMMITMENTS.md").write_text(
        "# Analysis Commitments\n\n"
        "| ID | Commitment | Status | Evidence | Phase Resolved |\n"
        "|----|-----------|--------|----------|---------------|\n"
        "| — | _Populated by the commitment-declaring node_ | — | — | — |\n",
        encoding="utf-8",
    )

    (analysis_root / ".analysis_config").write_text(
        "# Set data_dir to the path where your input ROOT files live.\n"
        "# Add extra allow= lines for additional paths (one per line).\n"
        "data_dir=\n"
        "# allow=/path/to/mc/samples\n",
        encoding="utf-8",
    )


def _write_node_tree(analysis_root: Path, plan: AnalysisPlan) -> None:
    """Create each node's working directory and seed it with the node's prompt."""
    for node in plan.nodes:
        for sub in NODE_SUBDIRS:
            (analysis_root / node.directory / sub).mkdir(parents=True, exist_ok=True)
        if node.prompt:
            (analysis_root / node.directory / "CLAUDE.md").write_text(node.prompt, encoding="utf-8")

    # Citations are collected once, in the last node that writes an analysis
    # note — the one whose bibliography the final PDF is built from.
    note_nodes = [node for node in plan.nodes if node.produces_note]
    if note_nodes:
        bib = analysis_root / note_nodes[-1].outputs_dir / "references.bib"
        bib.parent.mkdir(parents=True, exist_ok=True)
        bib.write_text(
            "% BibTeX references for the analysis note.\n"
            "% Add entries as you cite them with [@key] in the AN.\n",
            encoding="utf-8",
        )


def _copy_reference_material(analysis_root: Path) -> None:
    """Copy conventions/ and methodology/ into the analysis for agents to read.

    `agents/` is deliberately not copied — role definitions are internal to
    hepagent, not part of the analysis record.
    """
    jfc_data = get_jfc_data_dir()
    for dir_name in ("conventions", "methodology"):
        src = jfc_data / dir_name
        dest = analysis_root / dir_name
        if src.exists() and not dest.exists():
            shutil.copytree(src, dest)


def _git_init(analysis_root: Path, analysis_name: str) -> None:
    """Put the scaffold under version control. Git is optional."""
    try:
        subprocess.run(["git", "init"], cwd=analysis_root, check=True, capture_output=True)
        (analysis_root / ".gitignore").write_text(
            "# pixi\n.pixi/\npixi.lock\n\n# Python\n__pycache__/\n*.pyc\n\n# SLURM\n.slurm_*.out\n",
            encoding="utf-8",
        )
        subprocess.run(["git", "add", "-A"], cwd=analysis_root, check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", f"scaffold: initialize {analysis_name} analysis"],
            cwd=analysis_root,
            check=True,
            capture_output=True,
        )
    except subprocess.CalledProcessError:
        pass


async def _scaffold_impl(
    analysis_name: str,
    physics_prompt: str,
    analysis_type: Literal["measurement", "search"],
    base_dir: str = "analyses",
    template: str = DEFAULT_TEMPLATE,
    plan: AnalysisPlan | None = None,
) -> str:
    """Underlying scaffold implementation (testable without the FunctionTool wrapper).

    Args:
        analysis_name: Short name for this analysis.
        physics_prompt: The physics question.
        analysis_type: "measurement" or "search".
        base_dir: Parent directory for analyses.
        template: Built-in plan template to start from. Ignored when `plan` is given.
        plan: An already-authored plan — what `--plan` and the plan editor supply.

    Returns:
        The absolute path to the analysis root, or a string starting with "Error:".
    """
    analysis_root = Path(base_dir).resolve() / analysis_name

    if analysis_root.exists():
        return (
            f"Error: directory already exists: {analysis_root}. "
            f"Use a different analysis_name or remove the existing directory."
        )

    if plan is None:
        try:
            plan = instantiate(
                template,
                analysis_name=analysis_name,
                analysis_type=analysis_type,
                physics_prompt=physics_prompt,
            )
        except Exception as exc:  # noqa: BLE001 - surfaced to the caller as a message
            return f"Error: could not build a plan from template '{template}': {exc}"

    try:
        analysis_root.mkdir(parents=True)
    except OSError as e:
        return f"Error creating directory {analysis_root}: {e}"

    variables = {
        "name": analysis_name,
        "analysis_type": plan.analysis_type,
        "conventions_files": CONVENTIONS_FOR_TYPE.get(plan.analysis_type, ""),
    }

    # The plan lands first: everything below is derived from it, and writing it
    # before `git init` puts it in the scaffold commit.
    save_plan(analysis_root, plan)

    _write_root_files(analysis_root, plan, physics_prompt, variables)
    _write_node_tree(analysis_root, plan)
    _copy_reference_material(analysis_root)

    pixi_template = get_jfc_data_dir() / "templates" / "pixi.toml"
    if pixi_template.exists():
        content = pixi_template.read_text(encoding="utf-8").replace("{name}", analysis_name)
        (analysis_root / "pixi.toml").write_text(content, encoding="utf-8")

    # Seed the provenance graph from the same plan. Imported here to keep the
    # import graph acyclic.
    from hepagent.agents.jfc.graph_builder import bootstrap_graph

    bootstrap_graph(analysis_root, plan)

    _git_init(analysis_root, analysis_name)

    return str(analysis_root)


@function_tool
async def scaffold_jfc_analysis(
    analysis_name: str,
    physics_prompt: str,
    analysis_type: Literal["measurement", "search"],
    base_dir: str = "analyses",
    template: str = DEFAULT_TEMPLATE,
) -> str:
    """
    Create the analysis directory structure and plan for a new analysis.

    Returns the absolute path to the analysis root directory.

    Args:
        analysis_name: Short name for this analysis (e.g., "z_boson_xsec").
        physics_prompt: The physics question or task for this analysis.
        analysis_type: "measurement" or "search".
        base_dir: Parent directory for analyses (default: "analyses").
        template: Plan template to start from, e.g. "jfc-measurement" or
            "jfc-search". Run `hepagent jfc templates` to list them.
    """
    return await _scaffold_impl(analysis_name, physics_prompt, analysis_type, base_dir, template)
