# Project
![coverage](https://img.shields.io/badge/coverage-70%25-green)

## Introduction

`hepagent` is an AI agent framework tailored for High Energy Physics (HEP) and cosmology workflows. Key features include:

- **Multi-provider LLM support**: seamlessly switch between providers such as `cborg`, `openai`, `amsc`, and `gemini` via a unified CLI (`hepagent run`, `hepagent repl`) or programmatic API, with per-provider configuration managed in `providers.toml`.
- **Skill-based domain knowledge**: a modular skill registry (`.agents/skills/`) packages domain-specific instructions (e.g. running Nyx cosmology simulations) that agents load on demand, keeping prompts concise and context-relevant.
- **CLI REPL**: a Claude Code-inspired interactive REPL (`hepagent repl`) with slash commands, streaming transcript output, command approval prompts, and markdown/code rendering.
- **Bash and execution modes**: interactive shell-capable agents with configurable YOLO (auto-approve), CONFIRM, and HUMAN execution modes, output character limits, and turn budgets—safe for running on HPC clusters.
- **Textual TUI agent**: a rich Terminal User Interface (`TextualAgent`) with real-time display of agent thinking, step navigation, and live cost tracking.
- **HPC / Slurm integration**: built-in tooling for submitting and monitoring Slurm jobs, Globus data transfers, and IRI compute resources.
- **Extensible tool system**: common and domain-specific tools are registered under `src/hepagent/tools/`, making it straightforward to add new capabilities without touching agent logic.

## Installation

### Quick start (no code checkout required)

With [uv](https://docs.astral.sh/uv/) installed, you can run `hepagent` directly from PyPI without cloning the repository:

```bash
# Run once without installing permanently
uvx hepagent -h

# Or install as a persistent tool
uv tool install hepagent
hepagent -h
```

### Developer setup (from source)

```bash
git clone https://github.com/xju2/hepagent.git
cd hepagent
uv python install 3.12
make sync
source .venv/bin/activate
export OPENAI_AGENTS_DISABLE_TRACING=1  # Optional: disable tracing logs if you don't have OPENAI_API_KEY
hepagent list-platforms
hepagent list-models --platform cborg
```

### Configurations
After the installation, you can find default configurations at `$HOME/.hepagent`.
The environment variables are stored in `$HOME/.hepagent/config/env_vars.toml`.

To use a LLM provider, set the corresponding API keys as environment variables.
You may also want to set `OPENAI_AGENTS_DISABLE_TRACING=1` to disable the tracing logs,
especially if you do not have an OPENAI_API_KEY.

If you don't want to store API keys in the TOML file,
you can set them to environment variables directly.
```bash
export CBORG_API_KEY="your-api-key"
export OPENAI_API_KEY="your-api-key"
export AMSC_API_KEY="your-api-key"
export GEMINI_API_KEY="your-api-key"
```


## Instructions

### Choose a platform and model
You can run the agent as in the following examples.
The default model is `cborg:lbl/gemma-4` if not specified.

List available models for a platform:
```bash
hepagent list-models --platform cborg
hepagent list-models --platform amsc
hepagent list-models --platform openai
hepagent list-models --platform gemini
```


### Run an agent with a specific model and task:

```bash
hepagent run --agent "shell" --model "gemini:models/gemini-flash-lite-latest" "how many python files in this code repository"
hepagent run --agent "scientist" --model "cborg:lbl/gemma-4" "I would like to simulate a cosmology sky with Nyx code." --max-turn 30
hepagent run --agent "coder" --model "openai:gpt-5-mini" "Create a worktree for adding a new feature: chunkle."
hepagent run --agent "scientist" --model "gemini:models/gemini-2.0-flash" "..."  # uses Gemini provider
```

YOLO mode (auto-approve all bash commands):
```bash
hepagent run --agent "scientist" --yolo "your task here"
```

### Interactive REPL

Start the new CLI REPL:

```bash
hepagent repl
```

Examples:

```bash
hepagent repl --agent scientist
hepagent repl --agent shell --model openai:gpt-5-mini
hepagent repl --chat my-session
hepagent repl --disable-session
hepagent repl --yolo
hepagent repl --max-turn 30
```

Supported slash commands:

- `/help`
- `/quit`
- `/clear`
- `/agents`
- `/agent <name>`
- `/platforms`
- `/platform <name>`
- `/models [platform]`
- `/model <name>`
- `/mode <confirm|yolo|human>`

For more detail, see [docs/REPL.md](docs/REPL.md).
