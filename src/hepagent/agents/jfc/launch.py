"""Start a JFC analysis from the plan page.

`plan/runs.py` knows how to supervise *a* run; this is the half that knows what
a JFC run is. It reads what the analysis directory already records — the physics
prompt in `prompt.md`, the type and node ids in `plan.json` — so launching adds
no state of its own beyond the model to use.

Two things happen on the worker thread that do not happen on a CLI run:

- an interaction backend is bound, so every blocking question the agents ask
  (bash approvals, `ask_user_for_info`, the human gate) surfaces on the page
  instead of on a terminal that is not there;
- `run_jfc_analysis` is driven by `asyncio.run` on a *private* event loop, which
  is the whole reason the run gets a thread: its function tools are synchronous
  and would otherwise block the web server's loop for as long as a human takes
  to answer.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from hepagent.plan import store
from hepagent.plan.runs import RUNS, RunHandle, RunInteractionBackend, RunRegistry

#: Progress messages that mean a node finished, rather than that it is working.
#: The orchestrator reports free text; only these two words are load-bearing for
#: the page's node colouring, and a message that matches neither leaves the node
#: "running", which is the safe reading of "something happened here".
DONE_MESSAGES = frozenset({"PASS"})


def _node_status(message: str) -> str:
    return "done" if message.strip() in DONE_MESSAGES else "running"


def start_analysis_run(
    analysis_root: Path | str,
    *,
    name: str | None = None,
    model: str | None = None,
    unattended: bool = False,
    max_iterations: int = 3,
    only_node: str | None = None,
    max_turns: int | None = None,
    registry: RunRegistry | None = None,
) -> RunHandle:
    """Launch the analysis at `analysis_root` on a worker thread.

    Args:
        analysis_root: The analysis directory. Its `plan.json` is what runs —
            whatever the editor last saved.
        name: Analysis name. Defaults to the directory name.
        model: `"provider:model"` override for every agent in the run.
        unattended: Auto-approve shell commands instead of asking the page.
        max_iterations: Floor on review iterations per node.
        only_node: Run just this node and stop — the page's per-node "Run"
            button. Nothing upstream runs and nothing downstream follows.
        max_turns: Per-agent turn cap.
        registry: Registry to start in. Defaults to the process-wide one.

    Returns:
        The handle the page polls.

    Raises:
        RunAlreadyActive: if this analysis is already running.
        store.PlanNotFoundError: if it has no plan to run.
    """
    root = Path(analysis_root).resolve()
    plan = store.load_plan(root)
    analysis_name = name or root.name
    node_ids = {node.id for node in plan.nodes}
    if only_node is not None and only_node not in node_ids:
        raise ValueError(f"Plan '{plan.name}' has no node '{only_node}'.")

    # The plan's own question, which `prompt.md` mirrors — so a prompt edited on
    # the plan page is what the run is launched with, without a reload.
    physics_prompt = plan.problem.strip() or store.read_prompt_file(root) or plan.name

    def runner(handle: RunHandle) -> str:
        from hepagent.agents.jfc.orchestrator import run_jfc_analysis
        from hepagent.interaction import bound_backend
        from hepagent.model_providers import parse_model_spec

        provider, model_name = parse_model_spec(model)

        def progress(node_id: str, message: str) -> None:
            # `record` raises RunCancelled when a stop was asked for, which is
            # what makes "cancel" take effect at the next node boundary.
            handle.record(node_id, message)
            if node_id in node_ids:
                handle.set_node_status(node_id, _node_status(message))

        async def drive() -> str:
            pdf = await run_jfc_analysis(
                analysis_name=analysis_name,
                physics_prompt=physics_prompt,
                analysis_type=plan.analysis_type,  # type: ignore[arg-type]
                base_dir=str(root.parent),
                model_provider=provider,
                model_name=model_name,
                only_node=only_node,
                max_iterations_per_phase=max_iterations,
                max_turns=max_turns,
                progress_callback=progress,
            )
            return str(pdf)

        with bound_backend(RunInteractionBackend(handle)):
            return asyncio.run(drive())

    return (registry or RUNS).start(analysis_name, root, runner, unattended=unattended)
