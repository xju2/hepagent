# T2: Analysis Scaffolding Tool

**Priority:** P1 (needed before any phase can run)
**Depends on:** nothing
**Blocks:** T5, T6

## Goal

Port `testarea/jfc/src/scaffold_analysis.py` as a HepAgent `@function_tool`
that creates the standard JFC analysis directory structure, initializes git,
and writes the initial experiment log.

## Why This Is Separate

Scaffolding is a side-effect-producing, filesystem-level operation. It belongs
as an explicit tool call (not baked into agent logic) so it is auditable and
can be retried without duplicating state.

## Deliverables

### `src/hepagent/tools/jfc/__init__.py`

Empty init file to make this a package.

### `src/hepagent/tools/jfc/scaffold.py`

```python
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
    """
```

**Directory layout to create** (mirrors `testarea/jfc/src/templates/`):
```
{base_dir}/{analysis_name}/
├── prompt.md                   # physics_prompt written here
├── experiment_log.md           # initialized with header
├── retrieval_log.md            # empty
├── COMMITMENTS.md              # Phase 1 placeholder
├── phase1_strategy/
│   ├── outputs/
│   ├── src/
│   ├── review/
│   └── logs/
├── phase2_exploration/   (same sub-structure)
├── phase3_selection/
├── phase4a_inference_expected/
├── phase4b_inference_partial/
├── phase4c_inference_observed/
└── phase5_documentation/
    └── results/
```

**Symlinks to create** (pointing into `testarea/jfc/src/`):
- `{analysis_root}/conventions/` → `testarea/jfc/src/conventions/`
- `{analysis_root}/methodology/` → `testarea/jfc/src/methodology/`
- `{analysis_root}/agents/` → `testarea/jfc/src/agents/`

**Git initialization:**
- `git init` in `{analysis_root}`
- Initial commit: "scaffold: initialize {analysis_name} JFC analysis"
- Copy `testarea/jfc/src/templates/pixi.toml` → `{analysis_root}/pixi.toml`

**Experiment log header** format:
```markdown
# Experiment Log — {analysis_name}

Analysis type: {analysis_type}
Started: {ISO timestamp}

---
```

**Error handling:**
- If directory already exists: return error message (do not overwrite)
- If git init fails: log warning, continue (git is optional)

### Registration

Add `scaffold_jfc_analysis` to the skilled agent's tools list in
`agents/skilled.py` when the `jfc` skill is active. This means:
- Either add it unconditionally (harmless; it's a HEP-specific tool)
- Or gate it behind `AgentContext.active_skill == "jfc"` (preferred)

## Acceptance Criteria

- [ ] `scaffold_jfc_analysis("test_z_boson", "...", "measurement")` creates
  the full directory tree
- [ ] `analyses/test_z_boson/prompt.md` contains the physics prompt
- [ ] `analyses/test_z_boson/experiment_log.md` has the correct header
- [ ] All phase subdirectories (`phase1_strategy/outputs/`, etc.) exist
- [ ] Calling it twice with the same name returns an error, not overwrite
- [ ] Tool is importable: `from hepagent.tools.jfc.scaffold import scaffold_jfc_analysis`
- [ ] Unit test in `tests/tools/jfc/test_scaffold.py`
