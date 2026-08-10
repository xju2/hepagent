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
uv run hepagent web --base-dir analyses     # where the plan editor looks
```

The plan editor is served from the same port at `/plan/<analysis>`, and `/plan`
in the chat lists the analyses and links to them. To open it without starting a
chat server:

```bash
uv run hepagent jfc plan edit --name <analysis>            # serves until Ctrl-C
uv run hepagent jfc run --name X ... --review-plan         # edit, approve, run
```

`--review-plan` scaffolds the analysis, opens the editor, and holds the run at
the first node until you approve the plan.

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
| [`web/server.py`](../src/hepagent/web/server.py) | `hepagent web` launcher, plan-editor launcher |
| [`web/plan_api.py`](../src/hepagent/web/plan_api.py) | FastAPI routes for the plan editor |
| [`web/static/plan.html`](../src/hepagent/web/static/plan.html) | The plan editor page (one file, no external requests) |

The agent itself is built with the same `build_runtime()` / `create_app_agent()`
helpers in [`main.py`](../src/hepagent/main.py) that `run` and `repl` use.

## The plan editor

`hepagent web` also serves the analysis plan editor at `/plan/{name}`, and
`hepagent jfc plan edit --name X` serves the same router on a bare uvicorn app —
no Chainlit, which is much the heavier half of the `web` extra.

What the editor edits — the plan schema, the validation rules it enforces, and
the approval handshake it drives — is documented in [PLAN.md](PLAN.md). This
section covers only how it is served.

Every decision lives in [`plan/service.py`](../src/hepagent/plan/service.py),
which is stdlib-only and fully tested in CI. `plan_api.py` only translates to
HTTP. The page is a single self-contained file: inline CSS and JS, SVG
rendering, no external requests at all — the same discipline that keeps it
auditable keeps it working offline on a cluster login node.

Approving a plan in the browser releases `PlanApprovalGate`, which
`run_jfc_analysis(require_approval=True)` awaits before the first node. That only
works because both share one process and one event loop, which is what
`jfc run --review-plan` arranges via `server.plan_editor_running`.

## Invariants — do not break these

1. **Only `app.py` may import Chainlit at module scope, and only `plan_api.py`
   may import FastAPI.** CI runs `uv sync --group dev` without extras; a stray
   top-level import breaks the whole test suite. `session.py` and `server.py` are
   on paths that run without the extra, so they import `uvicorn`, `fastapi` and
   `chainlit` inside functions only.

   **The boundary is transitive, and that is the part that actually broke.**
   Importing a *module that imports FastAPI* is the same mistake as importing
   FastAPI — `session.py` reached `analyses_dir` through `plan_api`, which reads
   as harmless and failed CI just the same. Import shared helpers from
   `plan/service.py`, which is stdlib-only, and never re-export a stdlib helper
   from `plan_api` (a re-export makes the module look like a safe import, which
   is how this happened).

   Reading import statements cannot tell a safe function-local import from an
   unsafe one, so the real enforcement is the `without_web_extras` fixture in
   `tests/conftest.py`: it makes the extras unimportable and runs the code.
   `test_only_plan_api_imports_fastapi` still catches the direct form cheaply.

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

8. **Plan routes must be *inserted at the front* of `app.router.routes`.**
   Chainlit registers a SPA catch-all (`/{full_path:path}`) at import time and
   Starlette matches in order, so a router added with `include_router` is never
   reached for a GET — `/api/plan/zbb` silently returns the SPA instead of JSON.
   `plan_api.mount` splices the routes in ahead of it. Both halves are pinned by
   `tests/web/test_plan_api.py` — one test asserts the mount works, and its
   neighbour asserts that `include_router` would *not* have, so a refactor back
   to the obvious call fails loudly.

9. **The analysis name in a plan URL must be resolved, not concatenated.**
   `PUT /api/plan/{name}` writes files, so `../` in the name would reach any plan
   the server user can touch. `service.resolve_analysis` refuses anything that
   escapes the base directory; the route only turns that into a 400.

## Known limitations

- No chat history browser. Chainlit's thread history needs a data layer; today
  you resume by passing `--chat <id>`.
- Single user, no authentication. Bind to `127.0.0.1` (the default). The bash
  tool runs commands as the server user, and the plan editor writes `plan.json`
  under the analyses directory, so do not expose this port.
- `hepagent web --yolo` sets the *initial* mode only; it is per-chat state
  afterwards, changed via `/mode` or the ⚙ panel.

## Testing

Unit tests (no Chainlit required, run in CI):

```bash
uv run pytest tests/test_web_tools.py tests/test_web_session.py \
              tests/test_web_turn.py tests/test_web_server.py
```

Plan editor (needs the `web` extra for FastAPI; skipped otherwise):

```bash
uv run --extra web pytest tests/web/
```

`tests/web/test_plan_editor_js.py` runs the page's own JavaScript in Node against
a stub DOM (`plan_editor_harness.mjs`), so drag, add-node, connect and save are
exercised rather than assumed. It skips when Node is unavailable.

Manual end-to-end check:

```bash
uv run hepagent web --model ollama:<local-model>
# ask it to run a shell command; confirm the Approve/Reject buttons appear,
# that approving runs the command, and that rejecting sends your reason back.

uv run hepagent jfc plan edit --name <analysis>
# drag a node, retype an edge, save; confirm plan.json and plan.history/ on disk.
# Then `hepagent web` and confirm both / (chat) and /plan/<analysis> serve.
```
