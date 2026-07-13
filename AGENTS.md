# Agent Instructions (hepagent)

## Mission
Build and operate reliable AI agents for HEP/cosmology workflows, with strong support for Nyx simulation tasks.

## Core Architecture
- CLI entrypoint: `src/hepagent/main.py` (`hepagent` command).
- Main runtime agent: `src/hepagent/agents/skilled.py`.
- Interactive CLI REPL runtime: `src/hepagent/agents/cli_repl.py`.
- Headless task runner: `hepagent run` in `src/hepagent/main.py`.
- Shared instruction registry: `.agents/`:
  - `common/IDENTITY.md`, `common/OPERATION.md`, `common/ETHICS.md`
  - `storage/MEMORY.md`
  - `skills/<skill>/SKILL.md`, `LOGBOOK.md`, optional `resources/*.md`
- Skill context state is tracked in `AgentContext.active_skill`.

## Code Style
- Match existing TypeScript style and naming in nearby files.
- Prefer explicit, readable logic over compact clever code.
- Add brief comments only when logic is not obvious.

## Required Agent Behavior
- Treat the user task as authoritative; do not change intent.
- If a domain skill is relevant, call `load_skill_details(<skill>)` first.
- Only read skill resources on demand via `read_resource`.
- Ask for missing required inputs with `ask_user_for_info` (one clear question at a time).
- When a tool/action fails or the user corrects you, record it with `update_logbook`.
- When finishing a task, update relevant files in `docs/`, `README.md`, and `AGENTS.md` if critical lessons were learned.
- For long HPC runs, use `wait_for_slurm_job_completion(job_id)` instead of polling manually.
- If a task is described in a markdown file, follow the instructions there exactly and update the "## Progress report" section with your progress and next steps.

## Command Safety
- Never run destructive shell commands without explicit user approval.
- Confirm before expensive operations (large simulations, heavy jobs, large file writes).
- Keep outputs concise and actionable; include next steps when blocked.

## Local Development
- Setup: `make sync`
- Format: `make format`
- Tests (default): `make tests` (`-k "not skilled_agent"`)

## CLI Usage Rules
- For `hepagent run` (and the default-to-run behavior), `TASK_PROMPT` is a required positional argument.
- Use `hepagent run --non-interactive "..."` for fully automated runs; it must not prompt for bash approval or `ask_user_for_info` input.
- Default provider is `cborg`; model spec supports `provider:model` (e.g. `openai:gpt-5-mini`).
- Example:
  - `uv run hepagent run --agent scientist "List repository files"`
  - `uv run hepagent run --agent scientist "..." --model openai:gpt-5-mini`

## Task and Documentation structure
- `tasks/`: All tasks go here. Each task file describes a specific implementation or research task, with a clear goal. You may create sub-tasks for a user-defined task.
- `docs/`: All design, planning, user-facing documents go here. They outline the rationale, approach, and expected outcomes for features or research directions.

### Cross-referencing
Always link task files and planning docs to each other bidirectionally:

- Every task file (tasks/<task>.md) must open with a "Related documents" section listing the relevant plan doc(s), the original request task file, and any workflow or design docs it touches. Use relative Markdown links.
- Every plan or design doc section that spawns a concrete implementation task must include a back-link to that task file, e.g. Implementation tracked in [tasks/foo.md](../tasks/foo.md).

## Documentation
* If you worked on a task defined in `tasks/<task>.md`, update that doc with a short status report. Mark it as "In progress" or "Completed" and add a brief summary of what you did.
* Put the status report at the end of the task file so the task body stays intact and the file becomes a chronological record of work over time.
* Keep docs brief and task-oriented unless the user asks for a full guide
