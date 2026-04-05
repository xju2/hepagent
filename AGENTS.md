# Agent Instructions (hepagent)

## Mission
Build and operate reliable AI agents for HEP/cosmology workflows, with strong support for Nyx simulation tasks.

## Core Architecture
- CLI entrypoint: `src/hepagent/main.py` (`hepagent` command).
- Main runtime agent: `src/hepagent/agents/skilled.py`.
- Textual TUI runtime: `src/hepagent/agents/textual.py`.
- Textual TUI architecture notes and anti-regression guidance: `docs/TEXTUAL.md`.
- Shared instruction registry: `.agents/`:
  - `common/IDENTITY.md`, `common/OPERATION.md`, `common/ETHICS.md`
  - `storage/MEMORY.md`
  - `skills/<skill>/SKILL.md`, `LOGBOOK.md`, optional `resources/*.md`
- Skill context state is tracked in `AgentContext.active_skill`.

## Required Agent Behavior
- Treat the user task as authoritative; do not change intent.
- If a domain skill is relevant, call `load_skill_details(<skill>)` first.
- Before changing Textual TUI behavior, read `docs/TEXTUAL.md` and preserve its invariants.
- Only read skill resources on demand via `read_resource`.
- Ask for missing required inputs with `ask_user_for_info` (one clear question at a time).
- When a tool/action fails or the user corrects you, record it with `update_logbook`.
- When finishing a task, update relevant files in `docs/`, `README.md`, and `AGENTS.md` if critical lessons were learned.
- For long HPC runs, use `wait_for_slurm_job_completion(job_id)` instead of polling manually.

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
- Default provider is `cborg`; model spec supports `provider:model` (e.g. `openai:gpt-5-mini`).
- Example:
  - `uv run hepagent run --agent scientist "List repository files"`
  - `uv run hepagent run --agent scientist "..." --model openai:gpt-5-mini`
