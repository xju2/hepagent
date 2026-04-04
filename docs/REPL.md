# CLI REPL

This document summarizes the new Claude Code-inspired REPL added to `hepagent`.

## What Changed

- Added a dedicated interactive command:
  - `uv run hepagent repl`
- Kept the existing one-shot command:
  - `uv run hepagent run "your task"`
- Implemented the REPL as a new prompt-toolkit based terminal loop in
  [src/hepagent/agents/cli_repl.py](/Users/xju/code/hepagent/src/hepagent/agents/cli_repl.py).
- Reused the existing agent/model/session bootstrap logic in
  [src/hepagent/main.py](/Users/xju/code/hepagent/src/hepagent/main.py) so `run` and `repl`
  resolve agents, models, and chat sessions consistently.

## Design Direction

The new REPL does **not** extend the existing Textual UI. Instead, it follows a simpler
scroll-forward terminal interaction model inspired by Claude Code:

- inline prompt loop instead of a full-screen step browser
- slash commands handled locally before model execution
- readable streaming transcript for assistant output
- explicit command approval flow for shell execution
- markdown and fenced-code rendering for assistant responses

The old Textual flow still exists for `hepagent run`, but it is no longer the target
implementation for interactive REPL work.

## Usage

Start the REPL:

```bash
uv run hepagent repl
```

Common options mirror `hepagent run`:

```bash
uv run hepagent repl --agent scientist
uv run hepagent repl --agent shell --model openai:gpt-5-mini
uv run hepagent repl --chat my-session
uv run hepagent repl --yolo
uv run hepagent repl --max-turn 30
```

## Supported Slash Commands

- `/help`: show REPL help
- `/quit`: exit the REPL
- `/clear`: reset the current in-memory REPL transcript
- `/agents`: list available agents
- `/agent <name>`: switch the active agent for future turns
- `/platforms`: list supported model platforms/providers
- `/platform <name>`: switch the active platform and reset the model to that platform's default
- `/models [platform]`: list models for the current platform, or for an explicitly selected one
- `/model <name>`: switch to a specific model on the current platform
- `/mode <confirm|yolo|human>`: switch shell approval behavior

Unknown slash commands are handled locally and rendered as an inline error with a `/help`
hint instead of being sent to the model.

## Approval Modes

The REPL supports the same execution modes already used elsewhere in the project:

- `confirm`: press Enter to approve a proposed command, or type a reason to reject it
- `yolo`: auto-approve command execution
- `human`: type `y` to approve each command explicitly

These modes apply to bash tool usage wrapped by the REPL runtime.

## Session Behavior

- The REPL keeps one live in-memory conversation across multiple prompts.
- If `--chat` is provided, turns also use a persistent SQLite-backed session.
- `/clear` resets the live transcript and starts a fresh chat session id when persistent
  chat is enabled.
- `/agent` changes the active agent for subsequent prompts without requiring a restart.

## Model and Platform Visibility

- The startup panel shows the active agent, approval mode, platform, and model.
- The bottom toolbar keeps the current `agent`, `platform`, `model`, `mode`, and running
  token cost visible while you work.
- The streaming status panel shown for each turn also includes the current platform and
  model, so it is easier to confirm which provider configuration is active.
- `/platforms` highlights the active platform in the rendered table.
- `/platform <name>` immediately rebuilds the active agent against that provider.
- `/models` highlights the active model when the requested platform matches the current
  REPL platform.
- `/model <name>` validates the selection against the available models for the current
  platform before switching.

If the REPL cannot load models for a platform, it renders the provider/configuration error
locally instead of forwarding that failure to the model.

## Rendering and Output

The REPL uses:

- `prompt_toolkit` for input editing, history, and completions
- Rich for formatted transcript output, markdown rendering, panels, and tables

Assistant responses stream live. Tool calls, tool results, prompts for extra user input,
and errors are rendered in distinct blocks to keep the transcript readable.

## Related Files

- [src/hepagent/agents/cli_repl.py](/Users/xju/code/hepagent/src/hepagent/agents/cli_repl.py):
  REPL loop, slash-command handling, status panels, and toolbar rendering
- [src/hepagent/main.py](/Users/xju/code/hepagent/src/hepagent/main.py):
  REPL bootstrap and shared runtime wiring
- [src/hepagent/model_providers.py](/Users/xju/code/hepagent/src/hepagent/model_providers.py):
  shared provider/model discovery helpers
- [tests/test_cli_repl.py](/Users/xju/code/hepagent/tests/test_cli_repl.py):
  REPL command and rendering coverage
- [tests/test_main_commands.py](/Users/xju/code/hepagent/tests/test_main_commands.py):
  provider/model listing command coverage

## Validation Summary

The current REPL command and provider/model listing behavior was validated with:

```bash
uv run pytest tests/test_cli_repl.py tests/test_main_commands.py
uv run python -m compileall src/hepagent/agents/cli_repl.py src/hepagent/model_providers.py src/hepagent/main.py
```

Both checks passed.
