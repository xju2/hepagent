# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
make sync          # Install all dependencies (uv sync --all-extras --group dev)
make format        # Auto-fix formatting and lint issues (ruff format + ruff check --fix)
make lint          # Check lint without fixing (ruff check)
make tests         # Run tests, excluding slow skilled_agent tests
make coverage      # Run tests with coverage report
```

Run a single test:
```bash
uv run pytest tests/test_model_providers.py
uv run pytest tests/test_main_chat.py::test_foo
uv run pytest -k "not skilled_agent and test_repl"
```

Run the agent:
```bash
uv run hepagent run --agent scientist "List repository files"
uv run hepagent run --agent scientist "..." --model openai:gpt-5-mini
uv run hepagent repl --agent shell
uv run hepagent web            # browser UI; needs the optional `web` extra
```

## Architecture

### Entry point and CLI

`src/hepagent/main.py` defines the `hepagent` Typer CLI with subcommands: `run`, `repl`, `web`, `list-agents`, `list-platforms`, `list-models`. A `DefaultToRunGroup` makes unknown first tokens route to `run` for backward compatibility.

### Agent types

**Skilled Agent** (`agents/skilled.py`): The main task-execution agent (`--agent scientist`). Loads its system prompt dynamically via `AgentManifestLoader`, which assembles identity/operation/ethics/memory from `.agents/common/` and injects a skill catalog. Tools include `execute_bash_command_with_confirmation`, `load_skill_details`, `read_resource`, `ask_user_for_info`, `update_logbook`, `update_user_profile`, `wait_for_slurm_job_completion`.

**Role Agents** (`agents/role.py`): Lightweight stateless agents (`shell`, `shell_describer`, `coder`) with hardcoded role prompts and no tools. Returned by `create_role_cfg()`.

**Textual TUI** (`agents/textual.py`): A Textual-based full-screen TUI used by `hepagent run`. Agent messages are grouped into reviewable steps; a virtual "task step" is appended after completion to allow new-task entry without clipping history. `VerticalScroll` must remain non-focusable to preserve arrow-key navigation. See `docs/TEXTUAL.md` for invariants — read it before changing TUI behavior.

**CLI REPL** (`agents/cli_repl.py`): A prompt_toolkit REPL used by `hepagent repl`. Supports slash commands (`/agent`, `/model`, `/mode`, `/platforms`, etc.) and streaming output.

**Web UI** (`web/`): A Chainlit browser chat used by `hepagent web`, behind the optional `web` extra. Only `web/app.py` imports Chainlit; `web/bridge.py`, `web/tools.py`, `web/turn.py` and `web/session.py` are transport-agnostic and unit-tested without it. `web/turn.py` ports the REPL's streaming loop (reusing its recovery helpers); `web/tools.py` swaps the terminal-bound tools for browser-driven ones and offloads blocking tools to threads. See `docs/WEB.md` for invariants — read it before changing web behavior.

### Skill registry (`.agents/`)

Skills are stored in `.agents/skills/<skill_name>/` with a `SKILL.md` (YAML frontmatter + instructions), `LOGBOOK.md` (error/correction history), and optional `resources/*.md` (domain manuals). The active skill is tracked in `AgentContext.active_skill`. When a domain skill is relevant, the agent must call `load_skill_details(<skill>)` first; resources are loaded on demand via `read_resource`. The only current domain skill is `nyx` (Nyx cosmology simulation).

### Model providers

`src/hepagent/model_providers.py` provides a unified OpenAI-compatible interface to multiple providers (`cborg`, `openai`, `amsc`, `gemini`). Provider configuration lives in `src/hepagent/config/providers.toml` and is copied to `$HOME/.hepagent/config/` on first run. Model specs follow the `provider:model` format; the default provider is `cborg`.

### Context and session

`AgentContext` (dataclass) carries `agent_name` and `active_skill` through the agent run. Persistent conversation history uses `SQLiteSession` from the `openai-agents` SDK, stored in `$HOME/.hepagent/sessions/conversation.db`.

### User profile

`~/.hepagent/USER.md` stores durable user context (preferences, projects, expertise). The `update_user_profile` function tool appends bullet notes under sections; agents call it when new significant context is learned.

### Tools

Common domain-agnostic tools: `src/hepagent/tools/common.py`. Nyx-specific tools: `src/hepagent/tools/nyx/`. IRI/Globus compute tools: `src/hepagent/tools/iri/`. Adding a new tool means creating a `@function_tool`-decorated function and registering it in the relevant agent's `tools=[...]` list.

## Required agent behaviors

- If a domain skill is relevant, call `load_skill_details(<skill>)` before proceeding.
- Before changing Textual TUI behavior, read `docs/TEXTUAL.md` and preserve its invariants.
- Before changing web UI behavior, read `docs/WEB.md` and preserve its invariants.
- When a task fails or the user corrects the agent, record it with `update_logbook`.
- For long HPC runs, use `wait_for_slurm_job_completion(job_id)` instead of polling manually.
- If a task is described in a markdown file (e.g. `tasks/*.md`), follow the instructions there exactly and update the `## Progress report` section with progress and next steps.
- When finishing a task with critical lessons learned, update relevant files in `docs/`, `README.md`, and `AGENTS.md`.
