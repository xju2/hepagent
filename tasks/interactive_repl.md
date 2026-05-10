# Task: interactive REPL
The task is to address the following issue. The "scientist" agent can use the funciton `ask_user_for_info` to prompt the user for information. However, the question box does not render promptly when running `hepagent repl`. The goal is to ensure that the question box appears properly when the agent needs to ask the user for information, providing a smoother and more responsive interaction.

## Progress report

**Status: Complete**

### Root cause

Two tools in `ReplToolWrapper` (`ask_user_for_info` and `execute_bash_command_with_repl_confirmation`) were defined as synchronous `@function_tool` functions. The openai-agents SDK runs synchronous tools via `asyncio.to_thread()`, meaning they execute in a worker thread while the main event loop continues running. Calling `PromptSession.prompt()` from a worker thread is unreliable — in Python 3.12+ it raises a `RuntimeError` because there is no current event loop in the thread, causing the prompt to silently fail.

A secondary issue: the streaming loop writes text deltas directly to `console.file` (bypassing Rich's state tracking), leaving the cursor mid-line. When the tool then tried to print a Rich Panel, the panel would appear on the same line as the trailing streaming text.

### Changes made (`src/hepagent/agents/cli_repl.py`)

- **`ReplToolWrapper._create_ask_user_tool`**: changed the inner function to `async def` and replaced `repl.prompt_inline()` with `await repl.prompt_session.prompt_async(...)`. Added `console.file.write("\n")` before panel rendering to ensure a clean line after any streaming text.

- **`ReplToolWrapper._create_bash_tool`**: same treatment — changed to `async def` and added the newline flush before `render_command_proposal`.

- **`CliRepl.approve_command`**: renamed to `approve_command_async` and made async; the two `prompt_inline()` calls inside are now `await prompt_inline_async(...)`.

- **`CliRepl.prompt_inline_async`**: new async helper that wraps `prompt_session.prompt_async(...)` with the same completer/toolbar arguments as the sync `prompt_inline`. The sync `prompt_inline` is kept for the main REPL input loop, which runs outside the asyncio context.

### Tests updated (`tests/test_cli_repl.py`)

- `FakePromptSession` gained a matching `async def prompt_async` method.
- `test_approve_command_modes` and `test_approve_command_yolo_skips_prompt` updated to call `approve_command_async` via `asyncio.run()`.
- `test_bash_tool_rejects_when_not_approved` stub updated to expose `approve_command_async` (async) and a `console.file` attribute.
- All 23 existing cli_repl tests pass.
