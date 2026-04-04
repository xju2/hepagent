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

## Rendering and Output

The REPL uses:

- `prompt_toolkit` for input editing, history, and completions
- Rich for formatted transcript output, markdown rendering, panels, and tables

Assistant responses stream live. Tool calls, tool results, prompts for extra user input,
and errors are rendered in distinct blocks to keep the transcript readable.

## Files Added or Updated

- Added:
  - [src/hepagent/agents/cli_repl.py](/Users/xju/code/hepagent/src/hepagent/agents/cli_repl.py)
  - [docs/REPL.md](/Users/xju/code/hepagent/docs/REPL.md)
- Updated:
  - [src/hepagent/main.py](/Users/xju/code/hepagent/src/hepagent/main.py)
  - [pyproject.toml](/Users/xju/code/hepagent/pyproject.toml)
  - [uv.lock](/Users/xju/code/hepagent/uv.lock)
  - [tests/test_main_chat.py](/Users/xju/code/hepagent/tests/test_main_chat.py)
  - [tests/test_cli_repl.py](/Users/xju/code/hepagent/tests/test_cli_repl.py)

## Validation Summary

The implementation was validated with:

```bash
uv run ruff check src/hepagent/main.py src/hepagent/agents/cli_repl.py tests/test_main_chat.py tests/test_cli_repl.py
uv run pytest -q tests/test_main_chat.py tests/test_cli_repl.py tests/test_cli_entrypoint.py tests/test_main_commands.py tests/test_repl.py tests/test_repl_extra.py tests/test_textual_bash.py tests/test_textual_common.py tests/test_textual_agent.py
```

Both checks passed at the time of implementation.
