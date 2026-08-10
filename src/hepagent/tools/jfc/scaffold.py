"""JFC analysis scaffolding tool."""

from __future__ import annotations

import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from agents import function_tool
from hepagent.agents.jfc._data import get_jfc_data_dir

PHASE_DIRS = [
    "phase1_strategy",
    "phase2_exploration",
    "phase3_selection",
    "phase4a_inference_expected",
    "phase4b_inference_partial",
    "phase4c_inference_observed",
    "phase5_documentation",
]

PHASE_SUBDIRS = ["outputs", "outputs/figures", "src", "review", "logs"]

# Maps each phase directory to the template that generates its CLAUDE.md
_PHASE_TEMPLATE_MAP = {
    "phase1_strategy": "phase1_claude.md",
    "phase2_exploration": "phase2_claude.md",
    "phase3_selection": "phase3_claude.md",
    "phase4a_inference_expected": "phase4_claude.md",
    "phase4b_inference_partial": "phase4_claude.md",
    "phase4c_inference_observed": "phase4_claude.md",
    "phase5_documentation": "phase5_claude.md",
}

_CONVENTIONS_FOR_TYPE = {
    "measurement": (
        "- `conventions/unfolding.md` — for unfolded measurements\n"
        "- `conventions/extraction.md` — for extraction/counting measurements\n"
        "\nThe technique selected in Phase 1 determines which file applies."
    ),
    "search": "- `conventions/search.md`",
}


def _substitute(template: str, variables: dict[str, str]) -> str:
    for key, value in variables.items():
        template = template.replace("{{" + key + "}}", value)
    return template


async def _scaffold_impl(
    analysis_name: str,
    physics_prompt: str,
    analysis_type: Literal["measurement", "search"],
    base_dir: str = "analyses",
) -> str:
    """Underlying scaffold implementation (testable without FunctionTool wrapper)."""
    analysis_root = Path(base_dir).resolve() / analysis_name

    if analysis_root.exists():
        return (
            f"Error: directory already exists: {analysis_root}. "
            f"Use a different analysis_name or remove the existing directory."
        )

    try:
        analysis_root.mkdir(parents=True)
    except OSError as e:
        return f"Error creating directory {analysis_root}: {e}"

    jfc_data = get_jfc_data_dir()
    templates_dir = jfc_data / "templates"

    variables = {
        "name": analysis_name,
        "analysis_type": analysis_type,
        "conventions_files": _CONVENTIONS_FOR_TYPE.get(analysis_type, ""),
    }

    # Root CLAUDE.md
    root_template = templates_dir / "root_claude.md"
    if root_template.exists():
        (analysis_root / "CLAUDE.md").write_text(
            _substitute(root_template.read_text(encoding="utf-8"), variables),
            encoding="utf-8",
        )

    # Physics prompt
    (analysis_root / "prompt.md").write_text(
        f"# Physics Prompt\n\n{physics_prompt}\n",
        encoding="utf-8",
    )

    # Experiment log and retrieval log
    ts = datetime.now(UTC).isoformat(timespec="seconds")
    (analysis_root / "experiment_log.md").write_text(
        f"# Experiment Log — {analysis_name}\n\nAnalysis type: "
        f"{analysis_type}\nStarted: {ts}\n\n---\n",
        encoding="utf-8",
    )
    (analysis_root / "retrieval_log.md").write_text(
        f"# Retrieval Log — {analysis_name}\n",
        encoding="utf-8",
    )

    # COMMITMENTS.md placeholder
    (analysis_root / "COMMITMENTS.md").write_text(
        "# Phase 1 Commitments\n\n"
        "| ID | Commitment | Status | Evidence | Phase Resolved |\n"
        "|----|-----------|--------|----------|---------------|\n"
        "| — | _Populated by Phase 1 executor_ | — | — | — |\n",
        encoding="utf-8",
    )

    # .analysis_config (data directory isolation hook)
    (analysis_root / ".analysis_config").write_text(
        "# Set data_dir to the path where your input ROOT files live.\n"
        "# Add extra allow= lines for additional paths (one per line).\n"
        "data_dir=\n"
        "# allow=/path/to/mc/samples\n",
        encoding="utf-8",
    )

    # Phase subdirectories + per-phase CLAUDE.md
    for phase in PHASE_DIRS:
        for sub in PHASE_SUBDIRS:
            (analysis_root / phase / sub).mkdir(parents=True, exist_ok=True)

        template_name = _PHASE_TEMPLATE_MAP.get(phase)
        if template_name:
            phase_template = templates_dir / template_name
            if phase_template.exists():
                (analysis_root / phase / "CLAUDE.md").write_text(
                    _substitute(phase_template.read_text(encoding="utf-8"), variables),
                    encoding="utf-8",
                )

    # Extra results dir for Phase 5
    (analysis_root / "phase5_documentation" / "outputs" / "results").mkdir(
        parents=True, exist_ok=True
    )

    # Stub references.bib for citations
    (analysis_root / "phase5_documentation" / "outputs" / "references.bib").write_text(
        "% BibTeX references for the analysis note.\n"
        "% Add entries as you cite them with [@key] in the AN.\n",
        encoding="utf-8",
    )

    # Copy conventions/ and methodology/ into the analysis directory.
    # agents/ is NOT copied — agent role definitions are internal to hepagent.
    for dir_name in ("conventions", "methodology"):
        src = jfc_data / dir_name
        dest = analysis_root / dir_name
        if src.exists() and not dest.exists():
            shutil.copytree(src, dest)

    # pixi.toml from template
    pixi_template = templates_dir / "pixi.toml"
    if pixi_template.exists():
        content = pixi_template.read_text(encoding="utf-8").replace("{name}", analysis_name)
        (analysis_root / "pixi.toml").write_text(content, encoding="utf-8")

    # Seed the analysis graph: problem node, root node, and the pending artifact
    # chain for all seven phases. Imported here to keep the import graph acyclic.
    from hepagent.agents.jfc.graph_builder import bootstrap_graph

    bootstrap_graph(analysis_root, analysis_name, analysis_type, physics_prompt)

    # Git initialization
    try:
        subprocess.run(["git", "init"], cwd=analysis_root, check=True, capture_output=True)
        (analysis_root / ".gitignore").write_text(
            "# pixi\n.pixi/\npixi.lock\n\n# Python\n__pycache__/\n*.pyc\n\n# SLURM\n.slurm_*.out\n",
            encoding="utf-8",
        )
        subprocess.run(["git", "add", "-A"], cwd=analysis_root, check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", f"scaffold: initialize {analysis_name} JFC analysis"],
            cwd=analysis_root,
            check=True,
            capture_output=True,
        )
    except subprocess.CalledProcessError:
        pass  # git is optional

    return str(analysis_root)


@function_tool
async def scaffold_jfc_analysis(
    analysis_name: str,
    physics_prompt: str,
    analysis_type: Literal["measurement", "search"],
    base_dir: str = "analyses",
) -> str:
    """
    Create the JFC analysis directory structure for a new analysis.

    Returns the absolute path to the analysis root directory.

    Args:
        analysis_name: Short name for this analysis (e.g., "z_boson_xsec").
        physics_prompt: The physics question or task for this analysis.
        analysis_type: "measurement" or "search".
        base_dir: Parent directory for analyses (default: "analyses").
    """
    return await _scaffold_impl(analysis_name, physics_prompt, analysis_type, base_dir)
