# Project
![coverage](https://img.shields.io/badge/coverage-70%25-green)

## Introduction

`hepagent` is an AI agent framework tailored for High Energy Physics (HEP) and cosmology workflows. Key features include:

- **Multi-provider LLM support**: seamlessly switch between providers such as `cborg`, `openai`, `amsc`, and `gemini` via a unified CLI (`hepagent run`) or programmatic API, with per-provider configuration managed in `providers.toml`.
- **Skill-based domain knowledge**: a modular skill registry (`.agents/skills/`) packages domain-specific instructions (e.g. running Nyx cosmology simulations) that agents load on demand, keeping prompts concise and context-relevant.
- **Bash and REPL agents**: interactive shell agents with configurable YOLO (auto-approve), CONFIRM, and HUMAN execution modes, output character limits, and turn budgets—safe for running on HPC clusters.
- **Textual TUI agent**: a rich Terminal User Interface (`TextualAgent`) with real-time display of agent thinking, step navigation, and live cost tracking.
- **HPC / Slurm integration**: built-in tooling for submitting and monitoring Slurm jobs, Globus data transfers, and IRI compute resources.
- **Extensible tool system**: common and domain-specific tools are registered under `src/hepagent/tools/`, making it straightforward to add new capabilities without touching agent logic.

## Installation

```bash
uv python install 3.14 (or higher)
make sync
source .venv/bin/activate
uv pip install -e .
uv run hepagent list-platforms
uv run hepagent list-models --platform cborg
```

### Configurations
after the installation, you can find default configurations at `$HOME/.hepagent`.
The environment variables are stored in `$HOME/.hepagent/config/env_vars.toml`.

To use a LLM provider, set the corresponding API keys as environment variables.
You may also want to set `OPENAI_AGENTS_DISABLE_TRACING=1` to disable the tracing logs,
especically if you do not have a OPENAI_API_KEY.

If you don't want to store API keys in the TOML file,
you can set them to environment variables directly.
```bash
export CBORG_API_KEY="your-api-key"
export OPENAI_API_KEY="your-api-key"
export AMSC_API_KEY="your-api-key"
export GEMINI_API_KEY="your-api-key"
```


## Instructions

### Chose a platform and model
We can run the agent as the following examples.
The default model is `cborg:gemini-flash` if not specified.

List available models for a platform:
```bash
uv run hepagent list-models --platform cborg
uv run hepagent list-models --platform amsc
uv run hepagent list-models --platform openai
uv run hepagent list-models --platform gemini
```


### Run an agent with a specific model and task:

```bash
uv run hepagent run --agent "shell" --model "gemini:models/gemini-flash-lite-latest" "how many python files in this code repository"
uv run hepagent run --agent "scientist" --model "cborg" "I would like to simulate a cosmology sky with Nyx code." --max-turn 30
uv run hepagent run --agent "coder" --model "openai:gpt-5-mini" "Create a worktree for adding a new feature: chunkle."
uv run hepagent run --agent "scientist" --model "gemini:models/gemini-2.0-flash" "..."  # defaults to cborg
```

YOLO mode (auto-approve all bash commands):
```bash
uv run hepagent run --agent "scientist" --yolo "your task here"
```

### Interactive Bash Agent with REPL
```
python src/hepagent/scripts/bash_repl.py
```
With custom turn budget:
```
HEPAGENT_MAX_TURNS=80 python src/hepagent/scripts/bash_repl.py
```
