"""JFC analysis scaffolding tool."""

from __future__ import annotations

import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from agents import function_tool
from hepagent.helpers import get_repo_root


def _jfc_src() -> Path:
    return get_repo_root() / "testarea" / "jfc" / "src"


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

    # Write physics prompt
    (analysis_root / "prompt.md").write_text(
        f"# Physics Prompt\n\n{physics_prompt}\n",
        encoding="utf-8",
    )

    # Write initial experiment log
    ts = datetime.now(UTC).isoformat(timespec="seconds")
    (analysis_root / "experiment_log.md").write_text(
        f"# Experiment Log — {analysis_name}\n\n"
        f"Analysis type: {analysis_type}\n"
        f"Started: {ts}\n\n"
        "---\n",
        encoding="utf-8",
    )

    # Empty retrieval log
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

    # Phase subdirectories
    for phase in PHASE_DIRS:
        for sub in PHASE_SUBDIRS:
            (analysis_root / phase / sub).mkdir(parents=True, exist_ok=True)

    # Extra results dir for Phase 5
    (analysis_root / "phase5_documentation" / "outputs" / "results").mkdir(
        parents=True, exist_ok=True
    )

    # Symlinks into testarea/jfc/src/
    jfc_src = _jfc_src()
    for link_name, src_name in [
        ("conventions", "conventions"),
        ("methodology", "methodology"),
        ("agents", "agents"),
    ]:
        link = analysis_root / link_name
        src = jfc_src / src_name
        if not link.exists() and src.exists():
            link.symlink_to(src.resolve())

    # Copy pixi.toml template
    pixi_template = jfc_src / "templates" / "pixi.toml"
    if pixi_template.exists():
        content = pixi_template.read_text(encoding="utf-8")
        content = content.replace("{name}", analysis_name)
        (analysis_root / "pixi.toml").write_text(content, encoding="utf-8")

    # Git initialization
    try:
        subprocess.run(
            ["git", "init"],
            cwd=analysis_root,
            check=True,
            capture_output=True,
        )
        gitignore = (
            "# pixi\n.pixi/\npixi.lock\n\n# Python\n__pycache__/\n*.pyc\n\n# SLURM\n.slurm_*.out\n"
        )
        (analysis_root / ".gitignore").write_text(gitignore, encoding="utf-8")
        subprocess.run(
            ["git", "add", "-A"],
            cwd=analysis_root,
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "commit", "-m", f"scaffold: initialize {analysis_name} JFC analysis"],
            cwd=analysis_root,
            check=True,
            capture_output=True,
        )
    except subprocess.CalledProcessError:
        # Git is optional — log warning but continue
        pass

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
