# Project
![coverage](https://img.shields.io/badge/coverage-59%25-yellow)

## Introduction

`hepagent` is an AI agent framework tailored for High Energy Physics (HEP) and cosmology workflows. Key features include:

- **Multi-provider LLM support**: seamlessly switch between providers such as `cborg`, `openai`, `amsc`, and `gemini` via a unified CLI (`hepagent run`, `hepagent repl`) or programmatic API, with per-provider configuration managed in `providers.toml`.
- **Skill-based domain knowledge**: a modular skill registry (`.agents/skills/`) packages domain-specific instructions (e.g. running Nyx cosmology simulations, accessing CERN Open Data) that agents load on demand, keeping prompts concise and context-relevant.
- **First-class CLI REPL**: a Claude Code-inspired interactive REPL (`hepagent repl`) for day-to-day exploration, debugging, task progress, slash commands, streaming transcript output, command approval prompts, and markdown/code rendering.
- **Headless task runner**: an automated one-shot interface (`hepagent run "task"`) for terminal-bench style evaluation and scripted tasks. It prints the final result to stdout.
- **Web UI**: a browser chat interface (`hepagent web`, optional `web` extra) with token streaming, collapsible tool steps, and click-to-approve command execution, sharing sessions with the terminal frontends.
- **Bash and execution modes**: interactive shell-capable agents with configurable YOLO (auto-approve), CONFIRM, and HUMAN execution modes, output character limits, and turn budgets—safe for running on HPC clusters.
- **HPC / Slurm integration**: built-in tooling for submitting and monitoring Slurm jobs, Globus data transfers, and IRI compute resources.
- **Extensible tool system**: common and domain-specific tools are registered under `src/hepagent/tools/`, making it straightforward to add new capabilities without touching agent logic.
- **Autonomous analysis pipeline** *(in development)*: a multi-agent orchestration system (`hepagent jfc`) that drives a HEP physics analysis from a natural-language prompt to an analysis note. The analysis structure is an authored document — an architect agent proposes it, you edit it in a browser and approve it before any agent starts work — with the Strategy → Exploration → Processing → Inference → Documentation layout as the built-in default. Each node is executed by a dedicated executor agent, then evaluated by a panel of parallel reviewer agents (physics, critical, constructive) whose findings are adjudicated by an arbiter before the pipeline advances. Optional physicist co-design gates allow human-in-the-loop review. Every analysis keeps an append-only provenance graph recording what produced what. Artifacts (STRATEGY.md, EXPLORATION.md, analysis note PDF, etc.) are written to a structured directory and reproduced via `pixi run all`.

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


### Run an automated task with a specific model:

```bash
hepagent run --agent "shell" --model "gemini:models/gemini-flash-lite-latest" "how many python files in this code repository"
hepagent run --agent "scientist" --model "cborg:lbl/gemma-4" "I would like to simulate a cosmology sky with Nyx code." --max-turn 30
hepagent run --agent "explorer" --model "openai:gpt-5-mini" "Suggest research directions connecting weak lensing and neutrino mass"
hepagent run --agent "coder" --model "openai:gpt-5-mini" "Create a worktree for adding a new feature: chunkle."
hepagent run --agent "scientist" --model "gemini:models/gemini-2.0-flash" "..."  # uses Gemini provider
```

YOLO mode (auto-approve all bash commands):
```bash
hepagent run --agent "scientist" --yolo "your task here"
```

Fully automated mode for terminal-bench style evaluation:
```bash
hepagent run --agent "scientist" --non-interactive "your task here"
```

`hepagent run` is the non-interactive task interface: it accepts one task prompt, executes
agent tool calls or single emitted bash blocks, and prints the final answer. By default it
asks before bash execution and when an agent calls `ask_user_for_info`; `--non-interactive`
auto-approves bash and returns empty input for `ask_user_for_info`. `--max-turn` limits
agent turns per model run; `--max-command-proposals` separately limits emitted bash-block
continuations.

### Interactive REPL

Start the CLI REPL for day-to-day work:

```bash
hepagent repl
```

Examples:

```bash
hepagent repl --agent scientist
hepagent repl --agent explorer
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
- `/max-turn <turns>`

For more detail, see [docs/REPL.md](docs/REPL.md).

### Web UI

A browser-based chat UI with token streaming, collapsible tool steps, and
click-to-approve command execution. It needs the optional `web` extra:

```bash
uv sync --all-extras          # or: uv tool install 'hepagent[web]'
hepagent web
```

Examples:

```bash
hepagent web --agent scientist
hepagent web --model openai:gpt-5-mini
hepagent web --chat my-session      # resume a previous conversation
hepagent web --port 8080 --headless
```

It supports the same slash commands as the REPL (plus `/status`), and the same
settings are available from the ⚙ panel next to the chat input. Sessions are
shared with the terminal frontends, so a chat started in the browser can be
continued with `hepagent repl --chat <session-id>`.

Bind it to localhost only — the agent's bash tool runs commands as the server
user, and there is no authentication.

For more detail, see [docs/WEB.md](docs/WEB.md).

### Autonomous HEP analysis pipeline (`hepagent jfc`)

`hepagent jfc` drives a full HEP physics analysis from a natural-language prompt to a compiled analysis-note PDF. Each node of the analysis is executed by a dedicated executor agent and then evaluated by a panel of parallel reviewer agents whose findings are adjudicated by an arbiter before the pipeline advances.

**The structure is yours to author.** An analysis is described by a `plan.json` — a set of work nodes and the dependencies between them — that you can inspect, edit in a browser, and approve before any agent starts work. The seven-node layout below is the built-in default template, not a fixed pipeline: fan the selection out per channel, insert a calibration sub-analysis, or drop the partial-unblinding node when there is nothing to partially unblind.

| Node | Name | Description |
|------|------|-------------|
| `strategy` | Strategy | Define the analysis strategy and commit to key decisions |
| `exploration` | Exploration | Explore datasets, signal/background properties |
| `selection` | Processing | Run selection, reconstruction, and histogram production |
| `inference_expected` | Expected Results | Inference on expected (Asimov) data |
| `inference_partial` | 10% Validation | Inference on 10% of observed data (human gate) |
| `inference_observed` | Full Data | Inference on the full observed dataset |
| `documentation` | Documentation | Write and typeset the final analysis note PDF |

`hepagent jfc templates` lists the built-in templates. See [docs/PLAN.md](docs/PLAN.md) for the plan schema and [docs/GRAPH.md](docs/GRAPH.md) for the provenance graph each analysis keeps alongside it.

#### Start a new analysis

Create a markdown file with your physics question, then run:

```bash
hepagent jfc run \
  --name my_analysis \
  --type measurement \
  --prompt-file prompt.md
```

Options:

```
--name / -n          Analysis name (short identifier, used as directory name)
--type / -t          Analysis type: measurement or search
--prompt-file / -p   Path to a markdown file with the physics question
--model              Model as "provider:model" (e.g. "cborg:claude-sonnet-4-5")
--base-dir           Parent directory for analyses (default: analyses/)
--max-iterations     Max review iterations per node before halting (default: 3)
--max-turns          Max agent turns per call (defaults: executor=50, note_writer/fixer=30, reviewers=20)
--yolo               Auto-approve all bash commands
--codesign           Enable every co-design gate the plan declares: generates a strategy summary,
                     facilitates interactive Q&A, then re-adjudicates before continuing
--template           Plan template to start from (default: jfc-measurement)
--plan               Path to an authored plan.json to run instead of a template
--review-plan        Open the plan editor and wait for approval before any agent work begins
--plan-port          Port for the plan editor (default: 8001)
```

Example with a specific model and co-design enabled:

```bash
hepagent jfc run \
  --name atlas_zprime \
  --type search \
  --prompt-file tasks/zprime_search.md \
  --model cborg:claude-sonnet-4-5 \
  --codesign
```

#### Author and review the plan

```bash
hepagent jfc plan propose  --name my_analysis --prompt-file prompt.md   # architect drafts it
hepagent jfc plan show     --name my_analysis --format mermaid
hepagent jfc plan validate --name my_analysis                           # exits 1 if it cannot run
hepagent jfc plan edit     --name my_analysis                           # browser editor
hepagent jfc plan migrate  --name my_analysis                           # pre-plan analysis -> plan.json
```

The editor draws the plan, lets you drag nodes, rewrite any node's prompt, retype edges and add or delete either, and refuses to approve a plan that cannot execute. Approving releases a run that is waiting on it.

#### Resume an interrupted analysis

State is saved automatically after every node. Resume from any node:

```bash
hepagent jfc resume --name my_analysis --from-node selection
hepagent jfc resume --name my_analysis --from-node inference_expected
```

With no `--from-node`, it restarts from the most recent *consistent* checkpoint — a node whose artifact exists and which nothing later invalidated.

#### Check analysis status

```bash
hepagent jfc status --name my_analysis
```

Output lists each node with its status (`✓ PASS`, `→ IN PROGRESS`, or `○ pending`) and the number of review iterations used.

#### List all analyses

```bash
hepagent jfc list
hepagent jfc list --base-dir /path/to/analyses
```

#### References
This repository takes inspiration from and builds upon the following works:
- JFC, https://github.com/jfc-mit/jfc
- ShellGPT, https://github.com/ther1d/shell_gpt

Other related works:
* HEPTAPOD: https://github.com/tonymenzo/heptapod
* Just Furnish Context: https://github.com/jfc-mit/slop-X/tree/main
* Deer Flow: https://github.com/bytedance/deer-flow
* Archi: Agentic Operations at the CMS Experiment, [paper](https://arxiv.org/pdf/2606.04755), [code](https://github.com/archi-physics/archi)
* OpenClaw: https://github.com/openclaw/openclaw
* Oh My Agent: https://github.com/first-fluke/oh-my-agent
* Nemo Claw: https://docs.nvidia.com/nemoclaw/latest/get-started/quickstart.html
* Get Physics Done: https://github.com/psi-oss/get-physics-done
* US ATLAS marketplace: https://github.com/usatlas/marketplace/tree/main
