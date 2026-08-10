"""JFC-specific function tools."""

from __future__ import annotations

from hepagent.tools.jfc.artifacts import (
    append_experiment_log,
    read_commitments,
    read_phase_artifact,
    update_commitments,
    write_phase_artifact,
)
from hepagent.tools.jfc.figures import list_phase_figures, validate_figures
from hepagent.tools.jfc.graph import graph_add_edge, graph_add_node, graph_query
from hepagent.tools.jfc.pdf import compile_analysis_note
from hepagent.tools.jfc.pixi import list_pixi_tasks, run_pixi_task
from hepagent.tools.jfc.scaffold import scaffold_jfc_analysis


def get_jfc_tools() -> list:
    """Return all JFC function tools for registration with an agent."""
    return [
        scaffold_jfc_analysis,
        run_pixi_task,
        list_pixi_tasks,
        read_phase_artifact,
        write_phase_artifact,
        append_experiment_log,
        read_commitments,
        update_commitments,
        validate_figures,
        list_phase_figures,
        compile_analysis_note,
        graph_add_node,
        graph_add_edge,
        graph_query,
    ]
