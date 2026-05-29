"""JFC figure validation and listing tools."""

from __future__ import annotations

import subprocess
from pathlib import Path

from agents import function_tool
from hepagent.agents.jfc._data import get_jfc_data_dir

_PHASE_DIR_MAP = {
    "1": "phase1_strategy",
    "2": "phase2_exploration",
    "3": "phase3_selection",
    "4a": "phase4a_inference_expected",
    "4b": "phase4b_inference_partial",
    "4c": "phase4c_inference_observed",
    "5": "phase5_documentation",
}


@function_tool
async def validate_figures(analysis_root: str, phase: str) -> str:
    """
    Run lint_plots.py on figures in the phase outputs directory.

    Returns the linter output. Any RED FLAG lines indicate Category A issues.
    Wraps hepagent/agents/jfc/data/conventions/lint_plots.py.

    Args:
        analysis_root: Absolute path to the analysis root directory.
        phase: Phase identifier: "1", "2", "3", "4a", "4b", "4c", or "5".
    """
    phase_dir = _PHASE_DIR_MAP.get(phase)
    if not phase_dir:
        return f"Error: unknown phase '{phase}'. Valid: {', '.join(_PHASE_DIR_MAP)}"

    figures_dir = Path(analysis_root) / phase_dir / "outputs" / "figures"
    if not figures_dir.exists():
        return f"No figures directory found at {figures_dir}"

    lint_script = get_jfc_data_dir() / "conventions" / "lint_plots.py"

    if not lint_script.exists():
        return f"lint_plots.py not found at {lint_script}"

    try:
        result = subprocess.run(
            ["python", str(lint_script), str(figures_dir)],
            capture_output=True,
            text=True,
            timeout=60,
        )
        output = result.stdout + result.stderr
        return output or "lint_plots.py produced no output (no figures or no violations)."
    except subprocess.TimeoutExpired:
        return "Error: lint_plots.py timed out after 60s"
    except OSError as e:
        return f"Error running lint_plots.py: {e}"


@function_tool
async def list_phase_figures(analysis_root: str, phase: str) -> str:
    """
    List figure files in the phase outputs/figures/ directory.

    Args:
        analysis_root: Absolute path to the analysis root directory.
        phase: Phase identifier: "1", "2", "3", "4a", "4b", "4c", or "5".
    """
    phase_dir = _PHASE_DIR_MAP.get(phase)
    if not phase_dir:
        return f"Error: unknown phase '{phase}'. Valid: {', '.join(_PHASE_DIR_MAP)}"

    figures_dir = Path(analysis_root) / phase_dir / "outputs" / "figures"
    if not figures_dir.exists():
        return f"No figures directory at {figures_dir}"

    figures = sorted(figures_dir.glob("*"))
    if not figures:
        return f"No figures found in {figures_dir}"

    lines = [f"Figures in {figures_dir} ({len(figures)} files):"]
    for fig in figures:
        size_kb = fig.stat().st_size / 1024
        lines.append(f"  {fig.name}  ({size_kb:.1f} KB)")
    return "\n".join(lines)
