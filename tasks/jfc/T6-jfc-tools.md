# T6: JFC-Specific Tools

**Priority:** P2
**Depends on:** T2 (directory structure must exist)
**Blocks:** —

## Goal

Implement the HepAgent function tools that JFC executor and reviewer agents
will call to interact with the analysis environment: running pixi tasks,
reading phase artifacts, validating figures, and managing the experiment log.

## Deliverables

### `src/hepagent/tools/jfc/pixi.py`

```python
@function_tool
async def run_pixi_task(
    task_name: str,
    analysis_root: str,
    args: list[str] = [],
    timeout_seconds: int = 300,
) -> str:
    """
    Run a pixi task in the analysis directory.

    Examples:
      run_pixi_task("all", "/analyses/z_boson")
      run_pixi_task("plot", "/analyses/z_boson", args=["--phase", "3"])

    Returns stdout+stderr combined. Raises on non-zero exit.
    """
```

```python
@function_tool
async def list_pixi_tasks(analysis_root: str) -> str:
    """List available pixi tasks in the analysis directory."""
```

### `src/hepagent/tools/jfc/artifacts.py`

```python
@function_tool
async def read_phase_artifact(
    analysis_root: str,
    phase: str,  # "1", "2", "3", "4a", "4b", "4c", "5"
) -> str:
    """
    Read the primary artifact markdown for a completed phase.

    Returns the artifact content (truncated to 8000 chars if oversized,
    with a summary header).
    """

@function_tool
async def write_phase_artifact(
    analysis_root: str,
    phase: str,
    content: str,
) -> str:
    """
    Write or overwrite the primary artifact for a phase.

    Returns the artifact file path on success.
    """

@function_tool
async def append_experiment_log(
    analysis_root: str,
    entry: str,
) -> str:
    """
    Append an entry to the experiment_log.md (append-only lab notebook).

    Prepends a timestamp and phase separator automatically.
    """

@function_tool
async def read_commitments(analysis_root: str) -> str:
    """Read COMMITMENTS.md for the analysis (Phase 1 commitments [D1]-[DN])."""

@function_tool
async def update_commitments(
    analysis_root: str,
    commitment_id: str,   # e.g. "D3"
    status: Literal["resolved", "downscoped", "pending"],
    evidence: str,
) -> str:
    """Mark a Phase 1 commitment as resolved or downscoped with evidence."""
```

### `src/hepagent/tools/jfc/figures.py`

```python
@function_tool
async def validate_figures(
    analysis_root: str,
    phase: str,
) -> str:
    """
    Run lint_plots.py on figures in the phase outputs directory.

    Returns the linter output. Any RED FLAG lines indicate Category A issues.
    Wraps testarea/jfc/src/conventions/lint_plots.py.
    """

@function_tool
async def list_phase_figures(
    analysis_root: str,
    phase: str,
) -> str:
    """List figure files in the phase outputs/figures/ directory."""
```

### `src/hepagent/tools/jfc/pdf.py`

```python
@function_tool
async def compile_analysis_note(
    analysis_root: str,
    note_markdown_path: str,
    output_pdf_path: str,
) -> str:
    """
    Compile a markdown analysis note to PDF.

    Pipeline:
      pandoc {note_markdown_path} → .tex
      postprocess_tex.py → .tex (deterministic fixes)
      tectonic → .pdf

    Returns the PDF path on success, or error message on failure.
    Uses testarea/jfc/src/conventions/postprocess_tex.py.
    """
```

### Tool Registration

Add all JFC tools to `agents/jfc/executor.py`'s tool list. Only load them
when creating a JFC executor or reviewer agent — not in the general skilled
agent baseline.

Suggested import pattern in `agents/skilled.py`:
```python
if context.active_skill == "jfc":
    from hepagent.tools.jfc import get_jfc_tools
    extra_tools = get_jfc_tools()
```

Or: add all JFC tools at skilled agent creation time (simpler, negligible overhead).

### `src/hepagent/tools/jfc/__init__.py`

```python
def get_jfc_tools() -> list:
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
    ]
```

## Acceptance Criteria

- [x] `run_pixi_task("all", path)` calls `pixi run all` in the analysis dir
- [x] `append_experiment_log(path, "Found X")` appends a timestamped entry
- [x] `validate_figures(path, "3")` runs `lint_plots.py` and returns output
- [x] `update_commitments(path, "D1", "resolved", "closure chi2=1.3")` writes
  to COMMITMENTS.md
- [x] All tools handle missing directories or files gracefully (return error
  string, not raise)
- [x] Unit tests in `tests/tools/jfc/` covering each tool
