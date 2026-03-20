# Changelog

## [v0.2.0] – 2026-02-28

### Overview

HepAgent is an AI-agent framework built for high-energy physics (HEP) and cosmology research. It wraps the [OpenAI Agents SDK](https://github.com/openai/openai-agents-python) and provides scientist-friendly tooling for automating complex simulation and analysis workflows on HPC systems.

---

### Agent Capabilities

#### 🤖 Agent Types

| Agent | Description |
|---|---|
| **Skilled Agent** | Domain-expert agent that loads specialised skills (e.g. Nyx) at runtime. Driven by a structured identity/operations/ethics prompt assembly and a per-skill LOGBOOK for accumulated insights. |
| **Bash Agent** | General-purpose shell-command agent that solves programming tasks by iteratively executing bash commands. Reads `AGENTS.md` early when present. |

#### 🛠 Built-in Tools

| Tool | Description |
|---|---|
| `execute_bash_command_with_confirmation` | Runs arbitrary shell commands; supports YOLO (auto-approve), CONFIRM, and HUMAN execution modes. |
| `create_transfer_function` | Computes a cosmic matter transfer function with [CAMB](https://camb.readthedocs.io) and writes the 7-column CosmicIC-compatible file. |
| `load_skill_details` | Activates a named skill and loads its SOP, Logbook, and available resource manuals into the agent's context. |
| `read_resource` | Reads a supplementary technical manual for the currently active skill. |
| `ask_user_for_info` | Pauses execution to prompt the scientist for missing parameters (paths, choices, etc.). |
| `wait_for_slurm_job_completion` | Polls `squeue` at 30-second intervals until a SLURM batch job finishes. |
| `update_logbook` | Appends a corrective insight or technical lesson to the active skill's `LOGBOOK.md` so the agent avoids repeating mistakes. |

#### 🔭 Nyx Cosmology Simulation Skill

The bundled **Nyx** skill guides the agent through the full end-to-end workflow for running large-scale structure simulations on HPC:

1. Compile and set up the [CosmicIC](https://bitbucket.org/dpotter/pkdgrav3) initial-condition generator.
2. Generate cosmological initial conditions (`init input.par …`) via the `create_transfer_function` tool (CAMB-backed).
3. Submit and monitor the [Nyx](https://amrex-astro.github.io/Nyx/) simulation job on Perlmutter (NERSC) through SLURM.

#### 🖥 Interactive TUI (TextualAgent)

A full-screen terminal interface built with [Textual](https://textual.textualize.io/):

- **Three execution modes** switchable at any time:
  - **YOLO** (`y` / `Ctrl+Y`) — all agent commands execute immediately.
  - **CONFIRM** (`c`, default) — each command is proposed and awaits your approval.
  - **HUMAN** (`u` / `Ctrl+U`) — agent pauses; you drive.
- **Step navigation** (`←`/`→`) to inspect every reasoning step.
- **Real-time cost tracking** (token usage × model pricing).
- **Smart input widget** with single-line and multi-line modes (`Ctrl+T` to expand, `Ctrl+D` to submit).

#### ⌨️ REPL (Bash REPL)

A lightweight streaming REPL (`scripts/bash_repl.py`) for quick interactive sessions:

- Configurable turn budget (`HEPAGENT_MAX_TURNS`, default 40).
- Bounded bash-command output (`HEPAGENT_OUTPUT_CHAR_LIMIT`, default 8 000 chars).
- Auto-recovery from "dead-air" turns (no assistant text).
- FINALIZE_NOW guardrail signal support.
- `/exit` and `/quit` to terminate cleanly.

#### 🌐 Multi-Provider LLM Support

Provider configuration lives in `src/hepagent/config/providers.toml`. Supported out of the box:

| Provider | Env var |
|---|---|
| **CBORG** (LBNL, default) | `CBORG_API_KEY` |
| **AMSC** | `AMSC_API_KEY` |
| **OpenAI** | `OPENAI_API_KEY` |

Model spec syntax: `provider:model-name` (e.g. `openai:gpt-5-mini`) or a bare model name (defaults to CBORG).

---

### What's Changed since v0.1.0

- **TextualAgent TUI** — brand-new interactive terminal UI replacing the simple print-based flow.
- **Nyx skill & SLURM tool** — full HPC workflow support including SLURM job monitoring.
- **`create_transfer_function` tool** — CAMB-backed transfer function generation as a first-class agent tool.
- **Centralised environment config** — `HEPAGENT_YOLO`, `HEPAGENT_MAX_TURNS`, `HEPAGENT_OUTPUT_CHAR_LIMIT`.
- **Simplified bash agent** — removed unused guardrail policy machinery.
- **Anti-crawl safeguards** — guards against runaway directory exploration in the REPL.
- **Dead-air retry** — agent automatically recovers from silent LLM turns.
- **Decoupled tests** — test suite no longer depends on specific HPC problem data.

---

### Installation

```bash
uv python install 3.12
make sync
source .venv/bin/activate
uv run hepagent --help
```

### Quick Start

```bash
# Run the skilled agent with a Nyx simulation task
uv run hepagent --agent "scientist" \
    --task "I would like to simulate a cosmology sky with Nyx code." \
    --max-turn 30

# List available models for a provider
uv run hepagent list-models --platform cborg
```

[v0.2.0]: https://github.com/xju2/hepagent/releases/tag/v0.2.0
