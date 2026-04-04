# Task: Claude Code-like REPL
Create a REPL (Read-Eval-Print Loop) interface that mimics the style of Claude Code.
The leaked Claude Code can be found here: claude-code/

It can be invoked by running `uv run hepagent run --repl` in the terminal.

The REPL should allow users to ask questions, view LLM responses, show code changes, execute code snippets
with user's approval as well as automatically execute code snippets when appropriate,
and display the results in a clear and concise manner.
The interface should be user-friendly and visually appealing, with features such as syntax highlighting,
error messages, and support for multiple programming languages.

## Basic Features to Implement:
1. **Input Area**: A text input area where users can type their questions or code snippets.
2. **Response Display**: A section to display the LLM's responses, including any code changes or suggestions.
3. **Syntax Highlighting**: Implement syntax highlighting for code snippets in the response display area

## Advanced Features to Consider:
1. **Slash Commands**: Allow users to execute specific commands. See later sections for examples.


#### Example Slash Commands:
- `/quit`: Exit the REPL interface.
- `/help`: Display a list of available commands and their descriptions.
- `/clear`: Clear the conversation history and reset the REPL interface.
- `/agents`: List all available agents
- `/agent [agent_name]`: Switch to a specific agent for handling the conversation.


## Progress Tracking

# Claude Code-Inspired `hepagent repl`

## Summary
Replace the previous `run --repl` direction with a dedicated `hepagent repl` command. Implement it as a new prompt-toolkit based terminal REPL, separate from the existing Textual UI, and use Claude Code mainly as a model for interaction flow: dedicated launcher, slash-command system, approval prompts, readable streaming transcript, and coding-focused terminal ergonomics.

## Key Changes
- CLI surface:
  - Add a new `@app.command("repl")` in [main.py](/Users/xju/code/hepagent/src/hepagent/main.py).
  - Keep `hepagent run TASK_PROMPT` as the one-shot path.
  - Share existing agent/model/chat/yolo/max-turn option parsing between `run` and `repl` via a small helper so agent selection stays consistent.

- New REPL runtime:
  - Add a new module such as `src/hepagent/agents/cli_repl.py` as the primary implementation.
  - Use `prompt_toolkit` for input editing, history, key handling, and completions.
  - Use Rich rendering for transcript output, markdown/code blocks, syntax highlighting, status lines, and tool-result formatting.
  - Do not reuse the current `TextualAgent` or its UI wrappers.

- Session model:
  - Maintain one in-memory REPL session with:
    - current agent instance
    - current `AgentContext`
    - accumulated conversation input/history
    - current approval mode (`confirm`, `yolo`, `human`)
    - optional persistent SQLite chat session when `--chat` is provided
  - Support multiple prompts in the same REPL without resetting state unless `/clear` is used.
  - Stream assistant output live and append finalized turns to the session transcript.

- Slash-command architecture:
  - Add a small command registry/parser module for v1 slash commands:
    - `/quit`
    - `/help`
    - `/clear`
    - `/agents`
    - `/agent <name>`
    - `/mode <confirm|yolo|human>`
  - Commands should execute locally before any model call.
  - Unknown slash commands should print a structured inline error plus `/help` hint.
  - `/clear` resets the live transcript and in-memory turn history for the REPL session; if `--chat` is active, start a new conversation id rather than silently deleting persisted history.

- Tool approval and user prompts:
  - Add REPL-specific tool wrappers analogous to the current Textual wrappers, but bound to the new REPL I/O layer.
  - Bash tool calls should:
    - show the proposed command and cwd clearly
    - honor `yolo`, `confirm`, and `human` modes
    - prompt inline for approval/rejection in confirm/human modes
    - print bounded/truncated tool output in a dedicated style block
  - `ask_user_for_info` should pause the run and collect a typed answer through the same REPL input mechanism.

- Claude Code-inspired presentation:
  - Prefer a scroll-forward transcript with clear event types over full-screen panes.
  - Render assistant prose as markdown and fenced blocks with syntax highlighting.
  - Give tool calls, approvals, errors, and final summaries distinct visual treatments.
  - Add concise startup/help text that explains slash commands, mode switching, and how command execution approval works.

## Public Interfaces / Behavior
- New command:
  - `uv run hepagent repl`
- Supported options on `repl` should match `run` where practical:
  - `--agent`
  - `--model`
  - `--chat`
  - `--yolo`
  - `--max-turn`
- `hepagent run TASK_PROMPT` remains supported and unchanged in purpose.
- V1 slash commands:
  - `/quit`
  - `/help`
  - `/clear`
  - `/agents`
  - `/agent <agent_name>`
  - `/mode <confirm|yolo|human>`

## Test Plan
- CLI tests:
  - `hepagent repl` starts the REPL entrypoint.
  - `hepagent run hello` still uses the one-shot path.
  - `repl` passes agent/model/chat/max-turn options through correctly.
  - `--yolo` initializes REPL mode state correctly.

- REPL unit tests:
  - Slash-command parser dispatches known commands and rejects unknown ones cleanly.
  - `/agent <name>` switches the active agent for later turns.
  - `/clear` resets live transcript/session state and rolls chat persistence forward safely.
  - `/mode` changes approval behavior without restarting the REPL.

- Tool wrapper tests:
  - confirm mode prompts before command execution
  - yolo mode auto-approves
  - human mode blocks automatic execution until explicit approval
  - `ask_user_for_info` collects and returns typed input
  - long tool output is truncated consistently

- Rendering/behavior smoke tests:
  - streamed assistant text appears incrementally
  - fenced code blocks render with syntax highlighting
  - a multi-turn session preserves context across prompts
  - errors from agent/tool execution surface in the transcript without crashing the REPL

## Assumptions
- We will add `prompt_toolkit` as a new direct dependency.
- The new REPL will borrow Claude Code’s interaction structure, not attempt visual or architectural parity with its React/Ink codebase.
- The existing Textual app can remain in the repo for now, but it is not the target implementation for this task.
- Rich can be used for output rendering in the new REPL runtime.

