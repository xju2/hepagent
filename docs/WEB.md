# Web UI

This document describes the browser-based chat UI added to `hepagent`, alongside
the headless runner (`hepagent run`) and the prompt-toolkit REPL
(`hepagent repl`).

## Usage

Install the optional extra, then start the server:

```bash
uv sync --all-extras          # or: uv tool install 'hepagent[web]'
uv run hepagent web
```

It serves on <http://127.0.0.1:8000> and opens a browser. Options mirror
`hepagent repl`:

```bash
uv run hepagent web --agent scientist
uv run hepagent web --model openai:gpt-5-mini
uv run hepagent web --chat my-session      # resume a previous conversation
uv run hepagent web --yolo                 # start in auto-approve mode
uv run hepagent web --port 8080 --headless
```

Every chat gets a `web-<id>` session id, shown in the welcome message. Because
the web UI writes to the same `~/.hepagent/sessions/conversation.db` as the
other frontends, a conversation can be continued in the terminal:

```bash
hepagent repl --chat web-1a2b3c4d5e6f
```

## Feature parity

| Capability | Web UI |
| --- | --- |
| Token-level streaming | yes |
| Tool calls and outputs | collapsible steps |
| Bash approval (`confirm`/`yolo`/`human`) | Approve/Reject buttons, rejection reason sent back to the agent |
| `ask_user_for_info` | inline question in the transcript |
| Agent / platform / model switching | ⚙ settings panel *and* slash commands |
| Skill loading, logbook, user profile | via the normal tool steps |
| Cost and active-skill display | `/status` |
| Recovery behaviours | shared with the REPL (see below) |

Slash commands: `/help`, `/status`, `/clear`, `/agents`, `/agent`, `/platforms`,
`/platform`, `/models`, `/model`, `/mode`, `/max-turn`. They are parsed with the
REPL's `parse_slash_command`, so the two frontends stay in sync.

## Architecture

Only `app.py` imports Chainlit. Everything else is transport-agnostic, which is
why the web logic is unit-tested in CI even though CI does not install the
`web` extra.

| File | Role |
| --- | --- |
| [`web/bridge.py`](../src/hepagent/web/bridge.py) | `WebBridge` (human-in-the-loop) and `TurnUI` (rendering) protocols |
| [`web/tools.py`](../src/hepagent/web/tools.py) | `WebToolWrapper`: swaps terminal-bound tools, offloads blocking ones |
| [`web/turn.py`](../src/hepagent/web/turn.py) | `run_turn`: the streaming loop, ported from the REPL |
| [`web/session.py`](../src/hepagent/web/session.py) | `WebSessionState`: per-chat agent/model/mode/cost/history |
| [`web/app.py`](../src/hepagent/web/app.py) | Chainlit handlers, `ChainlitBridge`, `ChainlitTurnUI` |
| [`web/server.py`](../src/hepagent/web/server.py) | `hepagent web` launcher |

The agent itself is built with the same `build_runtime()` / `create_app_agent()`
helpers in [`main.py`](../src/hepagent/main.py) that `run` and `repl` use.

## Invariants — do not break these

1. **Only `app.py` may import Chainlit at module scope.** CI runs
   `uv sync --group dev` without extras; a stray top-level import breaks the
   whole test suite.

2. **Approval must fail closed.** `WebToolWrapper.approve` catches exceptions
   from the bridge and returns "not approved". If the websocket drops mid-prompt
   the command must not run. Covered by
   `tests/test_web_tools.py::test_approval_failure_does_not_execute_the_command`.

3. **Never call `chainlit.cli.run_chainlit`.** Importing `chainlit.cli` executes
   `nest_asyncio.apply()` at module scope. On Python 3.12+ that makes
   `asyncio.wait_for` raise `RuntimeError: Timeout should be used inside a task`
   instead of `TimeoutError`. python-engineio's ping-timeout service task only
   catches `TimeoutError`, so it dies and restarts in a hot loop — this produced
   a 512 MB log of tracebacks in about four minutes and broke the websocket.
   `server._build_server()` reimplements the handful of setup steps instead.

4. **`CHAINLIT_APP_ROOT` must be set before Chainlit is imported.**
   `chainlit.config` reads it at import time and otherwise defaults to
   `os.getcwd()`, littering the user's project with `.chainlit/`, `.files/` and
   `chainlit.md`. `server.build_environment()` pins it to `~/.hepagent/web`.

5. **Long-blocking tools must be offloaded.** The Agents SDK invokes synchronous
   function tools *inline on the event loop*. A terminal frontend gets away with
   this because it owns a private loop on its own thread; a web server does not.
   `wait_for_slurm_job_completion` alone would freeze the server for the
   lifetime of a job. Add any new sleeping/polling/CPU-heavy tool to
   `LONG_BLOCKING_TOOL_NAMES` in `web/tools.py`.

6. **Approval prompts need a long timeout.** Chainlit's defaults are 60–90 s.
   `ASK_TIMEOUT_SECONDS` raises this to an hour; a timeout is treated as a
   rejection.

7. **Tool names come from the call, not the output.** The SDK's
   `function_call_output` carries only a `call_id`, so `run_turn` keeps a
   `call_id -> name` map. Without it, bash results render twice — once as the
   bridge's `bash (exit N)` step and once as a generic `tool` step.

## Known limitations

- No chat history browser. Chainlit's thread history needs a data layer; today
  you resume by passing `--chat <id>`.
- Single user, no authentication. Bind to `127.0.0.1` (the default). The bash
  tool runs commands as the server user, so do not expose this port.
- `hepagent web --yolo` sets the *initial* mode only; it is per-chat state
  afterwards, changed via `/mode` or the ⚙ panel.

## Testing

Unit tests (no Chainlit required, run in CI):

```bash
uv run pytest tests/test_web_tools.py tests/test_web_session.py \
              tests/test_web_turn.py tests/test_web_server.py
```

Manual end-to-end check:

```bash
uv run hepagent web --model ollama:<local-model>
# ask it to run a shell command; confirm the Approve/Reject buttons appear,
# that approving runs the command, and that rejecting sends your reason back.
```
